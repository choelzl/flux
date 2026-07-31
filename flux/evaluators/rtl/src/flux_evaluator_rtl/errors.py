class NotExpressibleError(ValueError):
    """Raised when a workload/architecture cannot be translated to this RTL adapter's fixed
    mac_array.sv shape. Mirrors evaluators/zigzag's and evaluators/timeloop's
    NotExpressibleError and the Mapping IR's `not_expressible_in` (docs/04.md §3.3): fail loudly,
    never silently approximate.
    """
