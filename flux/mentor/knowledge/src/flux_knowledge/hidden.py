"""Paths hidden from a loop's AI agents (D1159): `flow.knowledge.hidden`, each an absolute path or glob in a
loop's folder. What the loop hands an agent or a prompt -- the library's papers, the workbench's index, the
`flux loop` reads -- leaves them out; the loop's own tools (its gate, its stages, its scripts) read them as ever.

The loader says each loop's hidden paths here (`hide`), by the loop's folder; a path is hidden when any loop
loaded in this process hides it. Every pattern is absolute and inside the loop that said it, so one loop's
never hides another's files.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterable

__all__ = ["hide", "hidden", "is_hidden", "version"]

_BY_HOME: dict[str, tuple[str, ...]] = {}
_VERSION = [0]


def hide(home: str | Path, patterns: Iterable[str]) -> None:
    """The hidden paths of the loop at `home` (absolute patterns), replacing what it said before."""
    key = str(Path(home).resolve())
    got = tuple(dict.fromkeys(str(p) for p in patterns if p))
    if _BY_HOME.get(key, ()) != got:
        if got:
            _BY_HOME[key] = got
        else:
            _BY_HOME.pop(key, None)
        _VERSION[0] += 1


def version() -> int:
    """Changes whenever a loop's hidden paths do: what an index built over the library is keyed by."""
    return _VERSION[0]


def _all() -> tuple[str, ...]:
    return tuple(p for ps in _BY_HOME.values() for p in ps)


def is_hidden(path: str | Path, patterns: Iterable[str]) -> bool:
    """Whether `path`, or a folder it is in, matches one of the absolute `patterns` (a glob's `*` spans folders)."""
    pats = [p.rstrip("/") or "/" for p in patterns if p]
    if not pats:
        return False
    try:
        p = Path(os.path.abspath(path)).resolve()
    except (OSError, RuntimeError):
        p = Path(os.path.abspath(path))
    for q in (p, *p.parents):
        s = str(q)
        if any(s == pat or fnmatch.fnmatchcase(s, pat) for pat in pats):
            return True
    return False


def hidden(path: str | Path) -> bool:
    """Whether a loop loaded in this process hides `path`."""
    pats = _all()
    return bool(pats) and is_hidden(path, pats)
