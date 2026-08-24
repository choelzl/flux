"""What a measurement means for a PE: a frequency, an area, and the two together.

Two axes, as in every study here (D362): `fmax_mhz`, the highest clock the measured worst
path supports, against `area_um2`. A target clock turns the first into a constraint -- the
decision is the smallest PE that makes the target, the interconnect study's own rule -- and
the frontier is reported whole so a reader with a different clock can pick their own point.
Throughput is `lanes x fmax` MACs per second; per area it is the figure of merit the array
scales by, and it is derived here from measured numbers, never estimated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import PeConfig


@dataclass(frozen=True)
class Score:
    """One PE's standing on one rung."""

    area_um2: float
    worst_slack_ps: float
    clock_period_ps: float
    power_w: float
    cell_count: int
    latency_cycles: int
    flow_depth: str                  # "synthesis" (screen) or "placement" (confirmed)

    @property
    def path_ps(self) -> float:
        """The measured worst path: the period the design was constrained to, less its slack."""
        return self.clock_period_ps - self.worst_slack_ps

    @property
    def fmax_mhz(self) -> float:
        return 1e6 / self.path_ps if self.path_ps > 0 else float("inf")

    def meets(self, target_mhz: float | None, tolerance: float = 0.0) -> bool:
        """Within `tolerance` (a fraction) of the target. Placement's own noise is larger than
        the fraction of a megahertz by which two designs at "the same" clock differ."""
        return target_mhz is None or self.fmax_mhz >= target_mhz * (1.0 - tolerance)

    def to_dict(self) -> dict[str, Any]:
        return {"area_um2": round(self.area_um2, 2), "fmax_mhz": round(self.fmax_mhz, 1),
                "path_ps": round(self.path_ps, 1), "worst_slack_ps": round(self.worst_slack_ps, 1),
                "clock_period_ps": self.clock_period_ps, "power_w": self.power_w,
                "cell_count": self.cell_count, "latency_cycles": self.latency_cycles,
                "flow_depth": self.flow_depth}


@dataclass(frozen=True)
class Scored:
    config: PeConfig
    score: Score
    provenance: str = ""

    @property
    def label(self) -> str:
        return self.config.label

    @property
    def area_um2(self) -> float:
        return self.score.area_um2

    @property
    def fmax_mhz(self) -> float:
        return self.score.fmax_mhz


def gmacs_per_mm2(lanes: int, s: Score) -> float:
    """Throughput density: lanes x fmax, in GMAC/s, per mm^2 of PE."""
    if s.area_um2 <= 0 or s.fmax_mhz == float("inf"):
        return float("nan")
    return lanes * s.fmax_mhz * 1e6 / 1e9 / (s.area_um2 * 1e-6)


def frontier(points: list[Scored]) -> list[Scored]:
    """Every design faster than everything smaller, smallest first.

    Frequencies compared to the megahertz: two placements 0.3 MHz apart are the same clock,
    and a frontier that listed the bigger of them as "faster" was a rounding artefact.
    """
    from flux_frontier import frontier as _frontier

    return _frontier(points, better=lambda p: round(p.fmax_mhz), cost=lambda p: p.area_um2)


def spread(front: list[Scored], count: int, *, keep: list[Scored] = ()) -> list[Scored]:
    from flux_frontier import spread as _spread

    return _spread(front, count, keep=list(keep), cost=lambda p: p.area_um2)


def decide(points: list[Scored], target_mhz: float | None, *, tolerance: float = 0.0
           ) -> tuple[Scored | None, str]:
    """The smallest design that makes the target clock; without one, the fastest.

    Returns the pick and how it was picked, so the report can say which rule applied.
    `tolerance` is how far under the target still counts, as a fraction: a measured floor
    (the incumbent's own clock) is quoted with it, a requested one without.
    """
    from flux_decide import cheapest_meeting

    pick, rule = cheapest_meeting(points, cost=lambda p: p.area_um2,
                                  value=lambda p: p.fmax_mhz,
                                  floor=target_mhz, tolerance=tolerance)
    if rule == "nothing":
        return None, "nothing measured"
    if rule == "cheapest-meeting":
        within = f" (within {tolerance:.0%})" if tolerance else ""
        return pick, f"smallest area at >= {target_mhz:.0f} MHz{within}"
    if rule == "fallback-best-value":
        return pick, f"nothing reaches {target_mhz:.0f} MHz; the fastest measured"
    return pick, "the fastest measured (no target clock)"


__all__ = ["Score", "Scored", "decide", "frontier", "gmacs_per_mm2", "spread"]
