# Raised when a workload/op/architecture cannot be expressed in Timeloop:
# fail loudly, never silently approximate.
from flux_evaluator_abi import NotExpressibleError  # noqa: F401  -- the ABI's one refusal, shared by every backend (D426)
