class NotExpressibleError(ValueError):
    """Raised when an architecture has no hierarchy entry declaring both a `floorplan` block and
    `attrs.power_w` (docs/decisions.md D64) — nothing for `evaluators/thermal`'s real 3D-ICE
    integration to build a floorplan from. Mirrors every other adapter's `NotExpressibleError`:
    fail loudly, never silently model an empty or fabricated die.
    """
