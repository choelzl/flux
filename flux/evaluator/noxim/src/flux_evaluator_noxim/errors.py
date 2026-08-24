class NotExpressibleError(ValueError):
    """Raised when an architecture's `interconnect.noc` block isn't a 2D mesh shape this
    adapter's real Noxim integration can translate (`topology="mesh"`, `dimensions` a 2-element
    list, `routing_function="dim_order"`, `traffic` in `{"uniform", "transpose"}`, `packet_size`
    at least 2 flits — Noxim's own hard floor). Mirrors `evaluators/booksim`'s
    `NotExpressibleError` and the Mapping IR's `not_expressible_in` (docs/ir.md): fail loudly,
    never silently approximate or drop to a default that changes what's actually being measured.
    """
