# Raised when an architecture's `interconnect.noc` block isn't a k-ary n-cube shape this
# adapter's real Booksim2 integration can translate (uniform `dimensions`, `topology` in
# {"mesh", "torus"}) — mirrors every other adapter's NotExpressibleError and the Mapping IR's
# `not_expressible_in` (docs/ir.md): fail loudly, never silently approximate.
from flux_evaluator_abi import NotExpressibleError  # noqa: F401  -- the ABI's one refusal, shared by every backend (D426)
