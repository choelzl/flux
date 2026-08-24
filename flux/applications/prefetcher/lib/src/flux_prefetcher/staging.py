"""Putting the traces where the simulator can actually read them.

MEASURED, not assumed. This repository is checked out on an sshfs mount:

    repo   fuse.sshfs   storage01...:/storage/homes/choelzl     114 MB/s
    local  ext4         /home/shared                            6.4 GB/s

Fifty-six times, and the sshfs figure is not per reader -- it is the whole mount, shared. ChampSim
streams its trace through a `popen("gzip -dc ...")` pipe for the entire run, so N concurrent
simulations split that 114 MB/s N ways. It shows up exactly as you would expect and not at all
where you would look: the simulator sits at 21% CPU, the decompressor at nearly zero, load average
climbs to 14 on a 64-core machine, and twelve concurrent runs each take three times their solo
time. Nothing in that picture says "network filesystem" until you check where the file lives.

So the traces are copied once to local scratch and every run reads them from there. 380 MB of copy
buys back the difference on every simulation of every configuration for the rest of the study.

`$FLUX_TMPDIR` is what the dev shell already sets up for this (flake.nix), so staging follows it
rather than inventing a second convention.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable


def scratch_root() -> Path | None:
    """Local scratch, or None if this environment has not been given any.

    `$FLUX_TMPDIR` first, because the dev shell sets it deliberately; `$TMPDIR` after, because a
    plain shell may still have somewhere better than the repository. No default guess: staging
    into a directory nobody chose is how a 380 MB copy ends up on the same slow mount.
    """
    for name in ("FLUX_TMPDIR", "TMPDIR"):
        value = os.environ.get(name)
        if value and Path(value).is_dir():
            return Path(value)
    return None


def is_slow_mount(path: Path) -> bool:
    """Is `path` on a filesystem worth copying off?

    Reads `/proc/mounts` and looks for a network or FUSE type. Deliberately conservative: an
    unrecognised filesystem is treated as fine, because a wrong "yes" costs a pointless copy on
    every run and a wrong "no" costs only the speed we had anyway.
    """
    try:
        mounts = [line.split() for line in Path("/proc/mounts").read_text().splitlines()]
    except OSError:
        return False
    resolved = str(path.resolve())
    best, best_type = "", ""
    for entry in mounts:
        if len(entry) < 3:
            continue
        point, fstype = entry[1], entry[2]
        if resolved.startswith(point) and len(point) > len(best):
            best, best_type = point, fstype
    return best_type.startswith(("fuse.sshfs", "nfs", "cifs", "smb", "fuse.s3", "9p"))


def stage_traces(traces: dict[str, Path], *, root: Path | None = None,
                 log: Callable[[str], None] | None = None) -> dict[str, Path]:
    """Copy each trace to local scratch if that is faster, and return the paths to use.

    Idempotent by size: a staged file of the right size is reused, so a second run of the study
    copies nothing. Any failure returns the ORIGINAL paths -- staging is an optimisation, and a
    full scratch disk must cost speed rather than the study.
    """
    say = log or (lambda _msg: None)
    root = root or scratch_root()
    if root is None:
        return traces
    if not any(is_slow_mount(p) for p in traces.values()):
        return traces

    staged_dir = root / "prefetcher-traces"
    out: dict[str, Path] = {}
    copied = 0
    try:
        staged_dir.mkdir(parents=True, exist_ok=True)
        for bench, source in traces.items():
            target = staged_dir / source.name
            if target.is_file() and target.stat().st_size == source.stat().st_size:
                out[bench] = target
                continue
            say(f"  staging {source.name} ({source.stat().st_size / 1e6:.0f} MB) to {staged_dir}")
            shutil.copy2(source, target)
            copied += 1
            out[bench] = target
    except OSError as exc:
        say(f"  staging failed ({exc}); reading traces from their original location")
        return traces

    if copied:
        say(f"  staged {copied} trace(s) to local disk; the repository mount is a network "
            f"filesystem and every simulation streams its whole trace")
    return out
