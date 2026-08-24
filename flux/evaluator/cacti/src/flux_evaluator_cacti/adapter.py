"""CACTI backend adapter implementing the Flux Evaluator ABI (docs/evaluator-abi.md,
docs/decisions.md D35/D36): real circuit-level SRAM area/energy/timing characterization via CACTI
7 (`HewlettPackard/cacti`). Licensing checked directly against source-file headers, not assumed —
there is no root `LICENSE`/`COPYING` file, but every `.cc`/`.h` file (verified: `basic_circuit.cc`)
carries "Copyright 2015 Hewlett-Packard Development Company, L.P." plus verbatim standard
3-clause-BSD redistribution terms ("redistributions of source code must retain... redistributions
in binary form must reproduce... neither the name of the copyright holders... may be used to
endorse or promote... without specific prior written permission") — permissive, same posture as
every other real dependency here (D21). Called through **CHIA's own
`chia.vlsi.sram_cacti.run_cacti`**, not a from-scratch subprocess wrapper —
the first evaluator in this repo that adapts a tool *via* CHIA's existing integration rather than
building a bespoke one, a genuinely different shape from `evaluators/booksim`/`evaluators/noxim`
(both wrap their external tool directly; CHIA has no NoC-simulator integration to reuse there).
`run_cacti` is a real `@ChiaFunction`, callable both locally (used here) and via
`.chia_remote()`.

Fills a real gap, not a redundant third memory model: neither `evaluators/zigzag` nor
`evaluators/timeloop` give any physically-grounded SRAM number — both cost memory access
analytically/parametrically. This is the first real circuit-level characterization in this repo.

v0.1 scope: `Candidate.arch` must describe exactly one SRAM macro (`architecture_translator.py`'s
`architecture_ir_to_sram_spec`), not a whole memory hierarchy — CACTI characterizes one physical
macro. `Candidate.workload` is required by the ABI and hashed into `Result.provenance`, but (like
`evaluators/booksim`) its content drives nothing — CACTI has no workload concept at all, only a
structural SRAM spec. `Candidate.mapping` must be `None` — no mapping concept either.

CACTI 7 itself is neither vendored nor pip-installed: `_ensure_cacti_binary` clones and builds it
on first use per `CactiEvaluator` instance, the same "fetch an external resource once, cache it"
shape every other real-tool adapter here uses. Needs `git`, `g++`/`gcc`, `make` on `PATH` — no
`flex`/`bison`/`cmake` gap this time (plain Makefile build, confirmed by actually building it).
"""

from __future__ import annotations

import subprocess
import tempfile
import threading
from pathlib import Path

import flux_ir
from chia.vlsi.sram_cacti.cacti_runner import run_cacti
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

from .architecture_translator import architecture_ir_to_sram_spec, architecture_ir_to_technology_um
from .errors import NotExpressibleError

_CACTI_REPO_URL = "https://github.com/HewlettPackard/cacti.git"


