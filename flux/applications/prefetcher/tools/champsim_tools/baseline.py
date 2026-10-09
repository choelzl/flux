"""The no-L2-prefetcher IPC every speedup is quoted against, computed once per binary and trace.

Cached in `$XDG_CACHE_HOME/flux/champsim/baseline.json`, keyed on (binary sha256, trace size +
sha256 of its first MB, warmup, sim). A per-key lock makes parallel workers wait for the one
computing it rather than all running it; the file itself is updated under a short global lock.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path

from .binary import fingerprint, resolve_binary
from .run import simulate


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "flux" / "champsim"


def trace_key(trace: Path) -> str:
    with trace.open("rb") as f:
        head = hashlib.sha256(f.read(1 << 20)).hexdigest()
    return f"{trace.stat().st_size}:{head}"


def _read(path: Path) -> dict[str, float]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def baseline_ipc(binary: str | Path | None, trace: str | Path, warmup: int, sim: int,
                 *, timeout_s: float | None = None) -> float:
    exe = resolve_binary(binary)
    trace = Path(trace)
    key = f"{fingerprint(exe)}|{trace_key(trace)}|{warmup}|{sim}"
    root = cache_dir()
    root.mkdir(parents=True, exist_ok=True)
    table = root / "baseline.json"
    if key in (got := _read(table)):
        return float(got[key])
    with open(root / f"baseline-{hashlib.sha256(key.encode()).hexdigest()[:16]}.lock", "w") as mine:
        fcntl.flock(mine, fcntl.LOCK_EX)
        if key in (got := _read(table)):              # another worker finished it while we waited
            return float(got[key])
        ipc = simulate(exe, "", (), trace, warmup, sim, timeout_s=timeout_s)["ipc"]
        with open(root / "baseline.json.lock", "w") as all_:
            fcntl.flock(all_, fcntl.LOCK_EX)
            got = _read(table)
            got[key] = ipc
            tmp = table.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(got, indent=1, sort_keys=True))
            tmp.replace(table)
    return ipc
