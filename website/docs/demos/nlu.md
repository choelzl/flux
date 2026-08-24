# NLU

**An FP16 non-linear unit, designed by the loop.** Seven operators -- `exp`, `log`,
`sigmoid`, `tanh`, `gelu`, `recip`, `rsqrt` -- one hard gate (every operator within
**1 ULP** of the FP16 reference), and a measured **PPA** verdict from real
synthesis and placement on ASAP7. The framework fixes only what a judge must own;
the model chooses everything the study is about:

| decision | options | who makes it |
|---|---|---|
| method per operator | LUT, interpolation, piecewise / minimax polynomial, Newton-Raphson, CORDIC, bit products, parabolic synthesis, ... | the model |
| hardware sharing | one shared datapath with an op mux, or per-op units (the framework's mux wrapper makes both pay for selection) | the model |
| timing | combinational, or pipelined to any declared depth | the model |
| the unit tests | adversarial FP16 vectors per operator, merged over a coverage floor no author can lower | the model |
| the verdict | ULP by exhaustion, area/fmax by yosys + STA, PPA by OpenROAD | the tools |

**Correctness is a proof, not a sample.** FP16 has 65536 inputs, so every operator
is swept exhaustively in Verilator; the reported error rate and max ULP cover the
whole domain, specials judged by class (NaN to NaN, the reference's infinities
exactly -- saturating where the reference overflows is an error, not an ULP).
A design over budget is refused with its worst failing inputs attached; they feed
the repair prompt and the campaign record.

**The flywheel.** Designs (with sources), refusals with counterexamples, the
authored test suite and each run's decision land in the campaign record; a resumed
run re-judges recorded designs (cached where tools and source are unchanged),
reads back conclusions, style/method/latency duels and past refusals -- and
`--llm-round 0` replays the record with no model at all. The designer prompt also
reads the operator's local paper library (`mentor/knowledge/library/`).

```bash
nix develop .#physical --command python3 applications/nlu/demo.py --llm-round 4 --tui
nix develop --command python3 applications/nlu/demo.py --ops recip rsqrt --screen-only
```