class CactiEvaluator:
    """Runs real CACTI 7 (via CHIA's own `chia.vlsi.sram_cacti.run_cacti`) to characterize one
    SRAM macro from a translated Architecture IR memory-class hierarchy node.
    """

    name = "cacti"

    def __init__(self, *, timeout_s: float = 120.0) -> None:
        self.timeout_s = timeout_s
        self._binary_path: Path | None = None
        self._build_lock = threading.Lock()

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        if not isinstance(candidate.arch, dict):
            raise NotExpressibleError(
                "CactiEvaluator v0.1 requires an inline Architecture IR dict as Candidate.arch "
                "with exactly one class=='memory' hierarchy node (see "
                "architecture_translator.py) — there is no fixed default SRAM to fall back to."
            )
        if not isinstance(candidate.workload, dict):
            raise NotExpressibleError(
                "CactiEvaluator v0.1 requires an inline Workload IR dict as Candidate.workload "
                "(no result-store hash resolution yet) — CACTI has no workload concept at all "
                "(see module docstring), but it's still required and hashed for provenance, "
                "same as every other evaluator here."
            )
        if candidate.mapping is not None:
            raise NotExpressibleError(
                "CactiEvaluator v0.1 does not use Mapping IR: CACTI characterizes a physical "
                "SRAM macro, which has no mapping concept — leave Candidate.mapping as None."
            )

        spec = architecture_ir_to_sram_spec(candidate.arch)
        technology_um = architecture_ir_to_technology_um(candidate.arch)
        workload_hash = flux_ir.content_hash(candidate.workload)
        arch_hash = flux_ir.content_hash(candidate.arch)

        binary_path = self._ensure_cacti_binary()
        cacti_result = run_cacti(spec, technology_um=technology_um, cacti_path=str(binary_path))
        if cacti_result is None:
            raise RuntimeError(
                f"CACTI failed or its output couldn't be parsed for SRAM spec {spec!r} "
                f"(technology_um={technology_um}) — see logs (chia.vlsi.sram_cacti logs a "
                "warning with CACTI's own stderr on failure)."
            )

        area_mm2 = cacti_result.height_mm * cacti_result.width_mm
        energy_pj = cacti_result.read_energy_nj * 1000.0
        power_w = cacti_result.leakage_power_mw / 1000.0

        result_metrics: dict[str, Estimate] = {}
        if not metrics or "area_mm2" in metrics:
            result_metrics["area_mm2"] = Estimate(
                value=area_mm2, ci_low=area_mm2, ci_high=area_mm2, unit="mm2", method=Method.SIMULATED,
            )
        if not metrics or "energy_pj" in metrics:
            result_metrics["energy_pj"] = Estimate(
                value=energy_pj, ci_low=energy_pj, ci_high=energy_pj, unit="pJ", method=Method.SIMULATED,
            )
        if not metrics or "power_w" in metrics:
            result_metrics["power_w"] = Estimate(
                value=power_w, ci_low=power_w, ci_high=power_w, unit="W", method=Method.SIMULATED,
            )

        return Result(
            metrics=result_metrics,
            validity=Validity(ok=True, checker_version="none (placeholder, same as every other adapter here)"),
            domain=Domain(in_domain=False, nearest_calibration=None),
            bottleneck=Bottleneck(
                limiter=Limiter.MEMORY,
                per_level_utilisation={
                    "access_time_ns": cacti_result.access_time_ns,
                    "cycle_time_ns": cacti_result.cycle_time_ns,
                },
            ),
            provenance=Provenance(
                evaluator="cacti7@real",
                inputs={
                    "workload_hash": workload_hash,
                    "arch_hash": arch_hash,
                    "sram": f"{spec.name}-{spec.depth}x{spec.width}b",
                    # The technology node this number is *for* (docs/decisions.md D147). CACTI's
                    # smallest usable node is 22nm — its `16nm.dat` contains the literal string
                    # "Invalid technology nodes" — while `synthesize_with_asap7` reports 7nm
                    # logic area. Those cannot be added together, and an unlabelled area invites
                    # exactly that. Recorded rather than enforced: nothing combines them today,
                    # and a guard against a caller that does not exist would be guessing at what
                    # it would look like.
                    "technology_um": technology_um,
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

    @staticmethod
    def _provided_cacti_binary() -> Path | None:
        """A prebuilt CACTI, if the environment supplies one (docs/decisions.md D146).

        `CACTI_BIN` first (nixchip's dev shell exports `PKGNAME_BIN` for every package it
        provides), then plain `cacti` on PATH. Falls through to the clone-and-build below when
        neither is present, so nothing that works today stops working.

        Only usable because nixchip's package now patches CACTI's own data lookups to absolute
        store paths — before that fix the binary segfaulted unless its `tech_params/` sat in the
        working directory (D145), which would have collided with the `cwd` convention CHIA's
        runner uses.
        """
        import os
        import shutil as _shutil

        candidates = []
        env_bin = os.environ.get("CACTI_BIN")
        if env_bin:
            candidates.append(Path(env_bin) / "cacti" if Path(env_bin).is_dir() else Path(env_bin))
        on_path = _shutil.which("cacti")
        if on_path:
            candidates.append(Path(on_path))
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        return None

    def _ensure_cacti_binary(self) -> Path:
        with self._build_lock:
            if self._binary_path is not None and self._binary_path.exists():
                return self._binary_path
            provided = self._provided_cacti_binary()
            if provided is not None:
                self._binary_path = provided
                return provided
            work_dir = Path(tempfile.mkdtemp(prefix="flux-cacti-build-"))
            repo_dir = work_dir / "cacti"
            clone_proc = subprocess.run(
                ["git", "clone", "--depth", "1", _CACTI_REPO_URL, str(repo_dir)],
                capture_output=True, text=True, timeout=self.timeout_s,
            )
            if clone_proc.returncode != 0:
                raise RuntimeError(
                    f"git clone of CACTI failed (exit={clone_proc.returncode}).\n"
                    f"--- stderr (tail) ---\n{clone_proc.stderr[-4000:]}"
                )
            build_proc = subprocess.run(
                ["make", "-j2"], capture_output=True, text=True, cwd=repo_dir, timeout=self.timeout_s,
            )
            binary_path = repo_dir / "cacti"
            if build_proc.returncode != 0 or not binary_path.exists():
                raise RuntimeError(
                    f"CACTI build failed (exit={build_proc.returncode}) — needs git, "
                    "g++/gcc, make on PATH; plain Makefile build, no flex/bison/cmake needed "
                    "(confirmed by actually building it, docs/decisions.md D35).\n"
                    f"--- stdout (tail) ---\n{build_proc.stdout[-4000:]}\n"
                    f"--- stderr (tail) ---\n{build_proc.stderr[-4000:]}"
                )
            self._binary_path = binary_path
            return binary_path
