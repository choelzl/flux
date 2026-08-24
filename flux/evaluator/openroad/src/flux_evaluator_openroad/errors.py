class OpenRoadError(RuntimeError):
    """A real tool failure (yosys or openroad exited nonzero, or output was unparseable)."""


# The candidate is outside this adapter's scope — same contract as every other adapter.
from flux_evaluator_abi import NotExpressibleError  # noqa: F401  -- the ABI's one refusal, shared by every backend (D426)
