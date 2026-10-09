"""Measuring a PE on ASAP7: the synthesis screen and the placement stage, cached and parallel.

The screen synthesizes with Yosys and times the mapped netlist with OpenSTA, no placement: a
few seconds, optimistic by the wire cost but consistent, so it orders the space. The confirm
stage places with OpenROAD and estimates parasitics: tens of seconds, the number a report may
quote (D365). Both are cached by tool fingerprints and exact source, so a resumed run
re-measures nothing and a changed tool invalidates old entries.

Each measurement is a separate single-threaded process, so candidates run in a thread pool.
"""

from __future__ import annotations

from typing import Any

from .objective import Score
from .rtl import Design

SCREEN, CONFIRM = "synthesis", "placement"



def measure_one(design: Design, *, stage: str, clock_period_ps: float,
                timeout_s: float = 600.0) -> dict[str, Any]:
    """One PE through one stage via the application's `rtl.py` measure (clock and reset from the
    PE's header, placement repaired). A plain dict (cacheable), or `{"error": ...}`."""
    from .rtl_check import measure

    try:
        return measure(design.all_sources, design.module_name, stage="synth" if stage == SCREEN else "place",
                       clock_ps=clock_period_ps, repair_design=stage == CONFIRM, timeout_s=timeout_s)
    except (Exception, SystemExit) as exc:                                # noqa: BLE001 -- rtl.py says why by SystemExit
        return {"error": f"{type(exc).__name__}: {str(exc)[:400]}"}


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


__all__ = ["CONFIRM", "SCREEN", "measure_one", "pe_score", "tools_missing"]
