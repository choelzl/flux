"""Builds real 3D-ICE (EPFL ESL, GPLv3 — https://github.com/esl-epfl/3d-ice) on first use.
Neither vendored nor a nix derivation — cloned and built here, the same "fetch an external
resource once, cache it" pattern every other heavy-external-tool adapter in this repo uses
(evaluators/booksim, /noxim, /cacti, /gem5). See docs/decisions.md D64 for the full build-friction
story; this module is where the fixes actually live, matching the split `evaluators/gem5` already
established (real workarounds live in the adapter's own build helper, not in flake.nix).

**Three real, empirically-found build fixes, applied here:**

1. SuperLU_MT 4.0.0 (3D-ICE's own bundled sparse-solver dependency, shipped as a zip inside the
   3D-ICE repo, not a separate fetch) is legacy C from ~2009 — GCC 15's stricter C23-by-default
   rejects its K&R-style unprototyped declarations (`char *getenv();`) as hard "conflicting types"
   errors, not warnings. Fixed with `-std=gnu89`.
2. SuperLU_MT's own top-level `make` (no target given) builds all four precisions (single/double/
   complex/complex16) as *parallel* prerequisites of `all:`, and every one of them `ar`-appends to
   the *same* shared `.a` archive — under this environment's ambient `MAKEFLAGS=-j32`, that's a
   real, reproducible race (`ranlib: ... file truncated`, confirmed by direct repro: forcing
   `-j1` fixes it every time, restoring the race reproduces it every time). `-j1` is passed
   explicitly and `MAKEFLAGS` is stripped from the subprocess environment, not merely overridden
   on the command line (make's own env-derived `MAKEFLAGS` can still leak into sub-makes
   otherwise).
3. nixpkgs' default `openblas` is built **ILP64** (64-bit BLAS integers) — SuperLU_MT's own C code
   declares plain 32-bit `int*` BLAS parameters (`extern void dgemv_(char*, int*, int*, ...)`),
   the real, standard LP64 Fortran ABI. Linked against ILP64 openblas, the top 32 bits of every
   dimension/stride argument are read as garbage, which manifests as either a "parameter had an
   illegal value" rejection (caught, safe) or — inside SuperLU_MT's own multithreaded factorization
   path — a real segfault deep inside openblas's own vectorized kernel (confirmed with `gdb`, not
   guessed: `dgemv_n_ZEN` inside `libopenblas.so`, called from `pdgstrf_snode_bmod`). Fixed by
   linking against **`pkgs.openblasCompat`** (nixpkgs' real LP64 build) instead — confirmed via a
   standalone `dgemv_` call before ever touching SuperLU_MT: garbage/crash against `openblas`,
   the correct numeric result against `openblasCompat`, both from the exact same test program.

**A fourth real, empirically-found fix**: the bundled `superlu_mt-4.0.0.zip` itself ships with
stale, pre-built `INSTALL/*.o` object files and test binaries — real leftover build artifacts from
whoever last built and zipped it upstream, for **ARM aarch64**, not this environment's x86-64
(confirmed with `file`, not guessed: `ELF 64-bit LSB relocatable, ARM aarch64`). Because their
mtimes survive the zip extraction and can end up newer than the `.c` sources next to them, `make`
considers them already-built and links the wrong-architecture `.o` straight into `testtimer`,
which fails with a real, if cryptic, linker error (`Relocations in generic ELF ... file in wrong
format`) — fixed by running the bundled `INSTALL/Makefile`'s own `clean` target immediately after
extracting the zip, before anything else touches that directory.

None of `-I`/`-L` for `openblasCompat`'s headers/library need to be resolved by this module at
all: `flake.nix`'s `.#default` shell lists `pkgs.openblasCompat` as a `mkShell` package, and
`nix develop` (unlike a bare `nix shell`, checked directly — they differ) auto-injects
`NIX_CFLAGS_COMPILE`/`NIX_LDFLAGS` pointing at it into every `gcc` invocation this module's own
subprocess calls make, the same mechanism `evaluators/gem5`'s own module docstring already
documents (there, that injection was a problem to route around; here it's exactly what's wanted,
a real, opposite-direction use of the same nix behavior). The built `3D-ICE-Emulator` binary
needs no `LD_LIBRARY_PATH` at run time either — nix's linker wrapper embeds a real `RPATH` for
every `-L` path it injects, confirmed by running the freshly-built binary with the environment
stripped back to a plain, non-nix shell and getting the identical reference numbers.
"""

from __future__ import annotations

import os
import re
import subprocess
import zipfile
from pathlib import Path

_3DICE_REPO_URL = "https://github.com/esl-epfl/3d-ice.git"
# Pinned to a real commit, not the moving default branch — the same reproducibility reasoning
# docs/decisions.md D38 already established for evaluators/gem5's own pin.
_3DICE_REF = "4953952a1ef6d38807ff307212a6f15e5b2ef935"
_SUPERLU_VERSION = "4.0.0"


class ThermalBuildError(RuntimeError):
    """Raised when cloning or building real 3D-ICE fails — always carries the real subprocess
    stdout/stderr tail, never a bare 'build failed'."""


def _run(cmd: list[str], *, cwd: Path, timeout_s: float, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s, env=env)


def _build_env() -> dict[str, str]:
    """The ambient environment, with exactly one change: `MAKEFLAGS` stripped (see module
    docstring, fix #2) — otherwise inherited as-is, deliberately, so real nix-injected
    `NIX_CFLAGS_COMPILE`/`NIX_LDFLAGS` reach every `gcc` call this module makes (fix #3).
    """
    env = dict(os.environ)
    env.pop("MAKEFLAGS", None)
    return env


