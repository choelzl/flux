"""A user's applications (D683): `<data>/users/<name>/apps/<app>/`, each a problem document and
its files (golden model, scripts, knowledge), uploaded as files or a zip, or written from the
crafter. Every path a request names is resolved inside the application and refused outside it."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import stat
import threading
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

MAX_BYTES = 256 * 1024 * 1024           # one request (D700: the page sends a large upload in batches)
MAX_FILES = 900                         # below the 1000 files a request may carry (Starlette)
LOOP_BYTES = 8 * 1024 ** 3              # a loop's own files in all
#: Parts of uploads checked against the room and written one at a time (D855): two uploads at once
#: cannot each claim the same room. In one server process; several would need the store's lock.
_ROOM = threading.Lock()
LOOP_FILES = 100_000
PART_BYTES = 64 * 1024 * 1024           # one part of a file sent in parts
TEXT_MAX = 2 * 1024 * 1024
_NAME = re.compile(r"[A-Za-z0-9_-]{1,60}")
DOCUMENT_FILE = "problem.yaml"            # D786: a loop's document; the loop's name is its id
DOC_SUFFIXES = (".problem.yaml", ".problem.yml", ".task.json", ".task.yaml", ".yaml", ".yml", ".json")


#: What runs write (D696): never one of the loop's own files, never descended into (D907)
RUN_DIRS = ("out", "runs", "workbench")
_ROOMS: dict[str, list[float]] = {}     # D907: a loop's own files and partial uploads, [when, count, bytes]
ROOM_FRESH = 60.0                       # seconds a tally serves the parts of an upload before a new walk


def own_files(root: Path, skip: tuple[str, ...] = ("__pycache__", ".git")) -> list[tuple[str, Path, os.stat_result]]:
    """(relative path, path, lstat) of each file of the loop, sorted as its path's parts (D907): the
    folders runs write (out/, runs/, workbench/) and `skip` folders pruned before descending, so
    a run's 20,000 outputs cost nothing; links to folders not followed. One walk for the digest,
    the input list and the room an upload has."""
    out = []
    for folder, dirs, names in os.walk(root):
        rel_dir = os.path.relpath(folder, root)
        top = rel_dir == "."
        dirs[:] = [d for d in dirs if d not in skip and not (top and d in RUN_DIRS)]
        for n in names:
            p = Path(folder) / n
            try:
                st = p.lstat()
            except OSError:
                continue
            out.append((n if top else f"{rel_dir}/{n}", p, st))
    out.sort(key=lambda x: x[0].split("/"))
    return out


class WorkspaceError(ValueError):
    pass


class Protected(WorkspaceError):
    """A path the Files tab may not rename, move or delete, with why (D908)."""


class Changed(WorkspaceError):
    """An item changed since the page read it (D908): 409, never overwritten silently."""


#: D908: why what runs write is not renamed, moved or deleted in the Files tab
PROTECTED_WHY = {"out": "out/ is the loop's record of its runs: Settings › Maintenance cleans it",
                 "runs": "runs/ is the loop's log, answers and notes: Settings › Maintenance cleans it",
                 "workbench": "workbench/ is the agents' own notes and tools: they keep it"}


def revision(st: os.stat_result) -> str:
    """An item as it is now (D908): when it changed and its size -- a save or a move naming an
    older one is refused, not applied over another tab's or an agent's change."""
    return f"{st.st_mtime_ns:x}.{st.st_size:x}"


def _rename_noreplace(src: Path, dst: Path) -> None:
    """`src` renamed to `dst` only when nothing is at `dst` (D908): renameat2(RENAME_NOREPLACE) as
    one step where the kernel has it; else a check and a rename under one lock (FileExistsError)."""
    import ctypes
    import errno

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fn = libc.renameat2
    except (OSError, AttributeError):
        fn = None
    if fn is not None:
        if fn(-100, os.fsencode(src), -100, os.fsencode(dst), 1) == 0:      # AT_FDCWD, RENAME_NOREPLACE
            return
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            raise FileExistsError(errno.EEXIST, "exists", str(dst))
        if err not in (errno.ENOSYS, errno.EINVAL):
            raise OSError(err, os.strerror(err), str(src))
    with _ROOM:
        if os.path.lexists(dst):
            raise FileExistsError(errno.EEXIST, "exists", str(dst))
        os.rename(src, dst)


class Exists(WorkspaceError):
    """A loop's name already taken (D906): a creation never replaces it -- 409 on the web."""


