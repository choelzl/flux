"""A user's applications (D683): `<data>/users/<name>/apps/<app>/`, each a problem document and
its files (golden model, scripts, knowledge), uploaded as files or a zip, or written from the
crafter. Every path a request names is resolved inside the application and refused outside it."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

MAX_BYTES = 256 * 1024 * 1024           # one request (D700: the page sends a large upload in batches)
MAX_FILES = 900                         # below the 1000 files a request may carry (Starlette)
LOOP_BYTES = 8 * 1024 ** 3              # a loop's own files in all
LOOP_FILES = 100_000
PART_BYTES = 64 * 1024 * 1024           # one part of a file sent in parts
TEXT_MAX = 2 * 1024 * 1024
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,59}$")
DOCUMENT_FILE = "problem.yaml"            # D786: a loop's document; the loop's name is its id
DOC_SUFFIXES = (".problem.yaml", ".problem.yml", ".task.json", ".task.yaml", ".yaml", ".yml", ".json")


class WorkspaceError(ValueError):
    pass


def check_name(name: str) -> str:
    if not _NAME.match(name or ""):
        raise WorkspaceError("an application name is letters, digits, - and _ (at most 60), starting with a letter or digit")
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
            raise WorkspaceError(f"application {name!r} exists")
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
        d.mkdir(parents=True, exist_ok=True)
        for rel, (_p, content) in zip(rels, files):
            target = d / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        meta = {"document": doc, "id": name}                          # D786: the loop's name is the problem's id
        (d / ".flux-app.json").write_text(json.dumps(meta))
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
        self._check_room(name, sum(len(b) for _p, b in files), len(files))
        prefix = safe_rel(sub) + "/" if sub.strip("/") else ""
        rels = [safe_rel(prefix + p) for p, _b in files]
        written = []
        for rel, (_p, content) in zip(rels, files):
            if rel == ".flux-app.json":
                raise WorkspaceError("that name is the server's")
            target = self.path(name, rel)
            if target.is_dir():
                raise WorkspaceError(f"{rel!r} is a folder")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)                # D700: a linked file is replaced, not written through
            target.write_bytes(content)
            written.append(rel)
        return written

    def _check_room(self, name: str, more_bytes: int, more_files: int) -> None:
        """A loop's own files stay under LOOP_BYTES and LOOP_FILES in all (D700)."""
        n, size = 0, 0
        for rec in self.inputs(name):
            n += 1
            size += rec["size"]
        if size + more_bytes > LOOP_BYTES or n + more_files > LOOP_FILES:
            raise WorkspaceError(f"a loop holds at most {LOOP_FILES} files and {LOOP_BYTES // 2**30} GB of its own")

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
        if offset == 0:
            self._check_room(name, 0, 1)
            part.parent.mkdir(parents=True, exist_ok=True)
            part.unlink(missing_ok=True)
        elif not part.exists() or part.stat().st_size != offset:
            raise WorkspaceError(f"{rel}: the part at {offset} does not follow the parts before")
        if offset + len(data) > LOOP_BYTES:
            raise WorkspaceError(f"a file is at most {LOOP_BYTES // 2**30} GB")
        with open(part, "ab") as fh:
            fh.write(data)
        size = part.stat().st_size
        if final:
            target.unlink(missing_ok=True)
            part.replace(target)
        return size

    def drop_part(self, name: str, rel: str) -> None:
        """A file sent in parts, given up (D702): its partial file goes, and the folders it leaves empty."""
        target = self.path(name, safe_rel(rel))
        part = target.with_name(f".{target.name}.part-upload")
        part.unlink(missing_ok=True)
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

    def create_empty(self, name: str) -> Path:
        """A loop with no document yet (D704): an agent is about to write it."""
        d = self.root / check_name(name)
        if d.exists():
            raise WorkspaceError(f"application {name!r} exists")
        d.mkdir(parents=True)
        (d / ".flux-app.json").write_text(json.dumps({"document": None, "id": name}))
        return d

    def create_from_text(self, name: str, filename: str, text: str) -> dict[str, Any]:
        """A loop from a document's text: its problem.yaml, or the NAME.problem.yaml it is called (D787)."""
        doc = PurePosixPath(filename or "").name
        doc = doc if doc.endswith((".problem.yaml", ".problem.yml")) else DOCUMENT_FILE
        return self.create(name, [(doc, text.encode())], replace=(self.root / name).exists())

    def documents(self, name: str) -> list[dict[str, Any]]:
        """The loop's problems (D787): its problem.yaml and each NAME.problem.yaml, with whether
        each loads and the record its runs keep."""
        from flux_loop.document import loadable, record_name

        d = self.app(name)
        return [{"path": p.name, "record": record_name(p), "ok": not err, "error": err[:400]} for p, err in loadable(d)]

    def delete(self, name: str) -> None:
        shutil.rmtree(self.app(name))

    def path(self, name: str, rel: str) -> Path:
        """`rel` inside the application, resolved (a link pointing out is refused)."""
        d = self.app(name).resolve()
        p = (d / safe_rel(rel)).resolve()
        if p != d and d not in p.parents:
            raise WorkspaceError(f"{rel!r} is outside the application")
        return p

    def files(self, name: str, sub: str = "", show_ignored: bool = False) -> list[dict[str, Any]]:
        """A folder of the loop as the Files tab lists it (D703): what its `.gitignore` files ignore
        left out, or marked with `show_ignored`; `.git` never."""
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
            ignored = ig.ignored(rel, is_dir)
            if ignored and not show_ignored:
                continue
            st = p.lstat()
            out.append({"path": rel, "dir": is_dir, "size": st.st_size, "mtime": st.st_mtime, "ignored": ignored})
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
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if not p.is_file() or p.is_symlink() or rel.parts[0] in ("out", "runs", "workbench") or p.name == ".flux-app.json" \
                    or "__pycache__" in rel.parts:
                continue
            h.update(str(rel).encode() + b"\0" + p.read_bytes() + b"\0")
        return h.hexdigest()[:16]

    def inputs(self, name: str) -> list[dict[str, Any]]:
        """The loop's own files (D696): the document and what it runs -- scripts, golden models,
        specs -- not what its runs write (out/, runs/, the workbench)."""
        from .gitignore import Ignores

        root = self.app(name).resolve()
        doc = self.meta(name).get("document")
        ig = Ignores(root)
        out = []
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if not p.is_file() or rel.parts[0] in ("out", "runs", "workbench") or p.name == ".flux-app.json" \
                    or "__pycache__" in rel.parts or ".git" in rel.parts or p.name.endswith(".part-upload"):
                continue
            ignored = ig.ignored(str(rel))                  # D703: what .gitignore ignores, marked
            out.append({"path": str(rel), "size": p.lstat().st_size, "document": str(rel) == doc, "ignored": ignored})
        return out

    def remove(self, name: str, rel: str) -> None:
        """One of the loop's own files deleted; never its document, nor what its runs write."""
        p = self.path(name, rel)
        root = self.app(name).resolve()
        parts = p.relative_to(root).parts
        if not parts or parts[0] in ("out", "runs", "workbench") or p.name == ".flux-app.json":
            raise WorkspaceError(f"{rel!r} is not one of the loop's own files")
        if str(p.relative_to(root)) == self.meta(name).get("document"):
            raise WorkspaceError("the document itself cannot be deleted here")
        if not p.is_file():
            raise WorkspaceError(f"no file {rel!r}")
        p.unlink()
        d = p.parent
        while d != root and not any(d.iterdir()):
            d.rmdir()
            d = d.parent

    def read(self, name: str, rel: str) -> tuple[bytes, bool]:
        """(content, whether it is text) of a file, at most TEXT_MAX for text; never under `.git` (D703)."""
        from .gitignore import Ignores

        if Ignores.hidden(rel):
            raise WorkspaceError("a repository's own folder is not shown")
        p = self.path(name, rel)
        if not p.is_file():
            raise WorkspaceError(f"no file {rel!r}")
        data = p.read_bytes()
        is_text = b"\x00" not in data[:8192]
        if is_text:
            try:
                data[:TEXT_MAX].decode()
            except UnicodeDecodeError:
                is_text = False
        return data, is_text

    def write(self, name: str, rel: str, text: str) -> None:
        p = self.path(name, rel)
        if len(text.encode()) > TEXT_MAX:
            raise WorkspaceError("too large to edit here")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.unlink(missing_ok=True)                      # D700: a linked file is replaced, not written through
        p.write_text(text)


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


