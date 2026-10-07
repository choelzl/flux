"""Reset a stopped loop's generated state, keeping its inputs and settings."""

from __future__ import annotations

import json
import shutil
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


def plan(workspace: Workspace, name: str, user: str) -> dict[str, Any]:
    root = workspace.app(name)
    if root.is_symlink():
        raise WorkspaceError("cannot reset a linked loop folder")
    within(root, workspace.root)
    return {"folders": [{"path": f"{root / folder}/", "what": what} for folder, what in FOLDERS.items()] + [
        {"path": f"{cache_root() / _key(user, name)}/", "what": "This loop's tool caches, scratch files and agent traces"}],
        "history": "All saved starts and their history, plus the last check and start status"}


def remove(path: Path) -> None:
    """Remove an entry itself; a symbolic link's target is never removed."""
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def clear(workspace: Workspace, name: str, user: str) -> None:
    plan(workspace, name, user)                  # validate again under the lifecycle locks
    root = workspace.app(name)
    try:
        with open_read(root / ".flux-app.json", root, text=True) as fh:
            meta = json.load(fh)
    except FileNotFoundError:
        meta = {}
    for folder in FOLDERS:
        remove(root / folder)
    remove(cache_root() / _key(user, name))
    for field in ("last_check", "last_start_digest", "last_options"):
        meta.pop(field, None)
    replace(root / ".flux-app.json", json.dumps(meta), root)
