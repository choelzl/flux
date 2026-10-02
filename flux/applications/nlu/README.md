# nlu — an FP16 non-linear unit, designed by the loop

**The problem.** One hardware unit for `exp`, `log`, `sigmoid`, `tanh`, `gelu`,
`recip`, `rsqrt` at IEEE half precision, with a hard correctness gate — every
operator within **1 ULP** of the FP16 reference — and a measured **PPA** verdict
(area, fmax, power) from real synthesis and placement on ASAP7.

**The division of labor.** Claude built the rig; the model running in it does the
designing. The rig fixes only what a judge must own: the interface contract, the
FP16 reference and ULP arithmetic, a test-vector floor no author can lower, the
tool chain, the record. The model chooses the computation method per operator
(LUT, interpolation, piecewise/minimax polynomial, Newton-Raphson, CORDIC, bit
products, parabolic synthesis…), shared datapath vs per-op units, combinational vs
pipelined and how deep — and it also **authors the unit tests**: adversarial FP16
vectors per operator, merged over the floor and kept in the campaign record.

**Correctness is a proof, not a sample.** FP16 has 65536 inputs, so every operator
is checked **exhaustively** in Verilator; `error-rate` and `max ULP` in the report
cover the whole domain. A design over budget is refused with its worst failing
inputs attached — they feed the repair prompt and the record. Specials are judged
by class: NaN→NaN (any payload), the reference's infinities exactly (saturating at
65504 where the reference overflows is an error, not an ULP).

**The chain** (the one loop every application runs on): author (model tests) → prototype (the model's
algorithm in the integer subset, checked on all 65,536 inputs inside the turn) → transpile
(the RTL, mechanically) → prove (exhaustive ULP) → screen (yosys+STA: area/fmax, cached) →
frontier (area vs fmax; error is a gate, never a trade) → confirm (OpenROAD placement: the
PPA the report quotes) → improve (the fmax ladder: sweep, take, depth pass, contender,
redesign) → decide (the smallest area meeting the `fmax_mhz` objective's goal, 800 MHz on the
routed stage in the document; otherwise the knee).

**The flywheel.** The campaign record (`applications/nlu/out/nlu.db` by default, or `--db`):
designs (with sources), refusals
with counterexamples, the authored test suite and the decision all land in the
campaign record; a resumed run re-judges recorded designs (cached where tools and
source are unchanged), reads back conclusions, style/method/latency duels and past
refusals. The
designer prompt also reads the operator's paper library
(`mentor/knowledge/library/`) through the local BM25 index.

The NLU is a PROBLEM DOCUMENT: `problem.yaml` says the parts, the objectives,
the ladder, the stages, the knowledge sheet and the budget, and names
the FP16 world (`flux_nlu.world.World`: the toolkit, the transpiler, the exhaustive gate, the
op mux, the yosys/OpenROAD flows) once. `flux task run` is the demo; another ask in this world
is a copy of the folder (its name is the id) with its own numbers.

It needs a model: the operators are designed by it (see "Choose a model" in the
[usage guide](../../../docs/usage-guide.md)).

```bash
cd flux
nix develop --command flux task check applications/nlu
nix develop --command flux task run applications/nlu --tui --think
nix develop --command flux task run applications/nlu --agent all   # the agent also calls tools, orchestrates and plans the pass
nix develop --command flux task run applications/nlu   # headless, until `flux stop` or Ctrl-C
```

The knobs are the document: `params:` (`ulp_budget`, `clock_period_ps`, `seed`, `test_rounds`),
the operators as `parts:`, the clock goal in `objectives:` and the loop's budget under
`budget:`. Another ask -- other operators, a 2-ULP budget, a 1 GHz clock -- is a copy of
the folder (its name is its id) with those changed. Routing
the composed unit takes over an hour; `--screen-only` stops at synthesis. The record of the
earlier demo campaigns is `demo-nlu.db` beside the document; `--db
applications/nlu/demo-nlu.db` resumes from it instead of starting a new record.
