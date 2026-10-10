# evaluator/ — measurement records and the measurement cache

`abi/` holds the measurement record (`Result` with its `Estimate`s, validity, domain, bottleneck,
provenance and escalation), `run_tool` and the toolchain fingerprints; `cache/` holds the loop's
measurement cache keyed by tool fingerprints (D340). See
[docs/evaluator-abi.md](../../docs/evaluator-abi.md).

No backend or adapter exists (D958): every loop stage is a command printing `name=value` (D954).
RTL measurement on ASAP7 is each RTL application's own one-file `rtl.py` (D948, D950); ChampSim
(Pythia) is the prefetcher's `tools/champsim_tools/`, run by `champsim.py run|build|check` and
`bingo.py` (no ABI adapter, D957).

Removed: the nine accelerator-era adapters nothing on the one loop reached (D540); the
`openroad` evaluator (D950); the redaction guard (D952); the registry, the Verilator
`mac_array.sv` reference (`rtl/`) and the calibration package (D954); ChampSim's ABI adapter
(D957); ZigZag, Timeloop, the `Evaluator` protocol, `SequentialBatch` and `NotExpressibleError`
(D958).
