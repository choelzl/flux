class NotExpressibleError(ValueError):
    """Raised when an architecture isn't a single-SRAM-macro shape this adapter's real CACTI
    integration can characterize (`hierarchy` must contain exactly one `class == "memory"` node,
    that node's `attrs` must carry an explicit `word_width_bits`, `size_kb` must divide evenly by
    it, and the resolved technology must be <= 90nm — CACTI 7's own real, verified constraint,
    docs/decisions.md D35). Mirrors every other adapter's `NotExpressibleError`: fail loudly,
    never silently guess a word width or clamp a technology node.
    """
