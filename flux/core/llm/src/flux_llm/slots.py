"""At most N calls at once on one endpoint, across every loop and user of this machine (D1027).

An agent's turn and a model's request each hold a slot of their endpoint's -- its host and port, so
two agents or a model on one server share them -- while they run. A slot is a file lock in
`FLUX_SLOTS_DIR` (the server's `slots/` under `flux serve`, mounted into every sandbox; else
~/.cache/flux/slots), so runs in different processes and containers wait for the same slots. N is
the admin's `FLUX_<NAME>_AT_ONCE` (an agent's) or `FLUX_REMOTE_AT_ONCE` (Flux's model); 0 or unset:
no limit. A call that waits says so as a `wait:` task.
"""

from __future__ import annotations

import fcntl
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

__all__ = ["at_once", "endpoint_key", "endpoint_slot", "slots_dir"]


def slots_dir() -> Path:
    raw = os.environ.get("FLUX_SLOTS_DIR")
    if raw:
        return Path(raw)
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "flux" / "slots"


def endpoint_key(base_url: str, fallback: str) -> str:
    """`host:port` of an endpoint URL (the scheme's port when it says none); `fallback` (an agent's
    name: its provider's own endpoint) without one."""
    try:
        u = urlsplit(base_url or "")
        if u.hostname:
            port = u.port or (443 if u.scheme == "https" else 80)
            return f"{u.hostname.lower()}:{port}"
    except ValueError:
        pass
    return fallback


def _take(folder: Path, n: int) -> int | None:
    for i in range(n):
        try:
            fd = os.open(folder / f"slot-{i}.lock", os.O_RDWR | os.O_CREAT, 0o666)
        except OSError:
            return -1                             # no slot to take here: the call goes on unlimited
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            os.close(fd)
    return None


@contextmanager
def endpoint_slot(key: str, n: int, what: str = "") -> Iterator[None]:
    """Hold one of the endpoint `key`'s `n` slots while the block runs (`n` <= 0: no limit)."""
    if n <= 0:
        yield
        return
    folder = slots_dir() / re.sub(r"[^A-Za-z0-9_.-]+", "_", key)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    fd = _take(folder, n)
    if fd is None:
        try:
            from flux_profile import phase
        except Exception:  # noqa: BLE001
            phase = None
        started = time.monotonic()
        if phase is not None:
            with phase(f"wait: {key}, {n} at once", why=f"every loop's calls to {key} share {n} slot(s): {what}"[:200]) as out:
                while fd is None:
                    time.sleep(0.5)
                    fd = _take(folder, n)
                out["waited_s"] = round(time.monotonic() - started, 1)
        else:
            while fd is None:
                time.sleep(0.5)
                fd = _take(folder, n)
    try:
        yield
    finally:
        if fd is not None and fd >= 0:
            os.close(fd)                          # closing releases the lock


def at_once(name: str) -> int:
    """`FLUX_<NAME>_AT_ONCE` as a whole number; 0 when unset or not a number."""
    try:
        return max(0, int(os.environ.get(f"FLUX_{name.upper()}_AT_ONCE") or 0))
    except ValueError:
        return 0
