"""Translate runtime paths while keeping persisted run pointers readable on the host."""

from __future__ import annotations

import json
import os

PATH_MAP = "FLUX_SANDBOX_PATH_MAP"


def translate(path: str, pairs: list[tuple[str, str]]) -> str:
    """Translate a whole absolute path, matching the most specific folder boundary."""
    if not os.path.isabs(path):
        return path
    for source, target in sorted(pairs, key=lambda pair: len(pair[0]), reverse=True):
        if path == source or path.startswith(source.rstrip("/") + "/"):
            return target.rstrip("/") + path[len(source.rstrip("/")):]
    return path


def _pairs() -> list[tuple[str, str]]:
    if os.environ.get("FLUX_SANDBOXED") != "1":
        return []
    try:
        rows = json.loads(os.environ.get(PATH_MAP) or "[]")
    except ValueError:
        return []
    return [(row[0], row[1]) for row in rows if isinstance(row, list) and len(row) == 2
            and all(isinstance(p, str) and os.path.isabs(p) for p in row)] if isinstance(rows, list) else []


def host_path(path: str) -> str:
    return translate(path, [(inside, host) for host, inside in _pairs()])


def container_path(path: str) -> str:
    return translate(path, _pairs())
