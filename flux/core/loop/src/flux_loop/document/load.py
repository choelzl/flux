"""Loading a document from its folder or file (D786, D787, D802), and the loop's knobs for it."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..types import LoopRequest
from .keys import ALT_SUFFIXES, DOCUMENT_FILE, TaskError
from .library import CONFINE
from .spec import TaskSpec


class ManyDocuments(TaskError):
    """A folder holding several problems that load: the caller names one (D787)."""

    def __init__(self, folder: Path, documents: list[Path]):
        self.documents = documents
        super().__init__(f"{folder}: {len(documents)} problems here ({', '.join(d.name for d in documents)}): name one")


def documents_in(folder: str | Path) -> list[Path]:
    """The problem documents of a folder (D787): its `problem.yaml` (or `problem.json`) first,
    then each `NAME.problem.yaml`."""
    f = Path(folder)
    main = [f / n for n in (DOCUMENT_FILE, "problem.json") if (f / n).is_file()][:1]
    return main + sorted(p for p in f.iterdir() if p.is_file() and p.name.endswith(ALT_SUFFIXES))


def alt_name(path: str | Path) -> str:
    """`NAME` of a `NAME.problem.yaml`: which of the folder's problems it is; '' for its `problem.yaml`."""
    name = Path(path).name
    for suffix in ALT_SUFFIXES:
        if name.endswith(suffix) and name != suffix[1:]:
            return name[: -len(suffix)]
    return ""


def record_name(path: str | Path) -> str:
    """The record a document's runs keep (D787): the folder's name, `<folder>.<NAME>` for a
    `NAME.problem.yaml` -- the file `out/<record>.db` and its campaign."""
    p = Path(path)
    folder, alt = p.resolve().parent.name, alt_name(p)
    return f"{folder}.{alt}" if alt else folder


def loadable(folder: str | Path) -> list[tuple[Path, str]]:
    """Each document of a folder with what its loader says ('' when it loads)."""
    out = []
    for d in documents_in(folder):
        try:
            load_task(d)
            out.append((d, ""))
        except TaskError as exc:
            out.append((d, str(exc)))
    return out


def load_task(path: str | Path) -> TaskSpec:
    """A task from its folder or a document file. The id is the folder's name (D786): a document
    does not say it. A folder with several problems (D787) loads the one that loads, and when
    more than one does, raises ManyDocuments for the caller to ask which. Every way it can fail
    is a TaskError that names the file (D590): a missing file, a syntax error with its line, a
    key no document has, and whatever the document itself gets wrong."""
    p = Path(path)
    parent = _parent_listing(p)
    if parent is not None:                                 # D802: a sub-loop alone, as its parent reads it
        return parent
    if p.is_dir():
        docs = documents_in(p)
        if not docs:
            raise TaskError(f"{p}: no problem document here ({DOCUMENT_FILE}, or NAME.problem.yaml)")
        if len(docs) > 1:
            said = loadable(p)
            good = [d for d, err in said if not err]
            if len(good) > 1:
                raise ManyDocuments(p, good)
            if not good:
                raise TaskError(said[0][1])
            docs = good
        p = docs[0]
    if p.suffix not in (".json", ".yaml", ".yml"):
        raise TaskError(f"{p}: a problem document is a .yaml, .yml or .json file")
    if not p.is_file():
        raise TaskError(f"{p}: no such file")
    text = p.read_text()
    try:
        if p.suffix == ".json":
            doc = json.loads(text)
        else:
            import yaml

            doc = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 -- yaml and json raise their own kinds; both are the file's fault
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else (
            f" (line {exc.lineno}, column {exc.colno})" if hasattr(exc, "lineno") else "")
        raise TaskError(f"{p}: not valid {'JSON' if p.suffix == '.json' else 'YAML'}{where}: "
                        f"{getattr(exc, 'problem', None) or getattr(exc, 'msg', None) or exc}") from exc
    if not isinstance(doc, dict):
        raise TaskError(f"{p}: a problem document is a mapping of keys (statement, language, flow, ...)")
    try:
        return task_in(doc, p.parent, alt=alt_name(p))
    except TaskError as exc:
        raise TaskError(f"{p}: {exc}") from exc


def _parent_listing(path: Path) -> TaskSpec | None:
    """The sub-task a folder is, when a document a few folders up lists it under `subtasks:`
    (D802): read through the parent, so it inherits what the parent says and keeps its record."""
    import os

    import yaml

    folder = (path if path.is_dir() else path.parent).resolve()
    if not (folder / DOCUMENT_FILE).is_file():
        return None
    root = CONFINE.get()
    for anc in list(folder.parents)[:3]:
        if root is not None and anc != root and root not in anc.parents:
            break                                          # D905: a confined load reads no parent outside
        doc_path = anc / DOCUMENT_FILE
        if not doc_path.is_file():
            continue
        try:
            raw = yaml.safe_load(doc_path.read_text()) or {}
        except yaml.YAMLError:
            continue
        rel = os.path.relpath(folder, anc)
        if isinstance(raw, dict) and isinstance(raw.get("subtasks"), list) and rel in raw["subtasks"]:
            whole = load_task(doc_path)
            return next(c for c in whole.subtasks if c.from_path == rel)
    return None


def task_in(doc: dict[str, Any], home: Path, alt: str = "") -> TaskSpec:
    """The task a document says in its folder `home`: the folder's name is its id (D786); `alt`,
    the NAME of a `NAME.problem.yaml`, names its record `<id>.NAME` (D787)."""
    folder = Path(home).resolve().name
    if "id" in doc:
        raise TaskError(f"a document does not say its `id`: it is its folder's name ({folder}) (D786)")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", folder):
        raise TaskError(f"the folder's name {folder!r} is the problem's id: letters, digits, _, . or - (D786)")
    return TaskSpec.from_dict({**doc, "id": folder, **({"_record": f"{folder}.{alt}"} if alt else {})}, base=home)


def request_for(task: TaskSpec, **overrides: Any) -> LoopRequest:
    """The loop's knobs for this task: the document's `budget`, then the caller's."""
    params = {"task": task.id, **task.params, **(overrides.pop("params", None) or {})}
    knobs = {"baseline": task.baseline is not None, "baseline_only": bool((task.baseline or {}).get("only")),
             **task.budget, **overrides}
    if isinstance(knobs.get("prototype"), str):
        knobs["prototype"] = True             # `prototype: systemc` names the language; the stage is on
    return LoopRequest(**knobs, params=params)
