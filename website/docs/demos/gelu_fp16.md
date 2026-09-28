# GELU FP16

**The problem.** A combinational FP16 GELU (the tanh form, used in neural networks) in
SystemVerilog, within 1 ULP on all 65,536 inputs, fast and small on ASAP7. A lookup table of the
answers is not allowed, so the AI must invent a formula.

## Run it

```bash
flux task check applications/gelu_fp16/gelu_fp16.problem.yaml
flux task run applications/gelu_fp16/gelu_fp16.problem.yaml
```

It runs until stopped (`flux stop`), resuming from its best prototype each pass.
`flux log applications/gelu_fp16/out/gelu_fp16.db` shows every agent turn.

## What the document says

| key | value |
|---|---|
| `contract` | module `gelu_fp16`, input `x`, output `y`, 16 bits each, no clock |
| `gate` | `flux rtl test` against `golden.py` |
| `stages` | `synth` (Yosys), `place_route` (OpenROAD) at a 2000 ps clock |
| `objectives` | `fmax_mhz` at least 500, then least `area_um2` |
| `flow.generate` | `{agent: {preset: opencode}}`: a coding agent writes the prototype |
| `knowledge` | `fp16-functions.md`: a method note (regions, polynomials, rounding), no design |
| `budget.prototype` | `true`: the algorithm is proved in Python first |

## How it runs

1. The coding agent writes `design(x)` in Python, on integers and bits, with small coefficient
   tables only (at most 64 entries). `flux rtl proto` checks every input in about a second.
2. Flux translates the passing prototype into SystemVerilog, bit for bit.
3. A prototype estimated too costly to build is made cheaper first.
4. Yosys screens it; OpenROAD places the finalists.

## Results recorded (hosted `qwen3.6-35b-a3b-apex`)

| route | result |
|---|---|
| model writes RTL directly | no draft compiled in about 60 attempts |
| OpenCode writes RTL directly | none passed in 115 attempts |
| OpenCode writes the prototype, with the method note | 37 of 65,536 inputs wrong (2 ULP in the negative tail) |

A stronger model, or a 2-ULP tolerance (`TOLERANCE_ULP` in `golden.py`), should pass.

## What it needs

OpenCode on PATH, configured for your model; the tool shell. Without an agent, set
`flow: {generate: model}` and the model writes the prototype itself.
