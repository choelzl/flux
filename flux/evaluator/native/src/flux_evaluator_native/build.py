"""Builds this repo's own native Rust `flux-core` crate (docs/decisions.md D75) — a genuinely
different shape from every other adapter's `ensure_X_binary` here: there is no external
repository to clone. `core/` already lives in this repo, so this just runs
`cargo build --release --features python` against it and locates the compiled cdylib. `cargo`
itself caches incrementally, so repeat calls after the first are fast.
"""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path

# evaluators/native/src/flux_evaluator_native/build.py -> flux/core
_CORE_DIR = Path(__file__).resolve().parents[4] / "core"
_LIB_NAME = "flux_core"


class NativeBuildError(RuntimeError):
    """Raised when building the real `flux-core` crate fails — always carries the real subprocess
    stdout/stderr tail, never a bare 'build failed'.
    """


def _dylib_suffix() -> str:
    system = platform.system()
    if system == "Linux":
        return "so"
    if system == "Darwin":
        return "dylib"
    if system == "Windows":
        return "dll"
    raise NativeBuildError(f"unsupported platform for a compiled flux-core extension: {system!r}")


def ensure_native_extension(*, timeout_s: float = 300.0) -> Path:
    """Builds (or reuses cargo's own incremental cache for) the real `flux-core` cdylib with the
    `python` feature enabled, returning the path to the compiled shared library. The `python`
    feature is off by default (see `core/Cargo.toml`) so plain `cargo test` never needs to link
    libpython — only this call, building the actual Python-facing artifact, turns it on.
    """
    if not _CORE_DIR.exists():
        raise NativeBuildError(f"expected the flux-core crate at {_CORE_DIR}, found nothing")
    proc = subprocess.run(
        ["cargo", "build", "--release", "--features", "python"],
        cwd=_CORE_DIR, capture_output=True, text=True, timeout=timeout_s,
    )
    if proc.returncode != 0:
        raise NativeBuildError(
            f"cargo build of flux-core failed (exit={proc.returncode}).\n"
            f"--- stdout (tail) ---\n{proc.stdout[-4000:]}\n--- stderr (tail) ---\n{proc.stderr[-4000:]}"
        )
    built = _CORE_DIR / "target" / "release" / f"lib{_LIB_NAME}.{_dylib_suffix()}"
    if not built.exists():
        raise NativeBuildError(
            f"cargo build reported success but the expected artifact is missing: {built}"
        )
    return built
