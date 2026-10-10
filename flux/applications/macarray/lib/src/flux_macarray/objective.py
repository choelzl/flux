"""What a measurement means for a PE: its fmax from the measured worst path (D362)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Score:
    """One PE's standing on one stage."""

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


__all__ = ["Score"]
