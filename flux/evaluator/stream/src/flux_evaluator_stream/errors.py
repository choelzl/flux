class NotExpressibleError(ValueError):
    """Raised when a workload/architecture cannot be translated to Stream's own real inputs.
    Mirrors every other adapter's NotExpressibleError: fail loudly, never silently approximate.
    """
