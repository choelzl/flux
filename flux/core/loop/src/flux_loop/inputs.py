"""What a loop's evidence was made from (D853): one fingerprint of the loop's input files -- its
document, the scripts, golden models, helpers and data beside it -- and its params.

An admission holds only for the checks that made it, and a measurement only for what measured it.
A check that imports a helper, a stage that reads a data file, a gate that takes `{params}`: any of
them changing changes this fingerprint, so a resumed loop re-checks what it admitted and measures
again what it measured. It is conservative on purpose: any input edit counts, whether or not that
step reads the file.

Not inputs: the document itself (what of it bears on evidence -- the gate, the stages' commands,
params, workload -- is keyed where it is used, so a new objective re-measures nothing), what runs
write (`out/`, `runs/`, `workbench/`), the library (papers, not evidence), hidden files and the
server's own, caches and backups. A file over `BIG` bytes is seen by its size,
its modification time and its first and last `SAMPLE` bytes -- traces of gigabytes are not read
whole each pass; touching one without changing it costs one re-measurement.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

__all__ = ["BIG", "SAMPLE", "digest", "file_digest"]

#: Above this, a file is seen by its size, time and its two ends rather than read whole.
BIG = 4 * 2 ** 20
SAMPLE = 64 * 2 ** 10
#: Top-level folders a run writes, or that hold no evidence.
SKIP_TOP = frozenset({"out", "runs", "workbench", "library"})
SKIP_ANY = frozenset({"__pycache__", ".git", "node_modules", ".pytest_cache"})
SKIP_SUFFIX = (".orig", ".part-upload", ".pyc", ".db", ".db-wal", ".db-shm", ".db-journal", ".runs.json")

_SEEN: dict[tuple[str, int, int, int], str] = {}


def file_digest(path: str | os.PathLike) -> str:
    """One file's fingerprint: its content, or for a big one its size, time and two ends."""
    p = Path(path)
    st = p.stat()
    key = (str(p), st.st_size, st.st_mtime_ns, st.st_ctime_ns)   # a write changes ctime, which utime cannot set back
    got = _SEEN.get(key)
    if got is not None:
        return got
    h = hashlib.sha256()
    if st.st_size <= BIG:
        h.update(p.read_bytes())
    else:
        h.update(f"{st.st_size}:{st.st_mtime_ns}".encode())
        with open(p, "rb") as fh:
            h.update(fh.read(SAMPLE))
            fh.seek(max(0, st.st_size - SAMPLE))
            h.update(fh.read(SAMPLE))
    out = h.hexdigest()[:16]
    _SEEN[key] = out
    return out


def _inputs(home: Path) -> list[Path]:
    out = []
    for root, dirs, files in os.walk(home):
        rel = Path(root).relative_to(home)
        top = rel.parts[0] if rel.parts else ""
        if top in SKIP_TOP:
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in SKIP_ANY and not d.startswith(".")
                         and not (not rel.parts and d in SKIP_TOP))
        records = tuple(f[:-len(".db")] + "." for f in files if f.endswith(".db"))   # a record's own files beside it
        for f in sorted(files):
            if f.startswith(".") or f.endswith(SKIP_SUFFIX) or f in ("problem.yaml", "problem.yml") \
                    or f.endswith((".problem.yaml", ".problem.yml")) or (records and f.startswith(records)):
                continue
            p = Path(root) / f
            if p.is_file():
                out.append(p)
    return out


def digest(home: str | os.PathLike | None, params: Any = None, extra: Any = None) -> str:
    """The loop's inputs and params as one fingerprint; "" without a home. `extra`: anything else
    the evidence depends on that lies outside the folder (a workload's resolved content)."""
    if not home:
        return ""
    home = Path(home)
    h = hashlib.sha256(json.dumps({"params": params, "extra": extra}, sort_keys=True, default=str).encode())
    if home.is_dir():
        for p in _inputs(home):
            try:
                h.update(f"{p.relative_to(home)}={file_digest(p)}\n".encode())
            except OSError:
                h.update(f"{p.relative_to(home)}=unreadable\n".encode())
    return h.hexdigest()[:16]
