# evaluator/ — Evaluator ABI + backend adapters

The narrow contract: `evaluate(workload, arch, mapping, budget) -> Result`. One directory per
backend — thirteen registered (zigzag, timeloop, rtl, systemc, booksim, noxim, cacti, gem5,
openroad, thermal, dramsim3, native, stream; `interfaces/cli/src/flux_cli/registry.py` is the
authoritative list), each independently installable. Adapters translate Flux IR to/from the
backend's native format and fail loudly (`not_expressible_in`) rather than silently approximate.

See [docs/evaluator-abi.md](../../docs/evaluator-abi.md).

`abi/` is implemented (types + `Evaluator` protocol, `flux-evaluator-abi` package). `zigzag/` and
`timeloop/` are both implemented for the workload side (real `zigzag-dse`, real
`timeloopaccelergy/accelergy-timeloop-infrastructure` via Docker) and both now translate
Architecture IR (`architecture_translator.py` in each — ZigZag's supports an N-dimensional
compute array, Timeloop's 1-D and 2-D compute arrays (docs/decisions.md D215)) *and* Mapping IR
(`mapping_translator.py` in each, each within its own narrower, differently-shaped scope — see
each package's README). The same workload + architecture document pair has been run through both
for real, with matching content hashes confirmed in `Result.provenance`, producing a genuine (if
still narrow) controlled PPA comparison — see `../docs/phase1-exit-criterion-report.md` for the
numbers and, more importantly, the diagnosis of why they disagree.

`rtl/` is implemented too, on a hand-written `mac_array.sv` (not a generator) run through real
Verilator — the first *simulated*, not analytic, rung (docs/calibration.md's escalation diagram). Same
content-addressed (workload, architecture) pair as above: a real 529-cycle measurement, versus
ZigZag's 1554 and Timeloop's 512 (both analytic) — see `rtl/README.md` and
`../docs/phase1-exit-criterion-report.md`. The other registered adapters are real too — see each
package's own README (`native/` is the in-repo Rust roofline core, docs/decisions.md D75/D76;
`stream/` is real multi-core/layer-fusion DSE, D80–D82; `openroad/` is real placed-silicon PPA,
D225–D230). Not built: `sparseloop/` (resolved as not needed — `timeloop/` gained Timeloop's own
sparsity mechanism directly, D78), `hammer/` (superseded by `openroad/`, D225; its README stays
as the documented commercial-flow alternative), and `cimloop/`.

## Wrapping a tool: through CHIA, or directly

Two shapes exist here and the difference is not arbitrary, though it was never written down in one
place before (docs/decisions.md D347).

**CHIA packages exactly four tools** — `chia.vlsi.hammer`, `chia.vlsi.sram_cacti`,
`chia.simulators.champsim`, `chia.simulators.gem5`. Where a tool is one of those, the evaluator
calls CHIA's integration instead of building a subprocess wrapper: `cacti` calls
`chia.vlsi.sram_cacti.run_cacti`, `gem5` uses `chia.simulators.gem5.Gem5Node`. Those are real
`@ChiaFunction`s, which is the payoff — the same call runs locally (as it does here) or dispatches
to a cluster via `.chia_remote()`, and a from-scratch wrapper would have to reimplement that to get
it.

**Everything else wraps its tool directly**, because CHIA has no integration to reuse. Yosys,
OpenROAD, Verilator, Timeloop, ZigZag, Booksim, Noxim, DRAMsim3, 3D-ICE and the rest are wrapped
here, and that is not a gap to be closed — CHIA has no NoC simulator or synthesis-flow node to
adapt through.

So the rule is: **reuse CHIA's node when CHIA has one; wrap the tool when it does not.** Two of the
twenty evaluators with code go through CHIA, which is exactly the two whose tools CHIA ships.

What does NOT vary is the outside. Every evaluator implements the same `flux_evaluator_abi`
`Evaluator` protocol — `evaluate(candidate, budget, metrics) -> Result` — and returns the same
`Result` with its metrics, validity, domain and provenance. That is what makes them
interchangeable at a rung, and it is the interface a new evaluator has to satisfy however its tool
is reached.

## Placeholders

`cimloop/`, `hammer/` and `sparseloop/` hold no code. They are backends named in the roadmap and
not built, not registrations that went missing — `tests/unit/test_backend_registry_parity.py`
knows this and does not count them as gaps. Each carries a README saying what it would be and why
it is not there yet; a directory whose only statement of intent lives in a test's docstring is a
directory the next reader deletes.
