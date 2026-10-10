"""A PE's measurement as the study's `Score` (`steps mult-screen`); the measuring itself is the
application's rtl.py (D948)."""

from __future__ import annotations

from typing import Any

from .objective import Score


def pe_score(got: dict[str, Any], latency_cycles: int) -> Score:
    """The study's own score from what `rtl_check.measure` returns."""
    return Score(area_um2=float(got["area_um2"]), worst_slack_ps=float(got["worst_slack_ps"]),
                 clock_period_ps=float(got["clock_period_ps"]), power_w=float(got["power_w"]),
                 cell_count=int(got["cell_count"]), latency_cycles=int(latency_cycles),
                 flow_depth=str(got["flow_depth"]))


__all__ = ["pe_score"]
