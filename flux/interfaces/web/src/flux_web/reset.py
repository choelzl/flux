"""Reset a stopped loop's generated state, keeping its inputs and settings."""

from __future__ import annotations

import json
import shutil
from collections.abc import Collection
from pathlib import Path
from typing import Any

from .admin import _key, cache_root
from .confine import open_read, replace, within
from .workspace import Workspace, WorkspaceError


FOLDERS = {
    "out": "Results, measurements, decisions and pass records",
    "runs": "Full run logs, archived logs, agent output, answers and notes",
    "workbench": "Agents' working files, tools and notes",
    ".author-work": "Problem-writing agent's scratch files",
}
FOLDER_KEYS = {"out": "history", "runs": "history", "workbench": "workbench", ".author-work": "author_work"}
KEEP_OPTIONS = {
    "workbench": "Workbench",
    "history": "Results, logs and run history",
    "author_work": "Author scratch files",
    "cache": "Tool caches and agent traces",
}


def plan(workspace: Workspace, name: str, user: str) -> dict[str, Any]:
    root = workspace.app(name)
    if root.is_symlink():
        raise WorkspaceError("cannot reset a linked loop folder")
    within(root, workspace.root)
    return {"folders": [{"key": FOLDER_KEYS[folder], "path": f"{root / folder}/", "what": what} for folder, what in FOLDERS.items()] + [
        {"key": "cache", "path": f"{cache_root() / _key(user, name)}/", "what": "This loop's tool caches, scratch files and agent traces"}],
        "keep_options": [{"key": key, "label": label} for key, label in KEEP_OPTIONS.items()],
        "history": "All saved starts and their history, plus the last check and start status"}


def remove(path: Path) -> None:
    """Remove an entry itself; a symbolic link's target is never removed."""
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def clear(workspace: Workspace, name: str, user: str, keep: Collection[str] = ()) -> None:
    if set(keep) - KEEP_OPTIONS.keys():
        raise ValueError("unknown reset option")
    plan(workspace, name, user)                  # validate again under the lifecycle locks
    root = workspace.app(name)
    try:
        with open_read(root / ".flux-app.json", root, text=True) as fh:
            meta = json.load(fh)
    except FileNotFoundError:
        meta = {}
    for folder in FOLDERS:
        if FOLDER_KEYS[folder] not in keep:
            remove(root / folder)
    if "cache" not in keep:
        remove(cache_root() / _key(user, name))
    fields = ("last_check",) if "history" in keep else ("last_check", "last_start_digest", "last_options")
    for field in fields:
        meta.pop(field, None)
    replace(root / ".flux-app.json", json.dumps(meta), root)
