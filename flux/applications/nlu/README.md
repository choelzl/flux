# nlu — the seven operators of an FP16 non-linear unit, designed by the loop

**The problem.** `exp`, `log`, `sigmoid`, `tanh`, `gelu`, `recip`, `rsqrt` at IEEE half
precision, each with a hard correctness gate — within **1 ULP** of the correctly rounded FP16
reference on all 65,536 inputs — and a measured **PPA** verdict (fmax, area, power) from real
synthesis and placement on ASAP7.

**One loop per operator (D802).** `problem.yaml` is the parent: it lists the operators as
sub-loops in folders (`subtasks: [ops/recip, ...]`) and says what they share — the language,
the gate (`flux rtl test {artifact} --golden {home}/golden.py`, `{home}` being the operator's
folder), the stages (synthesis, then placement, at 1250 ps), the objectives (at least 800 MHz,
then the least area, then the least power), the methods sheet (`knowledge/nlu-methods.md`) and
the budget -- and who drafts: `generate: claude`, which every operator inherits (D804). Each `ops/<op>/` holds a `problem.yaml` saying only what differs — its statement
and its contract — and its `golden.py`: the reference in double precision, rounded to FP16
once, with `TOLERANCE_ULP = {"y": 1}`. A folder may override anything else too: its own
`flow.test`, a stage, the budget.

**The blocks** (`knowledge/fp16_blocks.py`, D804) are what every prototype turn reads beside the
methods sheet: FP16 decode, one round-to-nearest-even pack that does the subnormals, the carry,
overflow and underflow, fixed-point Horner, a segment-table fitter -- integer Python checked on all
65,536 patterns (`tests/unit/test_nlu_fp16_blocks.py`) and spelled by py2sv as they are -- and a
recipe per operator in their terms. Pieces, not designs: the drafter composes them.

**The chain** is the loop's own, per operator: a Python prototype of the algorithm, checked
against the golden model on every input; the RTL spelled from it (py2sv); the gate (Verilator
against the golden's vectors, within 1 ULP; NaN → any NaN); the synthesis screen; placement for
the finalists; the improve ladder; the decision. There is no composed unit: each operator is its
own answer, and the parent's report lists them. A parent that should compose its operators into
one unit says so with `generate: {command: "... {parts} {artifact}"}` (D801).

```bash
cd flux
nix develop --command flux task check applications/nlu
nix develop --command flux task run applications/nlu --tui        # every operator, in order
nix develop --command flux task run applications/nlu/ops/exp      # one operator, as the parent reads it
```

It needs Claude Code (`flux agent test claude --live`); another drafter is one line in
`problem.yaml` (`generate: opencode`, or `model` for the configured model -- see "Choose a
model" in the [usage guide](../../../docs/usage-guide.md)). The record is `applications/nlu/out/nlu.db`, one
campaign per operator (`nlu/exp`, ...) -- an operator run alone resumes the same one. Another
ask -- a 2-ULP budget, a 1 GHz clock, other operators -- is a copy of the folder (its name is its
id) with those changed.
