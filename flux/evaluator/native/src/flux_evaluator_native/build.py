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

# evaluator/native/src/flux_evaluator_native/build.py -> flux/core/rust (docs/decisions.md D335).
# The `rust` segment is not decoration: the reorganisation around the four module types (D296)
# turned `core/` from the crate root into a directory of them — `core/ir`, `core/llm`,
# `core/stores`, `core/rust` — and this path was not moved with it.
_CORE_DIR = Path(__file__).resolve().parents[4] / "core" / "rust"
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
    # The MANIFEST, not the directory. `core/` still existed after the crate moved into
    # `core/rust/`, so this guard passed and cargo ran in a directory with no `Cargo.toml` —
    # turning a clear "expected the flux-core crate at X" into cargo's own "could not find
    # Cargo.toml in ... or any parent directory", which reads like a path-arithmetic bug and
    # sent the first diagnosis after the wrong parent index.
    if not (_CORE_DIR / "Cargo.toml").is_file():
        raise NativeBuildError(
            f"expected the flux-core crate at {_CORE_DIR}, found no Cargo.toml there")
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