def check_name(name: str) -> str:
    if not _NAME.fullmatch(name or ""):
        raise WorkspaceError("an application name is letters, digits, - and _ (1 to 60 characters)")
    return name


def safe_rel(path: str) -> str:
    """A relative path inside an application, or WorkspaceError."""
    if "\x00" in path:
        raise WorkspaceError("a path has no NUL")
    p = PurePosixPath(path.replace("\\", "/"))
    if p.is_absolute() or not p.parts or any(part in ("..", "") for part in p.parts):
        raise WorkspaceError(f"{path!r} is not a relative path inside the application")
    return str(p)


class Workspace:
    def __init__(self, data: Path, user: str) -> None:
        self.root = Path(data) / "users" / user / "apps"
        self.root.mkdir(parents=True, exist_ok=True)

    def app(self, name: str) -> Path:
        d = self.root / check_name(name)
        if not d.is_dir():
            raise WorkspaceError(f"no application {name!r}")
        return d

    def apps(self) -> list[dict[str, Any]]:
        out = []
        for d in sorted(self.root.iterdir()):
            if d.is_dir():
                meta = self.meta(d.name)
                out.append({"name": d.name, "document": meta.get("document"), "id": meta.get("id")})
        return out

    def meta(self, name: str) -> dict[str, Any]:
        try:
            return json.loads((self.root / name / ".flux-app.json").read_text())
        except (OSError, ValueError):
            return {}

    def create(self, name: str, files: list[tuple[str, bytes]], replace: bool = False) -> dict[str, Any]:
        """A new application (or its files replaced) from (relative path, content) pairs; a
        single .zip among them is unpacked. Returns its meta: which file is the document."""
        d = self.root / check_name(name)
        if d.exists() and not replace:
            raise Exists(f"application {name!r} exists")
        unzipped = len(files) == 1 and files[0][0].lower().endswith(".zip")
        if unzipped:
            files = _unzip(files[0][1])
        if not files:
            raise WorkspaceError("no files")
        _check_batch(files, unzipped)
        rels = [safe_rel(p) for p, _b in files]
        # a folder upload names every file under the folder: drop the common first directory
        firsts = {PurePosixPath(r).parts[0] for r in rels}
        if len(firsts) == 1 and all(len(PurePosixPath(r).parts) > 1 for r in rels):
            rels = [str(PurePosixPath(*PurePosixPath(r).parts[1:])) for r in rels]
        doc = _pick_document(rels)
        if doc is None:
            raise WorkspaceError("no problem document among the files (problem.yaml)")
        if not doc.endswith((".problem.yaml", ".problem.yml")) and doc != DOCUMENT_FILE:
            rels = [DOCUMENT_FILE if r == doc else r for r in rels]      # D786: a document of another name is the problem.yaml
            doc = DOCUMENT_FILE
        try:
            d.mkdir(parents=True, exist_ok=replace)                   # D906: two creations at once, one wins
        except FileExistsError as exc:
            raise Exists(f"application {name!r} exists") from exc
        for rel, (_p, content) in zip(rels, files):
            _replace(d, rel, content)                                 # D852: never through a link, an import's source kept
        meta = {"document": doc, "id": name}                          # D786: the loop's name is the problem's id
        _replace(d, ".flux-app.json", json.dumps(meta))
        return meta

    def add(self, name: str, files: list[tuple[str, bytes]], sub: str = "") -> list[str]:
        """Files added to (or replacing files in) an existing application, under `sub`; a single
        .zip is unpacked. The same checks as a new one."""
        self.app(name)                                     # it exists
        unzipped = len(files) == 1 and files[0][0].lower().endswith(".zip")
        if unzipped:
            files = _unzip(files[0][1])
        if not files:
            raise WorkspaceError("no files")
        _check_batch(files, unzipped)
        prefix = safe_rel(sub) + "/" if sub.strip("/") else ""
        rels = [safe_rel(prefix + p) for p, _b in files]
        self._check_room(name, sum(len(b) for _p, b in files), len(files), [self.app(name) / r for r in rels])
        written = []
        for rel, (_p, content) in zip(rels, files):
            if rel == ".flux-app.json":
                raise WorkspaceError("that name is the server's")
            self.path(name, rel)                           # inside the application
            _replace(self.app(name), rel, content)         # D700, D852: the file named is replaced, not what it links to
            written.append(rel)
        return written

    def _check_room(self, name: str, more_bytes: int, more_files: int, replacing: Any = (), fresh: bool = True) -> None:
        """A loop's own files stay under LOOP_BYTES and LOOP_FILES in all (D700) -- counting the
        uploads under way (D855: their partial files), and a file being replaced by the difference
        its new content makes, not twice."""
        n, size = self._tally(name, fresh)
        for old in replacing:
            try:
                if old.is_file() and not old.is_symlink():
                    size -= old.lstat().st_size
                    n -= 1
            except OSError:
                pass
        if size + more_bytes > LOOP_BYTES or n + more_files > LOOP_FILES:
            raise WorkspaceError(f"a loop holds at most {LOOP_FILES} files and {LOOP_BYTES // 2**30} GB of its own")

    def _tally(self, name: str, fresh: bool = False) -> tuple[int, int]:
        """(files, bytes) of the loop's own files and its uploads under way (D907): one walk, out/,
        runs/ and workbench/ pruned; kept a minute for the parts of an upload that follow, each
        counted as written -- not the whole tree walked again for every part."""
        import time

        key = str(self.app(name).resolve())
        got = _ROOMS.get(key)
        if got is None or fresh or time.monotonic() - got[0] > ROOM_FRESH:
            n = size = 0
            for _rel, p, st in own_files(Path(key)):
                if p.name == ".flux-app.json":
                    continue
                if not p.name.endswith(".part-upload"):
                    n += 1
                size += st.st_size
            got = _ROOMS[key] = [time.monotonic(), n, size]
        return int(got[1]), int(got[2])

    def _changed(self, name: str) -> None:
        """The loop's files changed other than by a part: its tally walked again next time (D907)."""
        _ROOMS.pop(str(self.app(name).resolve()), None)

    def put_part(self, name: str, rel: str, offset: int, data: bytes, final: bool) -> int:
        """A large file in parts (D700): each part written at its offset into a hidden partial
        file, moved into place with the last. Returns the size so far."""
        rel = safe_rel(rel)
        if rel == ".flux-app.json" or rel.split("/")[0] in ("out", "runs", "workbench"):
            raise WorkspaceError(f"{rel!r} is not one of the loop's own files")
        if len(data) > PART_BYTES:
            raise WorkspaceError(f"a part is at most {PART_BYTES // 2**20} MB")
        target = self.path(name, rel)
        part = target.with_name(f".{target.name}.part-upload")
        with _ROOM:                                   # D855: a part checked and written as one, uploads at once
            if offset == 0:
                part.parent.mkdir(parents=True, exist_ok=True)
                part.unlink(missing_ok=True)
            elif not part.exists() or part.stat().st_size != offset:
                raise WorkspaceError(f"{rel}: the part at {offset} does not follow the parts before")
            if offset + len(data) > LOOP_BYTES:
                raise WorkspaceError(f"a file is at most {LOOP_BYTES // 2**30} GB")
            # D855: every part against the loop's room -- what it holds, the uploads under way, this
            # part -- the file it replaces counted out (its new content takes its place)
            # D907: a file's first part walks the loop once; the parts after it count on that walk
            self._check_room(name, len(data), 1 if offset == 0 and not target.exists() else 0, [target], fresh=offset == 0)
            fd = os.open(part, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o644)   # D852: not through a link
            with os.fdopen(fd, "ab") as fh:
                fh.write(data)
            size = part.stat().st_size
            got = _ROOMS.get(str(self.app(name).resolve()))
            if got is not None:                       # the part counted as written; the rest of the tally holds
                got[2] += len(data)
        if final:
            target.unlink(missing_ok=True)
            part.replace(target)
            self._changed(name)
        return size

    def drop_part(self, name: str, rel: str) -> None:
        """A file sent in parts, given up (D702): its partial file goes, and the folders it leaves empty."""
        target = self.path(name, safe_rel(rel))
        part = target.with_name(f".{target.name}.part-upload")
        part.unlink(missing_ok=True)
        self._changed(name)
        root, d = self.app(name).resolve(), part.parent
        while d != root and d.is_dir() and not any(d.iterdir()):
            d.rmdir()
            d = d.parent

    def import_dir(self, name: str, src: Path, replace: bool = False) -> dict[str, Any]:
        """A folder of this machine as a loop (D700: the admin's applications): its files hard
        linked where the disk allows (copied otherwise) -- a run never writes its inputs, and an
        edit replaces a file rather than writing through the link. Its record, log and workbench
        are the loop's own; with `replace`, the files are taken again and those stay."""
        src = src.resolve()
        d = self.root / check_name(name)
        if d.exists() and not replace:
            raise WorkspaceError(f"application {name!r} exists")
        rels = []
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src)
            if not p.is_file() or rel.parts[0] in ("out", "runs", "workbench", ".git") or "__pycache__" in rel.parts \
                    or p.name == ".flux-app.json" or p.name.endswith(".part-upload"):
                continue
            rels.append(str(rel))
        doc = _pick_document(rels)
        if doc is None:
            raise WorkspaceError(f"no problem document in {src}")
        if len(rels) > LOOP_FILES:
            raise WorkspaceError(f"at most {LOOP_FILES} files")
        d.mkdir(parents=True, exist_ok=True)
        if replace:                                   # the loop's own files go; out/, runs/, workbench/ stay
            for rec in self.inputs(name):
                (d / rec["path"]).unlink(missing_ok=True)
        linked = copied = 0
        for rel in rels:
            target = d / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
            try:
                os.link(src / rel, target)
                linked += 1
            except OSError:
                shutil.copy2(src / rel, target)
                copied += 1
        meta = {**(self.meta(name) if replace else {}), "document": doc, "id": name, "source": str(src)}
        (d / ".flux-app.json").write_text(json.dumps(meta))
        return {**meta, "linked": linked, "copied": copied}

    #: D824: what a clone of a loop leaves behind -- its runs' own: the record and what they decided
    #: (out/), the log, answer, notes and questions (runs/), an agent's work in progress
    CLONE_SKIPS = ("out", "runs", ".author-work", ".attachments", "__pycache__", ".git")

    def clone(self, name: str, src: Path, *, workbench: bool = False, source: str = "") -> dict[str, Any]:
        """A new loop `name` with `src`'s problem (D824): its documents, the files they name, its
        library/, its sub-loops' folders -- never its runs' record, log or answers; its workbench (the
        agents' notes and tools) only when asked. Links stay links (an application's files, D703)."""
        d = self.root / check_name(name)
        if d.exists():
            raise WorkspaceError(f"application {name!r} exists")
        skips = set(self.CLONE_SKIPS) | (set() if workbench else {"workbench"})

        def ignore(folder: str, names: list[str]) -> set[str]:
            top = Path(folder).resolve() == src.resolve()
            return {n for n in names if (top and n in skips) or n in ("__pycache__", ".git") or n.endswith(".orig")}

        shutil.copytree(src, d, symlinks=True, ignore=ignore)
        try:
            old = json.loads((src / ".flux-app.json").read_text())
        except (OSError, ValueError):
            old = {}
        meta = {"document": old.get("document"), "id": name, "cloned_from": source}
        (d / ".flux-app.json").write_text(json.dumps(meta))
        return meta

    def create_empty(self, name: str) -> Path:
        """A loop with no document yet (D704): an agent is about to write it."""
        d = self.root / check_name(name)
        if d.exists():
            raise WorkspaceError(f"application {name!r} exists")
        d.mkdir(parents=True)
        (d / ".flux-app.json").write_text(json.dumps({"document": None, "id": name}))
        return d

    def create_from_text(self, name: str, filename: str, text: str) -> dict[str, Any]:
        """A loop from a document's text: its problem.yaml, or the NAME.problem.yaml it is called (D787).
        A name taken is Exists (D906): never replaced here -- a change of a loop is its edit, with a
        diff. The same request again (a retry after a lost answer) finds its own document and is
        answered as made, `existing` set."""
        doc = PurePosixPath(filename or "").name
        doc = doc if doc.endswith((".problem.yaml", ".problem.yml")) else DOCUMENT_FILE
        if (self.root / check_name(name)).exists():
            meta = self.meta(name)
            try:
                same = meta.get("document") == doc and self.path(name, doc).read_text() == text
            except (OSError, ValueError):
                same = False
            if same:
                return {**meta, "existing": True}
            raise Exists(f"a loop named {name!r} exists: open it, or choose another name")
        return self.create(name, [(doc, text.encode())])

    def documents(self, name: str) -> list[dict[str, Any]]:
        """The loop's problems (D787): its problem.yaml and each NAME.problem.yaml, with whether
        each loads and the record its runs keep."""
        from flux_loop.document import confined, loadable, record_name

        d = self.app(name)
        with confined(d):                                  # D905: the server reads nothing outside the loop
            said = loadable(d)
        return [{"path": p.name, "record": record_name(p), "ok": not err, "error": err[:400]} for p, err in said]

    def delete(self, name: str) -> None:
        shutil.rmtree(self.app(name))

    def path(self, name: str, rel: str) -> Path:
        """`rel` inside the application, resolved (a link pointing out is refused)."""
        d = self.app(name).resolve()
        p = (d / safe_rel(rel)).resolve()
        if p != d and d not in p.parents:
            raise WorkspaceError(f"{rel!r} is outside the application")
        return p

    def entry(self, name: str, rel: str) -> Path:
        """`rel` as a directory entry, for changing it (D905): its folder resolved and inside the
        application, its own name not followed -- a link is the link, never what it points to."""
        rel = safe_rel(rel)
        head, _, leaf = rel.rpartition("/")
        parent = self.path(name, head) if head else self.app(name).resolve()
        if not parent.is_dir():
            raise WorkspaceError(f"no folder {head!r}")
        return parent / leaf

    def files(self, name: str, sub: str = "", show_ignored: bool = False) -> list[dict[str, Any]]:
        """A folder of the loop as the Files tab lists it (D703): what its `.gitignore` files ignore
        left out, or marked with `show_ignored`, and so is a name starting with "." (D836); `.git` never."""
        from .gitignore import Ignores

        if Ignores.hidden(sub):
            raise WorkspaceError("a repository's own folder is not shown")
        base = self.path(name, sub) if sub else self.app(name).resolve()
        if not base.is_dir():
            raise WorkspaceError(f"{sub!r} is not a folder")
        root = self.app(name).resolve()
        ig = Ignores(root)
        out = []
        for p in sorted(base.iterdir(), key=lambda q: (not q.is_dir(), q.name)):
            if p.name in (".flux-app.json", ".git") or p.name.endswith(".part-upload"):
                continue
            rel = str(p.relative_to(root))
            is_dir = p.is_dir() and not p.is_symlink()
            # D836: a name starting with "." -- a file, a folder, or anything in one -- counts as ignored too
            ignored = ig.ignored(rel, is_dir) or any(part.startswith(".") for part in Path(rel).parts)
            if ignored and not show_ignored:
                continue
            st = p.lstat()
            out.append({"path": rel, "dir": is_dir, "link": p.is_symlink(), "size": st.st_size, "mtime": st.st_mtime, "ignored": ignored})
        return out

    def workbench(self, name: str) -> list[dict[str, Any]]:
        """The agents' workbench (D677) as the browser lists it (D688): each file with its first
        line, newest first within tools/ and notes/."""
        root = self.app(name).resolve()
        bench = root / "workbench"
        out = []
        if not bench.is_dir():
            return out
        for p in sorted(bench.rglob("*"), key=lambda q: q.stat().st_mtime, reverse=True):
            rel = p.relative_to(bench)
            if not p.is_file() or p.is_symlink() or p.suffix in (".pyc", ".pyo") or any(x.startswith(".") or x == "__pycache__" for x in rel.parts):
                continue
            first = ""
            try:
                with p.open(errors="replace") as fh:
                    for _ in range(20):
                        ln = fh.readline()
                        if not ln:
                            break
                        ln = ln.strip().lstrip("#").strip().strip('"').strip("'").strip()
                        if ln and not ln.startswith("!"):
                            first = ln[:160]
                            break
            except OSError:
                pass
            st = p.stat()
            out.append({"path": str(p.relative_to(root)), "kind": rel.parts[0] if len(rel.parts) > 1 else "",
                        "first": first, "size": st.st_size, "mtime": st.st_mtime})
        return out

    def set_meta(self, name: str, **fields: Any) -> dict[str, Any]:
        meta = {**self.meta(name), **fields}
        (self.app(name) / ".flux-app.json").write_text(json.dumps(meta))
        return meta

    def inputs_digest(self, name: str) -> str:
        """What a start reads (D693): the document and its files, not what runs write (out/,
        runs/, the workbench) -- a change here is what a check before starting looks for."""
        import hashlib

        root = self.app(name).resolve()
        h = hashlib.sha256()
        for rel, p, st in own_files(root, skip=("__pycache__",)):
            if not stat.S_ISREG(st.st_mode) or p.name == ".flux-app.json":
                continue
            h.update(rel.encode() + b"\0")
            try:
                with open(p, "rb") as fh:                  # D907: in pieces, never the whole file at once
                    while chunk := fh.read(1 << 20):
                        h.update(chunk)
            except OSError:
                pass
            h.update(b"\0")
        return h.hexdigest()[:16]

    def inputs(self, name: str) -> list[dict[str, Any]]:
        """The loop's own files (D696): the document and what it runs -- scripts, golden models,
        specs -- not what its runs write (out/, runs/, the workbench)."""
        from .gitignore import Ignores

        root = self.app(name).resolve()
        doc = self.meta(name).get("document")
        ig = Ignores(root)
        out = []
        for rel, p, st in own_files(root):                  # D907: out/, runs/, workbench/ pruned, not listed then dropped
            if p.name in (".flux-app.json", ".git") or p.name.endswith(".part-upload") or not p.is_file():
                continue
            ignored = ig.ignored(rel)                       # D703: what .gitignore ignores, marked
            out.append({"path": rel, "size": st.st_size, "document": rel == doc, "ignored": ignored})
        return out

    # ---- the Files tab as a file manager (D908): what an item is, its folder's size, rename and
    #      move as one no-clobber step, deletion of a file, a link or a folder said whole
    def protected(self, name: str, rel: str) -> str:
        """Why `rel` (where it is in the loop, links resolved) may not be renamed, moved or deleted
        here; '' when it may (D908)."""
        parts = [x for x in rel.strip("/").split("/") if x]
        if not parts:
            return "the loop's own folder: delete the loop under Settings"
        if parts[0] in RUN_DIRS:
            return PROTECTED_WHY[parts[0]]
        if parts == [".flux-app.json"]:
            return "the server's own record of this loop"
        if parts[0] in (".author-work", ".attachments"):
            return "an agent's work in progress: it goes when the agent is done"
        if ".git" in parts:
            return "a repository's own folder"
        if parts[-1].endswith(".part-upload"):
            return "an upload under way"
        if "/".join(parts) == self.meta(name).get("document"):
            return "the loop's document: the loop runs it under this name"
        return ""

    def _where(self, name: str, rel: str) -> tuple[Path, str]:
        """(the entry, where it is in the loop): its folder resolved, its own name not followed."""
        p = self.entry(name, rel)
        return p, str(p.relative_to(self.app(name).resolve())) if p != self.app(name).resolve() else ""

    def item(self, name: str, rel: str = "") -> dict[str, Any]:
        """What an item is (D908): its kind (file, folder, link -- a link never followed), a file's
        bytes, when it changed, its revision (a change since is a conflict), and why it is
        protected, if it is. A folder's size is `folder_size`, asked for, not computed here."""
        root = self.app(name).resolve()
        p, where = self._where(name, rel) if rel.strip("/") else (root, "")
        try:
            st = os.lstat(p)
        except OSError as exc:
            raise WorkspaceError(f"no file or folder {rel!r}") from exc
        kind = "link" if stat.S_ISLNK(st.st_mode) else "folder" if stat.S_ISDIR(st.st_mode) else "file"
        out = {"path": where, "name": p.name if where else name, "kind": kind, "size": st.st_size if kind == "file" else None,
               "mtime": st.st_mtime, "revision": revision(st), "protected": self.protected(name, where)}
        if kind == "link":
            out["target"] = os.readlink(p)
        return out

    def folder_size(self, name: str, rel: str = "", limit: int = 200_000, seconds: float = 3.0) -> dict[str, Any]:
        """A folder's contents in all (D908): bytes of its files, its files, folders and links -- the
        hidden and ignored ones too; a link counted, never followed. At most `limit` entries or
        `seconds`: past that, `partial`, what was counted so far."""
        import time

        root = self.app(name).resolve()
        top = self._where(name, rel)[0] if rel.strip("/") else root
        if top.is_symlink() or not top.is_dir():
            raise WorkspaceError(f"{rel!r} is not a folder")
        got = {"bytes": 0, "files": 0, "folders": 0, "links": 0, "partial": False}
        stack, seen, t0 = [top], 0, time.monotonic()
        while stack:
            try:
                with os.scandir(stack.pop()) as it:
                    for e in it:
                        seen += 1
                        if seen > limit or time.monotonic() - t0 > seconds:
                            got["partial"] = True
                            return got
                        if e.is_symlink():
                            got["links"] += 1
                        elif e.is_dir(follow_symlinks=False):
                            got["folders"] += 1
                            stack.append(Path(e.path))
                        else:
                            got["files"] += 1
                            got["bytes"] += e.stat(follow_symlinks=False).st_size
            except OSError:
                continue
        return got

    def move(self, name: str, src: str, dst: str, rev: str | None = None) -> dict[str, Any]:
        """`src` renamed or moved to `dst` in the same loop, a folder with everything in it (D908):
        never onto an item already there (no-clobber, Exists), never a folder into itself, never
        a protected item or into a protected folder; a link moved as the link. With `rev`, the
        item must be as it was then. Returns the new path and the folders that changed."""
        src, dst = safe_rel(src), safe_rel(dst)
        s, where = self._where(name, src)
        if not os.path.lexists(s):
            raise WorkspaceError(f"no file or folder {src!r}")
        self._unprotected(name, where)
        root = self.app(name).resolve()
        head, _, leaf = dst.rpartition("/")
        folder = self.path(name, head) if head else root          # resolved, inside the loop; made if new
        if folder.exists() and not folder.is_dir():
            raise WorkspaceError(f"{head!r} is a file, not a folder")
        folder_rel = str(folder.relative_to(root)) if folder != root else ""
        if folder_rel:
            self._unprotected(name, folder_rel, "into ")
        d, to = folder / leaf, f"{folder_rel}/{leaf}".lstrip("/")
        self._unprotected(name, to, "to ")
        if to == where:
            raise WorkspaceError("it is already there")
        if s.is_dir() and not s.is_symlink() and (to + "/").startswith(where + "/"):
            raise WorkspaceError("a folder cannot move into itself")
        self._check_rev(s, rev, src)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            _rename_noreplace(s, d)
        except FileExistsError as exc:
            raise Exists(f"{to!r} exists: choose another name (nothing is replaced)") from exc
        return {"path": to, "parents": sorted({where.rpartition("/")[0], to.rpartition("/")[0]})}

    def remove(self, name: str, rel: str, recursive: bool = False, rev: str | None = None) -> dict[str, Any]:
        """A file, a link or a folder of the loop deleted; never its document, nor what its runs
        write. A link is deleted as the link (D905): what it points to stays. A folder only with
        `recursive` (D908: everything in it, said before), and only when nothing in it is
        protected; its parent folders stay, even empty. With `rev`, the item must be as it was."""
        p, where = self._where(name, rel)
        self._unprotected(name, where)
        if not os.path.lexists(p):
            raise WorkspaceError(f"no file {rel!r}")
        self._check_rev(p, rev, rel)
        if p.is_symlink() or not p.is_dir():
            kind = "link" if p.is_symlink() else "file"
            p.unlink()
            return {"path": where, "kind": kind}
        if not recursive:
            raise WorkspaceError(f"{rel!r} is a folder: deleting it deletes everything in it -- say so (recursive)")
        held = [r for r, _p, _st in own_files(p, skip=()) if self.protected(name, f"{where}/{r}")][:5]
        if held:
            raise WorkspaceError(f"{rel!r} holds what may not be deleted here ({', '.join(held)}): nothing was deleted")
        shutil.rmtree(p)                                   # links inside are removed as links, never followed
        return {"path": where, "kind": "folder"}

    def _unprotected(self, name: str, where: str, how: str = "") -> None:
        why = self.protected(name, where)
        if why:
            raise Protected(f"not {how}{where or 'the loop'}: {why}")

    @staticmethod
    def _check_rev(p: Path, rev: str | None, rel: str) -> None:
        if rev and revision(os.lstat(p)) != rev:
            raise Changed(f"{rel!r} changed since you opened it (another tab, or an agent): look again first")

    def read(self, name: str, rel: str) -> tuple[bytes, bool, int]:
        """(its first TEXT_MAX bytes, whether it is text, its whole size) of a file (D907: a preview
        is bounded, a cut multi-byte character left out; the whole file is `open_file`'s, in
        pieces); never under `.git` (D703)."""
        with self.open_file(name, rel) as (fh, size):
            data = fh.read(TEXT_MAX)
        is_text = b"\x00" not in data[:8192]
        if is_text:
            try:
                data.decode()
            except UnicodeDecodeError as exc:
                if size > len(data) and exc.start >= len(data) - 3 and exc.reason == "unexpected end of data":
                    data = data[:exc.start]               # cut inside a character at the bound
                else:
                    is_text = False
        return data, is_text, size

    @contextlib.contextmanager
    def open_file(self, name: str, rel: str) -> Iterator[tuple[Any, int]]:
        """(the file open for reading, its size), confined to the loop, its last step not a link
        followed (D852); never under `.git`."""
        from .confine import Escape, open_read
        from .gitignore import Ignores

        if Ignores.hidden(rel):
            raise WorkspaceError("a repository's own folder is not shown")
        p = self.path(name, rel)
        if not p.is_file():
            raise WorkspaceError(f"no file {rel!r}")
        try:
            fh = open_read(p, self.app(name).resolve())
        except (Escape, OSError) as exc:
            raise WorkspaceError(f"{rel!r} cannot be read here") from exc
        with fh:
            yield fh, os.fstat(fh.fileno()).st_size

    def write(self, name: str, rel: str, text: str) -> None:
        self.path(name, rel)                           # inside the application
        if len(text.encode()) > TEXT_MAX:
            raise WorkspaceError("too large to edit here")
        _replace(self.app(name), safe_rel(rel), text)  # D700, D852: replaced, not written through a link


