# evaluator/ — the Evaluator ABI and the backend adapters

The contract: `evaluate(workload, arch, mapping, budget) -> Result`. One directory per backend,
each independently installable; `evaluator/abi/src/flux_evaluator_abi/registry.py` is the
authoritative list of registered names (`available_evaluators()`), and an application's own
adapter registers there too. Adapters translate Flux IR to and from the backend's native format
and fail loudly (`not_expressible_in`) rather than silently approximate. See
[docs/evaluator-abi.md](../../docs/evaluator-abi.md).

Registered today:

| name | package | what it is |
|---|---|---|
| `openroad` | `openroad/` | Yosys + OpenSTA synthesis, OpenROAD placement and full place-and-route on ASAP7 (D225-D230); the critical path as data (D526). The measurement stage of the NLU and the macarray worlds. |
| `rtl` | `rtl/` | the hand-written `mac_array.sv` reference through Verilator: the first simulated, not analytic, stage (a 529-cycle measurement against ZigZag's 1554 and Timeloop's 512) |
| `zigzag` | `zigzag/` | the ZigZag cost model (`zigzag-dse`), translating Workload, Architecture and Mapping IR |
| `timeloop` | `timeloop/` | Timeloop + Accelergy (Docker by default, `FLUX_TIMELOOP_LOCAL=1` for the hermetic shell), translating the same IR |
| `champsim_bingo` | `applications/prefetcher/evaluator/` | the prefetcher study's ChampSim/Pythia adapter |

Beside the adapters: `abi/` (the types, the `Evaluator` protocol, the registry, `run_tool`,
the toolchain fingerprint), `calibration/` (predicted-vs-reference residuals, escalation,
conformance, drift; [docs/calibration.md](../../docs/calibration.md)), `validity/` (an
independent first-principles check that shares no code with any adapter), `redaction/` (what
an evaluator's output may say to a model) and `cache/` (the loop's measurement cache keyed by
tool fingerprints, D340).

The nine adapters of the accelerator era that nothing on the one loop reached (booksim, cacti,
dramsim3, gem5, native, noxim, stream, systemc, thermal) and the placeholder directories were
removed in D540; their measured findings are kept in
[docs/history/accelerator-era-findings.md](../../docs/history/accelerator-era-findings.md).

## Wrapping a tool

Every adapter wraps its tool directly (D347): Yosys, OpenROAD, Verilator, ZigZag, Timeloop and
ChampSim are called through `run_tool` with a timeout, and no evaluator imports CHIA
(`tests/unit/test_evaluator_conventions.py` keeps it that way). What does not vary is the
outside: every evaluator implements the same `Evaluator` protocol and returns the same
`Result` with its metrics, validity, domain and provenance, which is what makes them
interchangeable at a stage of the loop's chain.
