class NotExpressibleError(ValueError):
    """Raised when a workload/op/architecture cannot be translated to ZigZag's native
    representation. Mirrors the Mapping IR's `not_expressible_in` (docs/ir.md): adapters
    fail loudly here, they never silently approximate.
    """
