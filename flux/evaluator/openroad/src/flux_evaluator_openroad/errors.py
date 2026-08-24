class OpenRoadError(RuntimeError):
    """A real tool failure (yosys or openroad exited nonzero, or output was unparseable)."""


class NotExpressibleError(ValueError):
    """The candidate is outside this adapter's scope — same contract as every other adapter."""
