# nlu — an FP16 non-linear unit, designed by the loop (D408)

**The problem.** One hardware unit for `exp`, `log`, `sigmoid`, `tanh`, `gelu`,
`recip`, `rsqrt` at IEEE half precision, with a hard correctness gate — every
operator within **1 ULP** of the FP16 reference — and a measured **PPA** verdict
(area, fmax, power) from real synthesis and placement on ASAP7.

**The division of labor.** Claude built the rig; the model running in it does the
designing. The rig fixes only what a judge must own: the interface contract, the
FP16 reference and ULP arithmetic, a test-vector floor no author can lower, the
tool ladder, the record. The model chooses the computation method per operator
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

**The ladder.** author (model tests) → design rounds (model RTL, ≤2 repairs) →
prove (exhaustive ULP) → screen (yosys+STA: area/fmax, cached) → frontier
(area vs fmax; error is a gate, never a trade) → confirm (OpenROAD placement:
the PPA the report quotes) → decide (`--target-mhz`: smallest area meeting it;
otherwise the knee of area/fmax/power).

**The flywheel.** `--db` (default `demo-nlu.db`): designs (with sources), refusals
with counterexamples, the authored test suite and the decision all land in the
campaign record; a resumed run re-judges recorded designs (cached where tools and
source are unchanged), reads back conclusions, style/method/latency duels and past
refusals — and `--llm-round 0` replays the record with no model at all. The
designer prompt also reads the operator's paper library
(`mentor/knowledge/library/`, D407) through the local BM25 index.

```bash
nix develop --command python3 applications/nlu/demo.py --llm-round 4 --tui
nix develop --command python3 applications/nlu/demo.py --ops recip rsqrt --target-mhz 800
nix develop --command python3 applications/nlu/demo.py --llm-round 0   # record replay
```
