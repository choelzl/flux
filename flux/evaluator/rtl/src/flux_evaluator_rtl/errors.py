# Raised when a workload/architecture cannot be translated to this RTL adapter's fixed
# mac_array.sv shape: fail loudly, never silently approximate.
from flux_evaluator_abi import NotExpressibleError  # noqa: F401  -- the ABI's one refusal, shared by every backend (D426)
