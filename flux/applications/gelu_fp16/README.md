# gelu_fp16/ -- a hard numeric function, as a formula

A combinational FP16 GELU (tanh form) in SystemVerilog, within 1 ULP on every one of the 65,536
inputs, fast and small on ASAP7. The input is a floating-point number, the answer must round
right everywhere, and a table of the answers is not allowed. This problem makes a model
invent an algorithm rather than write RTL around a known one.

## How it runs

1. **A coding agent writes the algorithm as Python.** It writes `design(x)` on integers and
   bits, with small tables for polynomial coefficients only (at most 64 entries). It runs
   `flux rtl proto` on its draft: every input is checked in about a second, and the failures
   come back grouped by sign and exponent.
2. **The loop spells the verified prototype as SystemVerilog** (`flux_loop.py2sv`), bit for
   bit, with every signal as wide as its measured range. Nobody transcribes it.
3. **A prototype too costly to build is made cheaper first.** The estimate of its hardware
   cost must be under `budget.prototype_cost_max`, by default 2,000 (about 650 um2; a design
   costing ten times that did not synthesise in an hour).
4. **Yosys screens it, and OpenROAD places the finalists.**

`fp16-functions.md` is the method note the agent reads: why one fixed-point format cannot
hold an FP16 value, GELU(x) = x * h(x), the regions measured on the golden model (the
identity above 3.38, -0 below -5.285, a two-term series under 0.0278), a polynomial per
segment, and the rounding. It holds no design.

## What a run has measured (hosted `qwen3.6-35b-a3b-apex`)

| route | result |
|---|---|
| the model writes the RTL directly | about 60 attempts, no draft compiled |
| OpenCode writes the RTL directly | 115 attempts over 1.5 days, none passed |
| the model writes the prototype | the first to pass was a whole-range table (refused since, D616); as a formula it stalled at 24,196 of 65,536 inputs wrong |
| OpenCode writes the prototype, with the method note | 48,669 wrong, then 529, then 37: about a day of turns. The misses are 2 ULP in the negative tail. |

A stronger model, or a 2-ULP tolerance (`TOLERANCE_ULP` in `golden.py`), should reach a
passing prototype. Everything after that is mechanical.

## Run it

```bash
nix develop --command flux task check applications/gelu_fp16/gelu_fp16.problem.yaml
nix develop --command flux task run applications/gelu_fp16/gelu_fp16.problem.yaml
```

It needs OpenCode on PATH and configured for your model; see [docs/models.md](../../../docs/models.md),
which covers the context limit and the reasoning effort a thinking model needs. Without an
agent, set `flow: {generate: model}` and the model writes the prototype itself. The run
continues until stopped (`flux stop`), resuming from its best prototype each pass.
`flux log out/gelu_fp16.db` shows every agent turn.
