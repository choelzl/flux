# Raised when an architecture has no hierarchy entry declaring both a `floorplan` block and
# `attrs.power_w` (docs/decisions.md D64) — nothing for `evaluator/thermal`'s real 3D-ICE
# integration to build a floorplan from. Mirrors every other adapter's `NotExpressibleError`:
# fail loudly, never silently model an empty or fabricated die.
from flux_evaluator_abi import NotExpressibleError  # noqa: F401  -- the ABI's one refusal, shared by every backend (D426)
