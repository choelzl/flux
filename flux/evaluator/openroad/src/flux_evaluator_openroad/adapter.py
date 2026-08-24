"""`OpenRoadEvaluator` — real physical-design PPA for the architecture's MAC datapath
(docs/decisions.md D225).

The Evaluator ABI's synthesis rung. The design it places is the **deterministic combinational
dot-product datapath** of `flux_generation.derive_design_spec` — the same (workload, arch)-derived
spec family the D223 generation loop verifies functionally — built here as its canonical
implementation (`acc = sum(a_i * w_i)`, one multiplier per lane at the workload's own declared
precision). No LLM anywhere: an evaluator must be deterministic, and the canonical implementation
is derivable from the spec alone.

Why not `mac_array.sv` (the design `evaluators/rtl` simulates): its operand memories are loaded
by the *testbench* via `$readmemh`, so under synthesis they are never-written and Yosys
constant-folds the whole datapath away — measured, LANES 8 vs 32 differed by ten cells. That
file is simulation-only; the derived datapath is what the architecture's compute array actually
costs in silicon: multipliers and an adder tree that scale with lanes and precision.

Reports **measured silicon numbers**: `area_mm2` from placed cell footprints, `power_w` from the
liberty power tables at a stated clock, plus `worst_slack_ps` (an extra-vocabulary metric, which
the ABI's Metric enum explicitly permits). Deliberately NOT reported: `latency_cycles`
(`evaluators/rtl`'s job, cycle-accurate) and `energy_pj` (power x another rung's runtime would be
that rung's number wearing this one's provenance). A campaign wanting all three escalates through
both rungs. The workload enters through the derived spec's own precision and lane count — the
same pair always places the identical design.
"""

from __future__ import annotations

import os

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

from .errors import NotExpressibleError
from .flow import run_ppa_flow

def _canonical_datapath_source(spec: dict) -> str:
    """The canonical implementation of a derived dot-product spec: `acc = sum(a_i * w_i)` over
    sized signed ports — the same shape the D223 holdout suite hand-writes as its known-honest
    module. Deterministic, spec-derived, LLM-free."""
    ports_sv = ",\n".join(
        f"  {'input' if p['dir'] == 'in' else 'output'} logic signed "
        f"[{p['bits'] - 1}:0] {p['name']}"
        for p in spec["ports"]
    )
    import re

    # ^a\d+$, not startswith("a") — the output port is named `acc` and a prefix test counted it
    # as a ninth lane (caught by reading the generated module, which referenced a8*w8).
    lanes = sum(1 for p in spec["ports"] if re.fullmatch(r"a\d+", p["name"]))
    terms = " + ".join(f"a{i} * w{i}" for i in range(lanes))
    return f"module {spec['module_name']} (\n{ports_sv}\n);\n  assign acc = {terms};\nendmodule\n"


class OpenRoadEvaluator:
    name = "openroad"

    def __init__(
        self,
        *,
        clock_period_ps: float = 2000.0,
        timeout_s: float = 600.0,
        flow_depth: str = "placement",
    ) -> None:
        """`flow_depth="routed"` (docs/decisions.md D229) adds real global+detailed routing and
        OpenRCX extraction (~90 s vs ~10 s per candidate for the 8-lane datapath) — timing and
        power then rest on extracted RC instead of placement estimates. Placement stays the
        default: it is the screening-grade physical number, and a campaign can hold both by
        registering two backends."""
        self.clock_period_ps = clock_period_ps
        self.timeout_s = timeout_s
        self.flow_depth = flow_depth
        # D147's pattern: env overrides for the binaries, PATH otherwise.
        self.yosys_bin = os.environ.get("YOSYS_BIN", "yosys")
        self.openroad_bin = os.environ.get("OPENROAD_BIN", "openroad")

    def evaluate(
        self, candidate: Candidate, budget: Budget, metrics: frozenset[str]
    ) -> Result:
        if candidate.mapping is not None:
            raise NotExpressibleError(
                "OpenRoadEvaluator evaluates the architecture's physical design; a mapping "
                "changes schedules, not silicon — pass mapping=None."
            )
        if not isinstance(candidate.arch, dict) or not isinstance(candidate.workload, dict):
            raise NotExpressibleError(
                "OpenRoadEvaluator derives the datapath from inline Workload + Architecture IR "
                "dicts (flux_generation.derive_design_spec's own scope)."
            )
        from flux_generation import DerivationError, derive_design_spec

        try:
            derived = derive_design_spec(candidate.workload, candidate.arch)
        except DerivationError as exc:
            raise NotExpressibleError(str(exc)) from exc
        lanes = derived.lanes
        arch_hash = derived.arch_hash

        report = run_ppa_flow(
            _canonical_datapath_source(derived.spec),
            derived.spec["module_name"],
            clock_port=None,  # combinational: a virtual clock constrains the ports
            clock_period_ps=self.clock_period_ps,
            flow_depth=self.flow_depth,
            yosys_bin=self.yosys_bin,
            openroad_bin=self.openroad_bin,
            timeout_s=self.timeout_s,
        )

        def _point(value: float, unit: str) -> Estimate:
            return Estimate(
                value=value, ci_low=value, ci_high=value, unit=unit, method=Method.MEASURED
            )

        result_metrics = {
            "area_mm2": _point(report.area_mm2, "mm^2"),
            "power_w": _point(report.power_total_w, "W"),
            "worst_slack_ps": _point(report.worst_slack_ps, "ps"),
        }

        workload_hash = derived.workload_hash
        return Result(
            metrics=result_metrics,
            validity=Validity(
                ok=report.worst_slack_ps >= 0.0,
                checker_version="openroad-placement-v0.1",
            ),
            domain=Domain(in_domain=False),
            bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
            provenance=Provenance(
                evaluator=f"openroad@asap7-{report.flow_depth}",
                inputs={
                    "workload_hash": workload_hash,
                    "arch_hash": arch_hash,
                    "lanes": str(lanes),
                    "clock_period_ps": str(self.clock_period_ps),
                    "flow_depth": report.flow_depth,
                    "cell_count": str(report.cell_count),
                },
            ),
            escalation=Escalation(recommended=False),
        )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        return [self.evaluate(c, budget, metrics) for c in candidates]
