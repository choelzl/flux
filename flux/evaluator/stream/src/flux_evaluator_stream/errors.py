# Raised when a workload/architecture cannot be translated to Stream's own real inputs.
# Mirrors every other adapter's NotExpressibleError: fail loudly, never silently approximate.
from flux_evaluator_abi import NotExpressibleError  # noqa: F401  -- the ABI's one refusal, shared by every backend (D426)
