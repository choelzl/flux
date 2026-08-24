class NotExpressibleError(ValueError):
    """Raised when an architecture's `interconnect.noc` block isn't a k-ary n-cube shape this
    adapter's real Booksim2 integration can translate (uniform `dimensions`, `topology` in
    {"mesh", "torus"}) — mirrors every other adapter's NotExpressibleError and the Mapping IR's
    `not_expressible_in` (docs/ir.md): fail loudly, never silently approximate.
    """
