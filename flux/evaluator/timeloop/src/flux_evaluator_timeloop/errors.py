class NotExpressibleError(ValueError):
    """Raised when a workload/op/architecture cannot be translated to Timeloop's native
    representation. Mirrors evaluators/zigzag's NotExpressibleError and the Mapping IR's
    `not_expressible_in` (docs/ir.md): fail loudly, never silently approximate.
    """
