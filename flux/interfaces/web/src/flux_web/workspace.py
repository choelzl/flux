"""A user's applications (D683): `<data>/users/<name>/apps/<app>/`, each a problem document and
its files (golden model, scripts, knowledge), uploaded as files or a zip, or written from the
crafter. Every path a request names is resolved inside the application and refused outside it."""

from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

MAX_BYTES = 50 * 1024 * 1024
MAX_FILES = 500
TEXT_MAX = 2 * 1024 * 1024
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,59}$")
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
        if len(files) == 1 and files[0][0].lower().endswith(".zip"):
            files = _unzip(files[0][1])
        if not files:
            raise WorkspaceError("no files")
        if len(files) > MAX_FILES or sum(len(b) for _p, b in files) > MAX_BYTES:
            raise WorkspaceError(f"at most {MAX_FILES} files and {MAX_BYTES // 2**20} MB")
        rels = [safe_rel(p) for p, _b in files]
        # a folder upload names every file under the folder: drop the common first directory
        firsts = {PurePosixPath(r).parts[0] for r in rels}
        if len(firsts) == 1 and all(len(PurePosixPath(r).parts) > 1 for r in rels):
            rels = [str(PurePosixPath(*PurePosixPath(r).parts[1:])) for r in rels]
        doc = _pick_document(rels)
        if doc is None:
            raise WorkspaceError("no problem document among the files (a *.problem.yaml or *.task.json)")
        d.mkdir(parents=True, exist_ok=True)
        for rel, (_p, content) in zip(rels, files):
            target = d / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        meta = {"document": doc, "id": _doc_id(d / doc) or name}
        (d / ".flux-app.json").write_text(json.dumps(meta))
        return meta

    def add(self, name: str, files: list[tuple[str, bytes]], sub: str = "") -> list[str]:
        """Files added to (or replacing files in) an existing application, under `sub`; a single
        .zip is unpacked. The same checks as a new one."""
        d = self.app(name)
        if len(files) == 1 and files[0][0].lower().endswith(".zip"):
            files = _unzip(files[0][1])
        if not files:
            raise WorkspaceError("no files")
        if len(files) > MAX_FILES or sum(len(b) for _p, b in files) > MAX_BYTES:
            raise WorkspaceError(f"at most {MAX_FILES} files and {MAX_BYTES // 2**20} MB")
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
            target.write_bytes(content)
            written.append(rel)
        meta = self.meta(name)
        if meta.get("document") in written:
            meta["id"] = _doc_id(d / meta["document"]) or meta.get("id")
            (d / ".flux-app.json").write_text(json.dumps(meta))
        return written

    def create_from_text(self, name: str, filename: str, text: str) -> dict[str, Any]:
        rel = safe_rel(filename)
        if not rel.endswith(DOC_SUFFIXES):
            raise WorkspaceError("the document's name ends in .problem.yaml or .task.json")
        return self.create(name, [(rel, text.encode())], replace=(self.root / name).exists())

    def delete(self, name: str) -> None:
        shutil.rmtree(self.app(name))

    def path(self, name: str, rel: str) -> Path:
        """`rel` inside the application, resolved (a link pointing out is refused)."""
        d = self.app(name).resolve()
        p = (d / safe_rel(rel)).resolve()
        if p != d and d not in p.parents:
            raise WorkspaceError(f"{rel!r} is outside the application")
        return p

    def files(self, name: str, sub: str = "") -> list[dict[str, Any]]:
        base = self.path(name, sub) if sub else self.app(name).resolve()
        if not base.is_dir():
            raise WorkspaceError(f"{sub!r} is not a folder")
        root = self.app(name).resolve()
        out = []
        for p in sorted(base.iterdir(), key=lambda q: (not q.is_dir(), q.name)):
            if p.name == ".flux-app.json":
                continue
            st = p.lstat()
            out.append({"path": str(p.relative_to(root)), "dir": p.is_dir() and not p.is_symlink(),
                        "size": st.st_size, "mtime": st.st_mtime})
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

    def read(self, name: str, rel: str) -> tuple[bytes, bool]:
        """(content, whether it is text) of a file, at most TEXT_MAX for text."""
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
        p.write_text(text)
        meta = self.meta(name)
        if rel == meta.get("document"):
            meta["id"] = _doc_id(p) or meta.get("id")
            (self.app(name) / ".flux-app.json").write_text(json.dumps(meta))


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
        if total > MAX_BYTES or len(out) >= MAX_FILES:
            raise WorkspaceError(f"at most {MAX_FILES} files and {MAX_BYTES // 2**20} MB")
        out.append((safe_rel(info.filename), z.read(info)))
    return out


def _pick_document(rels: list[str]) -> str | None:
    top = [r for r in rels if "/" not in r]
    for suffixes in ((".problem.yaml", ".problem.yml"), (".task.json", ".task.yaml")):
        hits = [r for r in top if r.endswith(suffixes)]
        if hits:
            return sorted(hits)[0]
    hits = [r for r in top if r.endswith((".yaml", ".yml", ".json")) and r not in ("package.json",)]
    return sorted(hits)[0] if len(hits) == 1 else None


def _doc_id(path: Path) -> str | None:
    """The document's `id`, read as data (nothing of it runs here)."""
    try:
        text = path.read_text()
        if path.suffix == ".json":
            doc = json.loads(text)
        else:
            import yaml

            doc = yaml.safe_load(text)
        return str(doc.get("id")) if isinstance(doc, dict) and doc.get("id") else None
    except Exception:  # noqa: BLE001 -- a document `flux task check` will explain
        return None
