class NotExpressibleError(ValueError):
    """Raised when a workload/architecture cannot be translated to this SystemC adapter's fixed
    mac_array_coarse shape — the same shape evaluators/rtl's mac_array.sv models, since this
    adapter is a coarse-grain pre-check for that exact design, not a general-purpose simulator.
    Mirrors every other adapter's NotExpressibleError and the Mapping IR's `not_expressible_in`
    (docs/ir.md): fail loudly, never silently approximate.
    """
