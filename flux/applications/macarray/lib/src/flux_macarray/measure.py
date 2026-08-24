"""Measuring a PE on ASAP7: the synthesis screen and the placement stage, cached and parallel.

Two stages, one method (D365). The SCREEN synthesizes with Yosys and times the mapped netlist
with OpenSTA, no placement, no wires: a few seconds, optimistic by what the wires would cost,
identical in method across candidates, so it orders the space. The CONFIRM stage places the
netlist with OpenROAD and estimates parasitics from placement: tens of seconds, and the number
a report may quote. Both are keyed in one cache by the tool fingerprints and the exact source,
so a resumed run re-measures nothing and a changed tool makes old entries unreachable (D340).

Every measurement is a separate single-threaded process, so candidates run in a thread pool
sized like the interconnect study's escalation workers (D290).
"""

from __future__ import annotations

import hashlib
from typing import Any

from .objective import Score
from .rtl import Design

SCREEN, CONFIRM = "synthesis", "placement"


def _identity(design: Design, stage: str, clock_period_ps: float) -> str:
    """Source, stage and clock. `abc=full` (D564): the full ABC mapping is the flow now; a row
    cached under `abc -fast` is not this. `map=delay` (D571): ABC maps against the clock; the
    area mapping was measured as noise and is gone."""
    digest = hashlib.sha256(design.all_sources.encode()).hexdigest()[:16]
    return f"{design.module_name}@{digest}|{stage}|{clock_period_ps:.0f}ps|map=delay|abc=full"


def measure_one(design: Design, *, stage: str, clock_period_ps: float,
                timeout_s: float = 600.0) -> dict[str, Any]:
    """One PE through one stage. Returns a plain dict (cacheable) or `{"error": ...}`."""
    from flux_evaluator_openroad import run_ppa_flow, run_synthesis_flow

    kw = dict(clock_port="clk" if design.config.clocked else None,
              reset_port="rst_n" if design.config.clocked else None,
              clock_period_ps=clock_period_ps, timeout_s=timeout_s,
              map_for="delay")
    try:
        if stage == SCREEN:
            report = run_synthesis_flow(design.all_sources, design.module_name, **kw)
        elif stage == CONFIRM:
            report = run_ppa_flow(design.all_sources, design.module_name, flow_depth="placement",
                                  repair_design=True, **kw)
        else:
            raise ValueError(stage)
    except Exception as exc:                                              # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {str(exc)[:400]}"}
    return {**report.metrics(), "flow_depth": report.flow_depth,
            "critical_path": _critical_path(report.openroad_log_tail)}


def _critical_path(log_tail: str) -> str:
    """The startpoint/endpoint of the worst path, from the report the flow already printed."""
    start = end = ""
    for line in log_tail.splitlines():
        if line.strip().startswith("Startpoint:"):
            start = line.strip()[len("Startpoint:"):].strip()
        elif line.strip().startswith("Endpoint:"):
            end = line.strip()[len("Endpoint:"):].strip()
    return f"{start} -> {end}" if start or end else ""


def pe_score(got: dict[str, Any], latency_cycles: int) -> Score:
    """The study's own score from a raw measurement (what `measure_one` returns)."""
    return Score(area_um2=float(got["area_um2"]), worst_slack_ps=float(got["worst_slack_ps"]),
                 clock_period_ps=float(got["clock_period_ps"]), power_w=float(got["power_w"]),
                 cell_count=int(got["cell_count"]), latency_cycles=int(latency_cycles),
                 flow_depth=str(got["flow_depth"]))


def tools_missing() -> list[str]:
    """Which of the three tools this study needs are not on PATH."""
    from flux_evaluator_abi.preflight import missing_tools

    return list(missing_tools({"verilator": "verify", "yosys": "screen", "openroad": "confirm"}))


def toolchain() -> dict[str, str]:
    from flux_evaluator_abi.toolchain import toolchain_fingerprint

    return toolchain_fingerprint(("verilator", "yosys", "openroad"))


__all__ = ["CONFIRM", "SCREEN", "measure_one", "pe_score", "toolchain", "tools_missing"]
