"""Finding traces in a directory, and putting them where the simulator reads them fast.

ChampSim streams its trace through a gzip pipe for the whole run, and a network mount's bandwidth
is shared by every concurrent simulation, so traces on such a mount are copied once to local
scratch (`$FLUX_TMPDIR`) and read from there.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Callable

SUFFIXES = (".gz", ".xz")      # what ChampSim decompresses


def trace_name(path: Path) -> str:
    """`fdd_su_v1_0.simout_champsim.gz` -> `fdd_su_v1_0`: the stem before the first dot, metric-key safe."""
    return re.sub(r"\W", "_", path.name.split(".", 1)[0])


def resolve(directory: str | os.PathLike[str]) -> dict[str, Path]:
    """`{name: path}` for every *.gz / *.xz trace in `directory`, sorted by name."""
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"trace directory not found: {root}")
    found = {trace_name(p): p for p in sorted(root.iterdir()) if p.is_file() and p.suffix in SUFFIXES}
    if not found:
        raise FileNotFoundError(f"no *.gz or *.xz traces in {root}")
    return dict(sorted(found.items()))


def scratch_root() -> Path | None:
    """`$FLUX_TMPDIR`, then `$TMPDIR`; no guess, since an unchosen directory may be on the slow mount."""
    for name in ("FLUX_TMPDIR", "TMPDIR"):
        value = os.environ.get(name)
        if value and Path(value).is_dir():
            return Path(value)
    return None


def is_slow_mount(path: Path) -> bool:
    """Is `path` on a network or FUSE filesystem (from /proc/mounts)? Unknown counts as fine."""
    try:
        mounts = [line.split() for line in Path("/proc/mounts").read_text().splitlines()]
    except OSError:
        return False
    resolved = str(path.resolve())
    best, best_type = "", ""
    for entry in mounts:
        if len(entry) >= 3 and resolved.startswith(entry[1]) and len(entry[1]) > len(best):
            best, best_type = entry[1], entry[2]
    return best_type.startswith(("fuse.sshfs", "nfs", "cifs", "smb", "fuse.s3", "9p"))


def stage(paths: dict[str, Path], *, root: Path | None = None,
          log: Callable[[str], None] | None = None) -> dict[str, Path]:
    """Copy traces on a slow mount to local scratch and return the paths to use.

    Idempotent by size. Any failure returns the original paths: staging is only an optimisation.
    """
    say = log or (lambda _msg: None)
    root = root or scratch_root()
    if root is None or not any(is_slow_mount(p) for p in paths.values()):
        return paths
    into = root / "champsim-traces"
    out: dict[str, Path] = {}
    try:
        into.mkdir(parents=True, exist_ok=True)
        for name, source in paths.items():
            target = into / source.name
            if not (target.is_file() and target.stat().st_size == source.stat().st_size):
                say(f"  staging {source.name} ({source.stat().st_size / 1e6:.0f} MB) to {into}")
                shutil.copy2(source, target)
            out[name] = target
    except OSError as exc:
        say(f"  staging failed ({exc}); reading traces where they are")
        return paths
    return out
