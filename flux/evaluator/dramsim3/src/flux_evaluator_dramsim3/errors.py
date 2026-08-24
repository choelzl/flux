class NotExpressibleError(ValueError):
    """Raised when an architecture has no hierarchy entry declaring `attrs.dramsim3_config`
    naming a real, bundled DRAMsim3 timing config (docs/decisions.md D74), or when more than one
    real DRAM channel is present in that config's own output (this adapter's real v0.1 scope is
    single-channel only — see README.md). Mirrors every other adapter's `NotExpressibleError`:
    fail loudly, never silently guess a DDR speed grade or average across channels it wasn't
    asked to.
    """
