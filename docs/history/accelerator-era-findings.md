# Findings of the accelerator era, kept from the README

The repository README carried these paragraphs about code that has since been removed (the
search engines in D521, the nine evaluator adapters and the native core in D540). The numbers
were measured, so they stay here as written; the packages they name no longer exist.

## ZigZag against Timeloop, the same document through both

Both adapters translated Architecture IR, and the same workload + architecture document pair
was run through both, with matching content hashes confirmed in `Result.provenance`:
**1,117,367.53 pJ / 1,554 cycles (ZigZag) vs 620,000.0 pJ / 512 cycles (Timeloop)**. ZigZag's
energy used to be ~359x smaller than Timeloop's, an artifact of a flat placeholder cost; fixed
by anchoring per-memory energy to values in ZigZag's own bundled reference example, which moved
it to within 1.8x. The remaining gap has an evidenced mechanism: ZigZag's auto-chosen mapping
re-reads weights from DRAM roughly proportionally to temporal iteration count, while Timeloop's
mapper found a mapping that buffers weights once, verified by diffing Timeloop's per-component
energy output across array widths and ZigZag's `mem_energy_breakdown`
([calibration-report.md](../../flux/docs/calibration-report.md), Finding 6). Two follow-up
hypotheses were refuted against the tools' own output: that ZigZag's auto-search settled for a
worse mapping than a person would pick (an exhaustive sweep of every hand-designed flat mapping
never beats it), and that Timeloop's winning mapping needs a structure the flat translator
cannot reach (Timeloop's own `timeloop-mapper.map.yaml`, translated literally into Mapping IR
and run through ZigZag, gives **1666 cycles, not 512**). What is left is a cost-model accounting
difference, not a search or expressiveness gap. That round trip became an adapter feature:
`evaluator/timeloop/mapping_translator.py` translates Flux Mapping IR into Timeloop
`mapspace_constraints`. Full write-up:
[phase1-exit-criterion-report.md](../../flux/docs/phase1-exit-criterion-report.md).

`tests/conformance/` runs the corpus of workload examples x architecture examples against both
backends, checked against an expected-outcome matrix populated by running every combination
first.

## Calibration's headline finding

ZigZag's `latency_cycles` is a near-constant **~3.03x** Timeloop's across three architecture
widths (4, 8, 16), then the pattern **breaks** at a deliberately held-out fourth width (32),
dropping to ~2.05x. Extrapolating the three-point pattern would have been wrong by 48%; the
calibrated confidence interval, built from the first three points only, covers the real value.
The escalation policy flags the held-out point and the in-domain points too, since the residual
(~204%) is large enough that even directly measured results carry real uncertainty
([calibration-report.md](../../flux/docs/calibration-report.md)). The escalation had a real
stage to escalate to (`evaluator/rtl`'s Verilator-backed evaluator, `reference_source=
"rtl_sim"`), a drift-detection CI check (`tests/golden/calibration_baseline.json`) and a
holdout-corpus enforcement (`CorpusStore`: `public_entries()` structurally cannot return a
holdout entry).

## The removed search engines' optima

`orchestrator/agentic` ran one local-model call per round over five axes, each validated
against a proven optimum: the flat-mapping space (the 1554-cycle optimum), the
architecture-width axis (263 cycles at width 32, strictly monotonic), the NoC-topology axis
against real Booksim2 over a combined mesh+torus set (**a 49.6749-cycle global optimum at
torus/3D** across 8 candidates, D25), the memory-hierarchy-size axis (**1.25 KiB /
1116618.0081255918 pJ**, D26/D27) and the joint width x memory-size axis (**width=32 /
size_kb=1.25 / 193018.0081255918 pJ**, D26/D28). The combined mesh+torus NoC landscape is
non-monotonic: torus's 3D point beats its own 6D point although 6D torus has marginally fewer
average hops. The memory-size axis has a hard feasibility floor below which the evaluator
rejects the candidate, then energy rising monotonically with size. Autonomous multi-turn tool
calling against the MCP server did not work reliably with the Ollama models available then, so
all five strategies drove a harness-side propose/observe loop. Building the NoC strategy found
a Booksim2 bug (torus candidates crashed on an invalid routing-function key: no `dor_torus`
alias, only `dor_mesh`), fixed by switching the default to `dim_order` (D14-D16).

## The RTL reference testbench's two bugs

Found by running it against the installed Verilator (5.020) rather than trusting the tests:
the clock generator used a blocking assignment (`always #5 clk = ~clk;`), which `-Wall` flags
as `BLKSEQ` and treats as fatal (fixed with the non-blocking `<=`); and the adapter's `-j 0`
combined with `--timing` hit a Verilator thread-pool-teardown bug on roughly half of all runs
(`Internal Error: attempted to destroy locked Thread Pool`, reproduced standalone), fixed by
pinning `-j 1`, clean over 17 consecutive runs.