def _sed(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text()
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ThermalBuildError(f"expected exactly one match for {pattern!r} in {path}, found {count}")
    path.write_text(new_text)


def ensure_3dice_binary(work_dir: Path, *, timeout_s: float = 300.0) -> Path:
    """Clones and builds real 3D-ICE under `work_dir` (a fresh, caller-owned directory — callers
    cache this per-instance, the same pattern `evaluators/cacti`/`evaluators/gem5` use). Returns
    the path to the real, built `3D-ICE-Emulator` binary.
    """
    env = _build_env()
    repo_dir = work_dir / "3d-ice"

    clone = _run(
        ["git", "clone", _3DICE_REPO_URL, str(repo_dir)], cwd=work_dir, timeout_s=timeout_s, env=env,
    )
    if clone.returncode != 0:
        raise ThermalBuildError(
            f"git clone of 3D-ICE failed (exit={clone.returncode}).\n--- stderr (tail) ---\n{clone.stderr[-4000:]}"
        )
    checkout = _run(["git", "checkout", _3DICE_REF], cwd=repo_dir, timeout_s=timeout_s, env=env)
    if checkout.returncode != 0:
        raise ThermalBuildError(
            f"git checkout of pinned 3D-ICE ref {_3DICE_REF!r} failed (exit={checkout.returncode}).\n"
            f"--- stderr (tail) ---\n{checkout.stderr[-4000:]}"
        )

    zip_path = repo_dir / f"superlu_mt-{_SUPERLU_VERSION}.zip"
    if not zip_path.exists():
        raise ThermalBuildError(f"3D-ICE's own bundled {zip_path.name} is missing from the clone at {repo_dir}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(repo_dir)

    superlu_dir = repo_dir / f"superlu_mt-{_SUPERLU_VERSION}"

    # Fix #4 (stale, wrong-architecture prebuilt objects bundled in the zip — see module
    # docstring). INSTALL/Makefile's own `clean` target does exactly the right thing.
    install_clean = _run(["make", "clean"], cwd=superlu_dir / "INSTALL", timeout_s=timeout_s, env=env)
    if install_clean.returncode != 0:
        raise ThermalBuildError(
            f"cleaning SuperLU_MT's bundled INSTALL/ dir failed (exit={install_clean.returncode}).\n"
            f"--- stderr (tail) ---\n{install_clean.stderr[-4000:]}"
        )

    make_inc_template = superlu_dir / "MAKE_INC" / "make.linux.openmp"
    make_inc = superlu_dir / "make.inc"
    make_inc.write_text(make_inc_template.read_text())

    # Fix #3 (ILP64 -> LP64): USE_VENDOR_BLAS + -lopenblas, no explicit -L — ambient NIX_LDFLAGS
    # (from flake.nix's pkgs.openblasCompat) already supplies the right one, see module docstring.
    _sed(make_inc, r"^BLASDEF.*$", "BLASDEF   = -DUSE_VENDOR_BLAS")
    _sed(make_inc, r"^BLASLIB .*$", "BLASLIB = -lopenblas")
    # Fix: Fortran symbol naming — SuperLU_MT's own default (-DNoChange, i.e. no trailing
    # underscore) doesn't match openblasCompat's real Fortran-ABI export names (dgemm_, dtrsv_,
    # ...) — -DAdd_ (trailing underscore) does, confirmed by the real "undefined reference to
    # `dgemm`/`dtrsv`" link failure this replaces, resolved.
    _sed(make_inc, r"^CDEFS.*$", "CDEFS        = -DAdd_")
    # Fix #1 (legacy K&R C under GCC 15's C23 default).
    _sed(make_inc, r"^CC\s+= gcc -fopenmp$", "CC           = gcc -fopenmp -std=gnu89")

    # Fix #2 (the real ar/ranlib race under ambient MAKEFLAGS=-j32) — -j1 explicit, MAKEFLAGS
    # already stripped from `env` above.
    slu_build = _run(["make", "-j1"], cwd=superlu_dir, timeout_s=timeout_s, env=env)
    if slu_build.returncode != 0:
        raise ThermalBuildError(
            f"SuperLU_MT build failed (exit={slu_build.returncode}).\n"
            f"--- stdout (tail) ---\n{slu_build.stdout[-4000:]}\n--- stderr (tail) ---\n{slu_build.stderr[-4000:]}"
        )
    superlu_lib = superlu_dir / "lib" / "libsuperlu_mt_OPENMP.a"
    if not superlu_lib.exists():
        raise ThermalBuildError(f"SuperLU_MT build reported success but {superlu_lib} is missing")

    makefile_def = repo_dir / "makefile.def"
    _sed(makefile_def, r"-L/lib/x86_64-linux-gnu -lopenblas", "-lopenblas")

    bin_build = _run(["make", "-j1", "bin"], cwd=repo_dir, timeout_s=timeout_s, env=env)
    emulator = repo_dir / "bin" / "3D-ICE-Emulator"
    if bin_build.returncode != 0 or not emulator.exists():
        raise ThermalBuildError(
            f"3D-ICE build failed (exit={bin_build.returncode}).\n"
            f"--- stdout (tail) ---\n{bin_build.stdout[-4000:]}\n--- stderr (tail) ---\n{bin_build.stderr[-4000:]}"
        )
    return emulator
