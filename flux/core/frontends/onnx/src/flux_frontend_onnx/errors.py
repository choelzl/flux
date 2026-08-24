class NotExpressibleError(ValueError):
    """Raised when an ONNX graph cannot be translated to Flux Workload IR. Mirrors every other
    Flux frontend/adapter's NotExpressibleError: fail loudly, never silently skip a node and
    produce an incomplete workload.
    """
