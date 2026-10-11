"""Finding the ChampSim binary and its source tree, naming every place looked when absent.

Order, most explicit first: an explicit argument, `$FLUX_CHAMPSIM_BIN`, then a name on PATH
(`pythia` is nixchip's package, which installs the sources under `$out/share/pythia`).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

#: Names to look for on PATH, best first. `pythia` links to the `multi multi no 1` build: the
#: L1D and L2 prefetchers are both chosen at run time.
ON_PATH = ("pythia", "perceptron-multi-multi-no-ship-1core", "perceptron-no-multi-no-ship-1core")


class ChampSimUnavailableError(RuntimeError):
    """No ChampSim binary (or source tree) could be found."""


def resolve_binary(binary: str | os.PathLike[str] | None = None) -> Path:
    """The ChampSim binary to run, or `ChampSimUnavailableError` naming every place looked."""
    tried: list[str] = []
    if binary is not None:
        path = Path(binary)
        if path.is_file():
            return path
        tried.append(f"binary= argument: {path}")
    env = os.environ.get("FLUX_CHAMPSIM_BIN")
    if env:
        path = Path(env)
        if path.is_file():
            return path
        tried.append(f"$FLUX_CHAMPSIM_BIN: {path}")
    else:
        tried.append("$FLUX_CHAMPSIM_BIN: unset")
    for name in ON_PATH:
        found = shutil.which(name)
        if found:
            return Path(found)
    tried.append(f"PATH: none of {', '.join(ON_PATH)}")
    raise ChampSimUnavailableError(
        "no ChampSim binary found. Looked at:\n  " + "\n  ".join(tried)
        + "\n`nix develop` puts nixchip's `pythia` on PATH; elsewhere set $FLUX_CHAMPSIM_BIN.")


def is_source_tree(path: Path) -> bool:
    return (path / "Makefile").is_file() and (path / "prefetcher" / "multi.l2c_pref").is_file()


def resolve_source_tree(explicit: str | os.PathLike[str] | None = None) -> Path:
    """The ChampSim SOURCE tree, for building. nixchip installs it beside the binary."""
    if explicit is not None:
        path = Path(explicit)
        if is_source_tree(path):
            return path
        raise ChampSimUnavailableError(f"{path} is not a ChampSim tree (no Makefile + prefetcher/multi.l2c_pref)")
    binary = resolve_binary()
    for candidate in (binary.resolve().parent.parent, binary.parent.parent):
        if is_source_tree(candidate):
            return candidate
    raise ChampSimUnavailableError(f"found a binary at {binary} but no source tree beside it; `nix develop` provides both")


_FINGERPRINTS: dict[tuple[str, int, int], str] = {}


def fingerprint(binary: str | os.PathLike[str]) -> str:
    """sha256 of the binary's bytes, memoised on (path, size, mtime)."""
    path = Path(binary).resolve()
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _FINGERPRINTS:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        _FINGERPRINTS[key] = h.hexdigest()
    return _FINGERPRINTS[key]
