"""Evaluator ABI adapter (docs/evaluator-abi.md): one ChampSim run per candidate.

    arch:     {"ini": "<knobs>", "types": ["a", "b"]}   or   {"header": "<C++ prefetcher>", "ini": ..., "types": [...]}
    workload: {"trace": path, "warmup_instructions": N, "simulation_instructions": M}

A header is built in (cached) and runs first among the types. Metrics: `ipc`, `cycles`,
`instructions` and the L2 prefetch counters `l2_pf_*`, all simulated.
"""

from __future__ import annotations

from flux_evaluator_abi import (
    Bottleneck, Budget, Candidate, Domain, Escalation, Estimate, Limiter, Method, NotExpressibleError,
    Provenance, Result, SequentialBatch, Validity,
)

from .binary import resolve_binary
from .build import build_header
from .run import L2_PREFETCH, simulate
from .study import split_ini

EVALUATOR_ID = "champsim@0.1"


class ChampSimEvaluator(SequentialBatch):
    name = "champsim"   # the registry name (flux_evaluator_abi.registry, D426)

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        arch, wl = candidate.arch, candidate.workload
        if not isinstance(arch, dict) or not ({"ini", "header", "types"} & set(arch)):
            raise NotExpressibleError("champsim needs an arch dict with `ini`, `header` and/or `types`")
        if not isinstance(wl, dict) or "trace" not in wl:
            raise NotExpressibleError("champsim needs a workload dict with a `trace` path")
        ini, types = split_ini(str(arch.get("ini", "")))
        types += list(arch.get("types", []))
        binary = resolve_binary()
        if arch.get("header"):
            got = build_header(str(arch["header"]))
            if not got.ok:
                raise NotExpressibleError(f"the prefetcher does not build: {got.first_error}")
            binary, types = got.binary, [got.name, *types]
        warmup = int(wl.get("warmup_instructions", 10_000_000))
        sim = int(wl.get("simulation_instructions", 15_000_000))
        ran = simulate(binary, ini, types, wl["trace"], warmup, sim,
                       timeout_s=budget.wall_clock_s if budget.wall_clock_s else None)

        def est(value: float, unit: str) -> Estimate:
            return Estimate(value=value, ci_low=value, ci_high=value, unit=unit, method=Method.SIMULATED)

        return Result(
            metrics={"ipc": est(ran["ipc"], "instructions_per_cycle"), "cycles": est(ran["cycles"], "cycles"),
                     "instructions": est(ran["instructions"], "instructions"),
                     **{f"l2_pf_{k}": est(ran[f"l2_pf_{k}"], "prefetches") for k in L2_PREFETCH}},
            validity=Validity(ok=True, checker_version=EVALUATOR_ID, violations=()),
            domain=Domain(in_domain=True),
            bottleneck=Bottleneck(limiter=Limiter.MEMORY),
            provenance=Provenance(
                evaluator=EVALUATOR_ID,
                inputs={"trace": str(wl["trace"]), "types": ",".join(types) or "none",
                        "warmup_instructions": str(warmup), "simulation_instructions": str(sim)},
                wall_clock_s=ran["wall_clock_s"]),
            escalation=Escalation(recommended=False),
        )