def _replace(app: Path, rel: str, content: bytes | str) -> Path:
    """`rel` in the application replaced by `content` (D852): its folders made, each inside the
    application; the file renamed into place, so a symbolic link or a hardlink there is replaced
    rather than written through -- an imported document's source stays as it was."""
    from .confine import Escape, replace

    target = app / rel
    try:
        within_app = app.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        return replace(target, content, within_app)
    except Escape as exc:
        raise WorkspaceError(f"{rel!r} is outside the application") from exc


def _unzip(data: bytes) -> list[tuple[str, bytes]]:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise WorkspaceError("not a zip file") from exc
    out, total = [], 0
    for info in z.infolist():
        if info.is_dir():
            continue
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise WorkspaceError(f"{info.filename!r} is a link; links are not accepted")
        total += info.file_size
        if total > LOOP_BYTES or len(out) >= LOOP_FILES:
            raise WorkspaceError(f"a zip holds at most {LOOP_FILES} files and {LOOP_BYTES // 2**30} GB")
        out.append((safe_rel(info.filename), z.read(info)))
    return out


def _check_batch(files: list[tuple[str, bytes]], unzipped: bool = False) -> None:
    """One request's files: at most MAX_FILES and MAX_BYTES (the page sends more in batches, a
    large file in parts); a zip's contents up to a loop's own limits."""
    n, size = (LOOP_FILES, LOOP_BYTES) if unzipped else (MAX_FILES, MAX_BYTES)
    if len(files) > n or sum(len(b) for _p, b in files) > size:
        raise WorkspaceError(f"at most {n} files and {size // 2**20} MB at once" + ("" if unzipped else
                             ": the page sends more in batches; from elsewhere, send it in several requests"))


def _pick_document(rels: list[str]) -> str | None:
    top = [r for r in rels if "/" not in r]
    if DOCUMENT_FILE in top:                                 # D786: the document is problem.yaml
        return DOCUMENT_FILE
    for suffixes in ((".problem.yaml", ".problem.yml"), (".task.json", ".task.yaml")):
        hits = [r for r in top if r.endswith(suffixes)]
        if hits:
            return sorted(hits)[0]
    hits = [r for r in top if r.endswith((".yaml", ".yml", ".json")) and r not in ("package.json",)]
    return sorted(hits)[0] if len(hits) == 1 else None

