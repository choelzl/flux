# Raised when a workload/op/architecture cannot be translated to ZigZag's native
# representation. Mirrors the Mapping IR's `not_expressible_in` (docs/ir.md): adapters
# fail loudly here, they never silently approximate.
from flux_evaluator_abi import NotExpressibleError  # noqa: F401  -- the ABI's one refusal, shared by every backend (D426)
