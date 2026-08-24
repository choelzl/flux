class NotExpressibleError(ValueError):
    """Raised when a candidate falls outside `NativeEvaluator`'s v0.1 scope (`Candidate.workload`/
    `Candidate.arch` must be inline dicts, `Candidate.mapping` must be `None`), or when the real
    `flux_core` Rust extension itself rejects the translated shape (not exactly one `einsum` op
    with a 3-dim bound; not exactly one compute node with exactly one spatial dimension) —
    mirrors every other adapter's `NotExpressibleError`: fail loudly, never silently approximate.
    """
