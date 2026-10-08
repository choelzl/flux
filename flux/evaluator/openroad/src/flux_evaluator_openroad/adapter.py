"""`OpenRoadEvaluator`: physical-design PPA for the architecture's MAC datapath (D225).

Places the deterministic combinational dot-product datapath of
`derive.derive_design_spec` (`acc = sum(a_i * w_i)`, one multiplier per lane at the
workload's precision). No LLM: an evaluator must be deterministic.

Not `mac_array.sv`: its operand memories are loaded by the testbench via `$readmemh`, so under
synthesis Yosys constant-folds the datapath away.

Reports `area_mm2` (placed cell footprints), `power_w` (liberty tables at the stated clock) and
`worst_slack_ps`. Not `latency_cycles` (`evaluator/rtl`'s job) nor `energy_pj`, which would
mix another stage's runtime into this stage's provenance.
"""

from __future__ import annotations

import os
from pathlib import Path

from flux_evaluator_abi import (
    SequentialBatch,
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


def _binary_override(name: str) -> str:
    value = os.environ.get(name.upper() + "_BIN")
    if value is None:
        return name
    # nixchip exports <TOOL>_BIN as its bin/ directory; explicit commands still work.
    return str(Path(value) / name) if Path(value).is_dir() else value


def _canonical_datapath_source(spec: dict) -> str:
    """The canonical implementation of a derived dot-product spec: `acc = sum(a_i * w_i)` over
    sized signed ports."""
    ports_sv = ",\n".join(
        f"  {'input' if p['dir'] == 'in' else 'output'} logic signed "
        f"[{p['bits'] - 1}:0] {p['name']}"
        for p in spec["ports"]
    )
    import re

    # full match, not startswith("a"): the output port `acc` is not a lane
    lanes = sum(1 for p in spec["ports"] if re.fullmatch(r"a\d+", p["name"]))
    terms = " + ".join(f"a{i} * w{i}" for i in range(lanes))
    return f"module {spec['module_name']} (\n{ports_sv}\n);\n  assign acc = {terms};\nendmodule\n"


class OpenRoadEvaluator(SequentialBatch):
    name = "openroad"

    def __init__(
        self,
        *,
        clock_period_ps: float = 2000.0,
        timeout_s: float = 600.0,
        flow_depth: str = "placement",
    ) -> None:
        """`flow_depth="routed"` (D229) adds global+detailed routing and OpenRCX extraction
        (~9x slower), so timing and power use extracted RC. Placement is the default,
        screening-grade depth."""
        self.clock_period_ps = clock_period_ps
        self.timeout_s = timeout_s
        self.flow_depth = flow_depth
        # env overrides for the binaries, PATH otherwise (D147)
        self.yosys_bin = _binary_override("yosys")
        self.openroad_bin = _binary_override("openroad")

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
                "dicts (derive_design_spec's own scope)."
            )
        from .derive import DerivationError, derive_design_spec

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
