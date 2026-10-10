# evaluator/ — the Evaluator ABI and the measurement cache

The contract: `evaluate(workload, arch, mapping, budget) -> Result`. `abi/` holds the types, the
`Evaluator` protocol, `SequentialBatch`, `NotExpressibleError`, `run_tool` and the toolchain
fingerprint; `cache/` holds the loop's measurement cache keyed by tool fingerprints
(D340). See [docs/evaluator-abi.md](../../docs/evaluator-abi.md).

No backend lives here and nothing registers one by name (D954). Each adapter belongs to the
application that uses it and is called by that application's own scripts:

| adapter | where | called by |
|---|---|---|
| ZigZag (`zigzag-dse`), translating Workload and Architecture IR (ZigZag maps them itself, D957) | `applications/npu_gemm/tools/zigzag_tools/` | `evaluate.py`, `measure.py` |
| Timeloop + Accelergy (Docker by default, `FLUX_TIMELOOP_LOCAL=1` for the hermetic shell), the same IR | `applications/npu_gemm/tools/timeloop_tools/` | `evaluate.py --backend timeloop` |
| ChampSim (Pythia) on a trace, no ABI adapter (D957): an `.ini` or a C++ prefetcher header built in | `applications/prefetcher/tools/champsim_tools/` | `champsim.py run\|build\|check`, `bingo.py` |

Adapters translate Flux IR to and from the backend's native format and fail loudly
(`not_expressible_in`) rather than silently approximate. RTL measurement on ASAP7 is each RTL
application's own one-file `rtl.py` (D948, D950).

Removed: the nine accelerator-era adapters nothing on the one loop reached (D540); the
`openroad` evaluator (D950); the redaction guard (D952); the registry, the Verilator
`mac_array.sv` reference (`rtl/`) and the calibration package (D954).

## Wrapping a tool

Every adapter wraps its tool directly (D347): ZigZag, Timeloop and ChampSim are called through
`run_tool` with a timeout. What does not vary is the outside: every evaluator implements the same
`Evaluator` protocol and returns the same `Result` with its metrics, validity, domain and
provenance.
