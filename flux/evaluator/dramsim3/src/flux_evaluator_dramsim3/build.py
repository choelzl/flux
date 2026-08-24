"""Builds real DRAMsim3 (University of Maryland Memory-Systems Research, MIT — real, published
research: Li et al., "DRAMsim3: a Cycle-accurate, Thermal-Capable DRAM Simulator," IEEE Computer
Architecture Letters) on first use — cloned, never vendored, the same "fetch an external resource
once, cache it" pattern every other heavy-external-tool adapter here uses (evaluators/booksim,
/noxim, /cacti, /gem5, /thermal).

**One real, empirically-found build fix**, far simpler than any prior external-tool integration
in this repo (no legacy Fortran/BLAS, no ABI-width traps — a plain, modern CMake/C++ project):
DRAMsim3's own bundled `CMakeLists.txt` declares `cmake_minimum_required(VERSION 2.8)`, which
modern CMake (this environment's — confirmed 3.0+, DRAMsim3's own stated minimum) refuses outright
("Compatibility with CMake < 3.5 has been removed"). Fixed with CMake's own suggested escape
hatch, `-DCMAKE_POLICY_VERSION_MINIMUM=3.5` — no source file edited, no vendored patch, just the
flag CMake's own error message names.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_DRAMSIM3_REPO_URL = "https://github.com/umd-memsys/DRAMsim3.git"
# Pinned to a real commit, not the moving default branch — the same reproducibility reasoning
# docs/decisions.md D38/D66 already established for gem5/3D-ICE's own pins.
_DRAMSIM3_REF = "29817593b3389f1337235d63cac515024ab8fd6e"


class DramSim3BuildError(RuntimeError):
    """Raised when cloning or building real DRAMsim3 fails — always carries the real subprocess
    stdout/stderr tail, never a bare 'build failed'."""


def _run(cmd: list[str], *, cwd: Path, timeout_s: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)


def ensure_dramsim3_binary(work_dir: Path, *, timeout_s: float = 300.0) -> tuple[Path, Path]:
    """Clones and builds real DRAMsim3 under `work_dir`. Returns `(binary_path, configs_dir)` —
    the real `dramsim3main` executable and the directory of real, bundled, published DDR/LPDDR/
    GDDR/HBM/HMC timing configs (`architecture_translator.py` resolves a caller-named config
    against this directory; nothing here fabricates or edits a config).
    """
    repo_dir = work_dir / "dramsim3"
    clone = _run(
        ["git", "clone", _DRAMSIM3_REPO_URL, str(repo_dir)], cwd=work_dir, timeout_s=timeout_s,
    )
    if clone.returncode != 0:
        raise DramSim3BuildError(
            f"git clone of DRAMsim3 failed (exit={clone.returncode}).\n"
            f"--- stderr (tail) ---\n{clone.stderr[-4000:]}"
        )
    checkout = _run(["git", "checkout", _DRAMSIM3_REF], cwd=repo_dir, timeout_s=timeout_s)
    if checkout.returncode != 0:
        raise DramSim3BuildError(
            f"git checkout of pinned DRAMsim3 ref {_DRAMSIM3_REF!r} failed (exit={checkout.returncode}).\n"
            f"--- stderr (tail) ---\n{checkout.stderr[-4000:]}"
        )

    build_dir = repo_dir / "build"
    build_dir.mkdir(exist_ok=True)
    configure = _run(
        ["cmake", "..", "-DCMAKE_POLICY_VERSION_MINIMUM=3.5"], cwd=build_dir, timeout_s=timeout_s,
    )
    if configure.returncode != 0:
        raise DramSim3BuildError(
            f"cmake configure of DRAMsim3 failed (exit={configure.returncode}).\n"
            f"--- stdout (tail) ---\n{configure.stdout[-4000:]}\n--- stderr (tail) ---\n{configure.stderr[-4000:]}"
        )
    build = _run(["make", "-j4"], cwd=build_dir, timeout_s=timeout_s)
    binary_path = build_dir / "dramsim3main"
    if build.returncode != 0 or not binary_path.exists():
        raise DramSim3BuildError(
            f"DRAMsim3 build failed (exit={build.returncode}).\n"
            f"--- stdout (tail) ---\n{build.stdout[-4000:]}\n--- stderr (tail) ---\n{build.stderr[-4000:]}"
        )
    return binary_path, repo_dir / "configs"
