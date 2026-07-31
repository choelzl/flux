# evaluators/ — Evaluator ABI + backend adapters

The narrow contract: `evaluate(workload, arch, mapping, budget) -> Result`. One directory per
backend (native, zigzag, stream, timeloop, sparseloop, cimloop, rtl, hammer), each independently
installable. Adapters translate Flux IR to/from the backend's native format and fail loudly
(`not_expressible_in`) rather than silently approximate.

See [docs/04.md §4](../docs/04.md#4-l4--the-evaluator-abi).

`abi/` is implemented (types + `Evaluator` protocol, `flux-evaluator-abi` package). `zigzag/` and
`timeloop/` are both implemented for the workload side (real `zigzag-dse`, real
`timeloopaccelergy/accelergy-timeloop-infrastructure` via Docker) and both now translate
Architecture IR (`architecture_translator.py` in each — ZigZag's supports an N-dimensional
compute array, Timeloop's only a single spatial dimension) *and* Mapping IR
(`mapping_translator.py` in each, each within its own narrower, differently-shaped scope — see
each package's README). The same workload + architecture document pair has been run through both
for real, with matching content hashes confirmed in `Result.provenance`, producing a genuine (if
still narrow) controlled PPA comparison — see `../docs/phase1-exit-criterion-report.md` for the
numbers and, more importantly, the diagnosis of why they disagree.

`rtl/` is implemented too, on a hand-written `mac_array.sv` (not a generator) run through real
Verilator — the first *simulated*, not analytic, rung (docs/04.md §5's escalation diagram). Same
content-addressed (workload, architecture) pair as above: a real 529-cycle measurement, versus
ZigZag's 1554 and Timeloop's 512 (both analytic) — see `rtl/README.md` and
`../docs/phase1-exit-criterion-report.md`. The remaining adapters (`native/`, `stream/`,
`sparseloop/`, `cimloop/`, `hammer/`) are not started.
