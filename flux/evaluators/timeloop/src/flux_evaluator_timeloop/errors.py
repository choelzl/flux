class NotExpressibleError(ValueError):
    """Raised when a workload/op/architecture cannot be translated to Timeloop's native
    representation. Mirrors evaluators/zigzag's NotExpressibleError and the Mapping IR's
    `not_expressible_in` (docs/04.md §3.3): fail loudly, never silently approximate.
    """
