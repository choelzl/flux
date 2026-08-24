# Raised when a workload/op/architecture cannot be translated to Timeloop's native
# representation. Mirrors evaluator/zigzag's NotExpressibleError and the Mapping IR's
# `not_expressible_in` (docs/ir.md): fail loudly, never silently approximate.
from flux_evaluator_abi import NotExpressibleError  # noqa: F401  -- the ABI's one refusal, shared by every backend (D426)
