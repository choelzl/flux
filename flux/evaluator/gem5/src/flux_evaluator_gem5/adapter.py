"""gem5 backend adapter implementing the Flux Evaluator ABI (docs/evaluator-abi.md,
docs/decisions.md D38): real cycle-accurate CPU simulation via gem5, called through **CHIA's own
`chia.simulators.gem5.Gem5Node`**, not a from-scratch subprocess wrapper — the second evaluator in
this repo adapted *through* an existing CHIA tool integration (after `evaluators/cacti`, D36).

**`Candidate.workload` is required by the ABI and hashed into `Result.provenance`, but its content
drives nothing** — the same honest gap `evaluators/booksim` already has for NoC traffic, for a
different underlying reason here: gem5 runs an actual *compiled program* on a modeled CPU, and
there is no Workload-IR -> compiled-RISC-V-binary translation anywhere in this repo (that would be
an actual compiler backend, a separate and much larger undertaking than an IR-to-config
translator). This adapter evaluates a **fixed, real benchmark** —
`tests/test-progs/hello/bin/riscv/linux/hello`, gem5's own bundled, pre-compiled RISC-V Linux test
binary (used by gem5's own CI) — against a *varying* CPU configuration, the same "fixed
representative design, varying architecture" posture `evaluators/rtl`/`evaluators/systemc` already
use for their fixed `mac_array` design. `latency_cycles` here means "cycles for gem5's own fixed
hello-world benchmark on this CPU config" — not comparable to ZigZag's/Timeloop's `latency_cycles`
for a Flux Workload IR document, the same "same metric name, different underlying quantity" trap
D37 already found and named for CACTI's `energy_pj`.

**A real, hard-won build saga, found empirically, not read off documentation.** Piecemeal fixes
(pinning just `PYTHON_CONFIG`, then just `CC`/`CXX`, then just `LD_LIBRARY_PATH`) each solved one
real, confirmed problem and immediately surfaced the next real one underneath — this sandbox has
two conflicting Python 3.12 installs (PATH ordering picks the broken one, breaking the build-time
Python-embedding step with `ModuleNotFoundError: No module named 'zlib'`), gem5's own default job
count triggered a real GCC 13 internal-compiler-error under memory pressure at `-j32` on this
64-core machine, and — only once run from inside `nix develop` rather than a plain shell — nix's
own `NIX_CFLAGS_COMPILE`/`NIX_LDFLAGS` injection breaks gem5's zlib detection regardless of which
compiler binary is named explicitly, and the ambient `g++` there resolves to nixpkgs' GCC 15.3.0,
which gem5's own `SConstruct` explicitly warns is unsupported (v11–v14.2). The one fix that
resolved *all* of these at once, confirmed directly: build with a **fully minimal, system-only
environment** (`PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`, `HOME`, and
`LD_LIBRARY_PATH=/lib/x86_64-linux-gnu` for the build-time marshal tool's own `libpython3.12.so`
resolution — a run-time dynamic-linker concern, separate from the Python-config compile-time one)
— not nix's compiler at all, not any nix-injected flag, the same toolchain this package's pinned
test numbers were verified against either way.

**This environment dict is passed directly to its own `subprocess.run` call, not through
`chia.simulators.gem5.Gem5Node.build_gem5`** (confirmed by reading its real signature: no `env`
override exists, only `extra_scons_args` — variable overrides, not environment ones). Mutating
*this whole process's* `os.environ` to work around the above (the first design tried) causes real
collateral damage instead: `build_gem5` is a `@ChiaFunction`, which auto-inits Ray as a side
effect, and Ray's own GCS-server subprocess is nix-built — it crashes at startup
(`Timed out waiting for file .../gcs_server_port...`) if the process environment is already
mutated when Ray first launches, and other nix-built subprocess machinery scons itself spawns
broke the same way even with Ray pre-initialized first (a real glibc-version mismatch,
`undefined symbol: __nptl_change_stack_perm, version GLIBC_PRIVATE`, confirmed reproducible).
A scoped `env=` dict passed to one explicit `subprocess.run` call — the same explicit-env pattern
every other adapter's build step in this repo already uses — never touches `os.environ` and
sidesteps all of it. `Gem5Node.run_gem5` (the actual simulation step, confirmed to need zero
environment overrides) is still used as-is — this is a build-step-only workaround, not an
abandonment of "adapt through CHIA."

Baked into `_ensure_gem5_binary`/`_scoped_build_env` below, not left as tribal knowledge or
caller-environment assumptions — this adapter's build should behave the same whether invoked from
a plain shell or from inside `nix develop`.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path

import flux_ir
from chia.simulators.gem5 import Gem5Node
from flux_evaluator_abi import (
    Bottleneck,
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)

from .architecture_translator import architecture_ir_to_gem5_config_args
from .errors import NotExpressibleError

_GEM5_REPO_URL = "https://github.com/gem5/gem5.git"
# Pinned to gem5's real latest tagged release, not the moving default branch — a real,
# verified-the-hard-way finding (docs/decisions.md D38): an earlier unpinned `--depth 1` clone of
# HEAD reproduced different real cycle counts on two different days (upstream gem5 moved between
# builds), so the "fully deterministic, a fresh build+run reproduces the exact pinned numbers"
# claim this adapter's tests make is only true when the clone itself is pinned.
_GEM5_REF = "v25.1.0.1"
_BENCHMARK_RELATIVE_PATH = "tests/test-progs/hello/bin/riscv/linux/hello"
_CONFIG_SCRIPT_RELATIVE_PATH = "configs/deprecated/example/se.py"
_GEM5_ISA = "riscv"
_GEM5_VARIANT = "opt"
# The real, empirically-found-safe job count (see module docstring) — gem5's own default
# (cpu_count // 2) triggered a real GCC 13 ICE on this 64-core machine at -j32.
_BUILD_JOBS = 8
# The real, found-working minimal build environment (see module docstring): plain system PATH —
# no nix compiler/flag injection, no broken vendored Python install ahead of the working system
# one — plus the one addition gem5's build-time marshal tool needs at run time (its own
# `libpython3.12.so` dynamic-linker resolution, separate from any compile-time concern).
_BUILD_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_RUNTIME_LIB_DIR = "/lib/x86_64-linux-gnu"


def _scoped_build_env() -> dict[str, str]:
    """A minimal, fully system-only environment for gem5's build subprocess — never derived from
    or mutating this process's own `os.environ` (module docstring: that broke Ray's own
    subprocess machinery when tried). Passed explicitly to a direct `subprocess.run` call, not
    through `Gem5Node.build_gem5` (which has no `env` override).
    """
    return {
        "PATH": _BUILD_PATH,
        "HOME": os.environ.get("HOME", "/root"),
        "LD_LIBRARY_PATH": _RUNTIME_LIB_DIR,
    }


class Gem5Evaluator:
    """Runs a real gem5 simulation of a fixed benchmark on a CPU config translated from
    Architecture IR — via CHIA's own `Gem5Node`, the second evaluator here (after
    `evaluators/cacti`) adapted through an existing CHIA tool integration.
    """

    name = "gem5"

    def __init__(self, *, build_timeout_s: float = 3600.0, run_timeout_s: float = 300.0) -> None:
        self.build_timeout_s = build_timeout_s
        self.run_timeout_s = run_timeout_s
        self._repo_dir: Path | None = None
        self._binary_path: Path | None = None
        self._build_lock = threading.Lock()
        self._node = Gem5Node(require_colocated=False)

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        if not isinstance(candidate.arch, dict):
            raise NotExpressibleError(
                "Gem5Evaluator v0.1 requires an inline Architecture IR dict as Candidate.arch "
                "with a single class=='compute' RISC-V node (see architecture_translator.py) — "
                "there is no fixed default CPU config to fall back to."
            )
        if not isinstance(candidate.workload, dict):
            raise NotExpressibleError(
                "Gem5Evaluator v0.1 requires an inline Workload IR dict as Candidate.workload "
                "(no result-store hash resolution yet) — its content isn't used to drive the "
                "simulated program (see module docstring: gem5 runs a fixed real benchmark, not "
                "a Workload-IR-derived one), but it's still required and hashed for provenance, "
                "same as every other evaluator here."
            )
        if candidate.mapping is not None:
            raise NotExpressibleError(
                "Gem5Evaluator v0.1 does not use Mapping IR: gem5's CPU simulation has no "
                "mapping concept — leave Candidate.mapping as None."
            )

        config_args = architecture_ir_to_gem5_config_args(candidate.arch)
        workload_hash = flux_ir.content_hash(candidate.workload)
        arch_hash = flux_ir.content_hash(candidate.arch)

        repo_dir, binary_path = self._ensure_gem5_binary()
        benchmark = repo_dir / _BENCHMARK_RELATIVE_PATH
        config_script = repo_dir / _CONFIG_SCRIPT_RELATIVE_PATH

        with tempfile.TemporaryDirectory(prefix=f"flux-gem5-run-{arch_hash[:12]}-") as outdir:
            run_result = self._node.run_gem5(
                str(binary_path), str(config_script), outdir,
                config_args=[*config_args, "--cmd", str(benchmark)],
                timeout_s=self.run_timeout_s,
            )
        if run_result.status != "ok":
            raise RuntimeError(
                f"gem5 run failed (status={run_result.status}): {run_result.error_messages}\n"
                f"--- stdout (tail) ---\n{run_result.stdout_tail}"
            )

        result_metrics: dict[str, Estimate] = {}
        if not metrics or "latency_cycles" in metrics:
            cycles = float(run_result.num_cycles)
            result_metrics["latency_cycles"] = Estimate(
                value=cycles, ci_low=cycles, ci_high=cycles, unit="cycles", method=Method.SIMULATED,
            )

        ipc = (
            run_result.sim_insts / run_result.num_cycles
            if run_result.num_cycles else None
        )
        return Result(
            metrics=result_metrics,
            validity=Validity(ok=True, checker_version="none (placeholder, same as every other adapter here)"),
            domain=Domain(in_domain=False, nearest_calibration=None),
            bottleneck=Bottleneck(
                limiter=Limiter.COMPUTE,
                per_level_utilisation={
                    "sim_insts": float(run_result.sim_insts),
                    **({"ipc": ipc} if ipc is not None else {}),
                },
            ),
            provenance=Provenance(
                evaluator="gem5@real",
                inputs={
                    "workload_hash": workload_hash,
                    "arch_hash": arch_hash,
                    "benchmark": _BENCHMARK_RELATIVE_PATH,
                    "config_args": " ".join(config_args),
                },
            ),
            escalation=Escalation(recommended=False),
        )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        # v0.1: sequential, same note as every other adapter here — the ABI's batch *interface*
        # is satisfied; batch *performance* is a Phase 3 concern (docs/roadmap.md).
        return [self.evaluate(c, budget, metrics) for c in candidates]

    def _ensure_gem5_binary(self) -> tuple[Path, Path]:
        with self._build_lock:
            if (
                self._repo_dir is not None
                and self._binary_path is not None
                and self._binary_path.exists()
            ):
                return self._repo_dir, self._binary_path
            work_dir = Path(tempfile.mkdtemp(prefix="flux-gem5-build-"))
            repo_dir = work_dir / "gem5"
            clone_proc = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", _GEM5_REF, _GEM5_REPO_URL, str(repo_dir)],
                capture_output=True, text=True, timeout=self.build_timeout_s,
            )
            if clone_proc.returncode != 0:
                raise RuntimeError(
                    f"git clone of gem5 failed (exit={clone_proc.returncode}).\n"
                    f"--- stderr (tail) ---\n{clone_proc.stderr[-4000:]}"
                )
            # A direct, explicit subprocess call for the build step — not `Gem5Node.build_gem5`
            # (module docstring: its real signature has no `env` override, so fixing this
            # sandbox's real build gotchas would otherwise mean mutating this whole process's
            # `os.environ`, which broke Ray's and scons's own nix-built subprocess machinery).
            # `Gem5Node.run_gem5` (below, in `evaluate()`) is still used as-is for the actual
            # simulation step, confirmed to need none of these workarounds. No CC/CXX/PYTHON_
            # CONFIG overrides needed either — `_scoped_build_env()`'s plain system PATH already
            # resolves to the right toolchain naturally, nothing nix-injected to fight.
            target = f"build/{_GEM5_ISA.upper()}/gem5.{_GEM5_VARIANT}"
            build_proc = subprocess.run(
                ["scons", target, f"-j{_BUILD_JOBS}"],
                cwd=repo_dir, capture_output=True, text=True, timeout=self.build_timeout_s,
                env=_scoped_build_env(),
            )
            binary_path = repo_dir / target
            if build_proc.returncode != 0 or not binary_path.exists():
                raise RuntimeError(
                    f"gem5 build failed (exit={build_proc.returncode}) — needs git, g++, make, "
                    "scons on the plain system PATH; see module docstring for the real, found "
                    "build gotchas this adapter already works around by using a minimal, "
                    "system-only build environment.\n"
                    f"--- stdout (tail) ---\n{build_proc.stdout[-4000:]}\n"
                    f"--- stderr (tail) ---\n{build_proc.stderr[-4000:]}"
                )
            self._repo_dir = repo_dir
            self._binary_path = binary_path
            return repo_dir, binary_path
