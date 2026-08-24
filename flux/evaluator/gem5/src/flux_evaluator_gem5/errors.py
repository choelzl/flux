# Raised when an architecture isn't a single-RISC-V-CPU shape this adapter's real gem5
# integration can evaluate (`hierarchy` must contain exactly one `class == "compute"` node
# with `attrs.isa` starting with `"rv"` and `attrs.freq_ghz` set). Mirrors every other
# adapter's `NotExpressibleError`: fail loudly, never silently guess a CPU config.
from flux_evaluator_abi import NotExpressibleError  # noqa: F401  -- the ABI's one refusal, shared by every backend (D426)
