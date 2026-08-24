"""Finding the ChampSim binary, and saying something useful when it is not there.

Its own module because resolution order is a decision, not a detail. The simulator is not vendored
and cannot be: it is a 12 MB build artifact of a fork whose `.gitignore` excludes `bin/`. So the
evaluator has to look in several places, and — more importantly — has to FAIL WELL when it finds
nothing, because "no such file" six layers down a search is the least actionable error this
repository can produce.

Order, most explicit first:

  1. an explicit `binary=` argument                   — a caller that already knows
  2. `$FLUX_CHAMPSIM_BIN`                             — an operator override
  3. `pythia` (or either `perceptron-*` name) on PATH — nixchip's package

There used to be a fourth: an in-tree `applications/prefetcher/proj/pythia/bin/` build, carried
because the simulator had no package and could not be committed. `nixchip.packages.pythia` builds
CMU-SAFARI/Pythia at the MICRO'21 fork and installs the whole tree under `$out/share/pythia`, so
`nix develop .#python` supplies both a binary and the sources the build loop needs. The project
tree is gone and the fallback with it — a resolution path that can no longer succeed is worse than
none, because it turns "no simulator on PATH" into a longer error that suggests a directory nobody
has.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

#: What `build_champsim.sh no multi no 1` produces. The name encodes the build: perceptron branch
#: predictor, no L1D prefetcher, `multi` in the L2C slot, no LLC prefetcher, ship LLC replacement,
#: one core. The `multi` part is the one that matters — it is what makes the prefetcher selectable
#: at RUN time via `--l2c_prefetcher_types`, so this search never needs a compiler.
BINARY_NAME = "perceptron-no-multi-no-ship-1core"

#: Names to look for on PATH, best first. `pythia` is what nixchip's package installs (a symlink
#: to the `multi multi no 1` build); the two long names are what `build_champsim.sh` produces for
#: the L1D-enabled and L1D-disabled configurations. The multi-multi build is preferred because it
#: reaches the L1D prefetcher axis as well, and it reproduces the recorded no-prefetcher baselines
#: EXACTLY (0.69602 / 0.99071 / 0.80232, zero delta) — checked before it was adopted, because a
#: simulator that shifts the denominator invalidates every speedup this study has recorded.
ON_PATH = ("pythia", "perceptron-multi-multi-no-ship-1core", BINARY_NAME)


class ChampSimUnavailableError(RuntimeError):
    """No ChampSim binary could be found. Carries every place that was looked."""


def resolve_binary(binary: str | os.PathLike[str] | None = None) -> Path:
    """The ChampSim binary to run, or `ChampSimUnavailableError` naming all four candidates."""
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

    for candidate in ON_PATH:
        found = shutil.which(candidate)
        if found:
            return Path(found)
    tried.append(f"PATH: none of {', '.join(ON_PATH)}")

    raise ChampSimUnavailableError(
        "no ChampSim binary found. Looked at:\n  " + "\n  ".join(tried)
        + "\n\nThe simulator comes from nix: `nix develop .#python` puts nixchip's "
          "`pythia` package on PATH. Outside that shell, point $FLUX_CHAMPSIM_BIN at a "
          "`build_champsim.sh`-produced binary."
    )


def resolve_trace(trace: str | os.PathLike[str]) -> Path:
    """A trace path, checked to exist before a six-minute run is started on it."""
    path = Path(trace)
    if not path.is_file():
        raise FileNotFoundError(
            f"trace not found: {path}. Traces are not in git (380 MB); see "
            "applications/prefetcher/traces/README.md for where they come from."
        )
    return path


def resolve_source_tree(explicit: str | os.PathLike[str] | None = None) -> Path:
    """The ChampSim SOURCE tree, for anything that needs to BUILD rather than run.

    The generated-prefetcher loop needs sources, not just a binary: it writes a header, patches the
    dispatch and rebuilds. nixchip installs the whole tree beside the binary, so the binary's own
    location finds it -- `$out/bin/pythia` is a symlink into `$out/share/pythia/bin/`.
    """
    if explicit is not None:
        path = Path(explicit)
        if (path / "build_champsim.sh").is_file():
            return path
        raise ChampSimUnavailableError(f"{path} is not a ChampSim tree (no build_champsim.sh)")

    binary = resolve_binary()
    for candidate in (binary.resolve().parent.parent, binary.parent.parent):
        if (candidate / "build_champsim.sh").is_file():
            return candidate
    raise ChampSimUnavailableError(
        f"found a binary at {binary} but no source tree beside it. The generated-prefetcher loop "
        "needs sources to rebuild; `nix develop .#python` provides both.")
