---
hide:
  - navigation
---

# Tutorial: a square root circuit

Goal: a 16-bit integer square root in SystemVerilog, as fast and small as possible on ASAP7,
written by an AI model and checked by Flux. About ten minutes of running.

Before you start: the [full install](../index.md#get-started), and an AI model
([choosing one](run.md#choosing-an-ai-model)). Run `flux selftest`: every line should
say PASS.

## 1. Start from a template

```bash
flux example rtl isqrt
```

This writes `isqrt/` with a document, a golden model and a README. It runs as it is (an 8-bit
adder); the next two steps turn it into a square root.

## 2. Say what is correct: `isqrt/golden.py`

The **golden model** is a Python function that computes the right answer. Every design is tested
against it.

```python
import math

PORTS = [
    {"name": "x", "dir": "in", "bits": 16, "unsigned": True},
    {"name": "r", "dir": "out", "bits": 8, "unsigned": True},
]
COUNT = 200          # random inputs, on top of the corner cases

def golden(x: int) -> dict:
    return {"r": math.isqrt(x)}
```

## 3. Say what you want: `isqrt/problem.yaml`

```yaml
# the folder's name, isqrt, is the problem's id: the document does not say it
statement: >-                     # the request, in words: the model reads it
  A combinational integer square root in SystemVerilog: module `isqrt`, input `x` (16 bits,
  unsigned), output `r` (8 bits), r = floor(sqrt(x)). As fast as possible on ASAP7, then as
  small as possible.
contract: >-                      # rules every design must follow
  One module named exactly `isqrt`, purely combinational (no clock, no reset), ports
  `input logic [15:0] x` and `output logic [7:0] r`. r must equal floor(sqrt(x)) for every x.
language: systemverilog

flow:
  test: flux rtl test {artifact} --golden {home}/golden.py     # refuses a wrong design
  measure:                                                     # measurements, cheapest first
    screen: flux rtl measure {artifact} --stage synth --clock-ps 1000  # Yosys synthesis: seconds
    confirm: flux rtl measure {artifact} --stage place --clock-ps 1000  # OpenROAD placement: the quoted numbers
  select: {finalists: 2}
objectives:                       # reach 1000 MHz, then the smallest area
  - {metric: fmax_mhz, direction: maximize, goal: 1000}
  - {metric: area_um2, direction: minimize}
budget: {steps: 3, repair_attempts: 6}
```

## 4. Check it

```bash
flux task check isqrt
```

It lists the stages, their tools, the model it would use, and says "ready" or why not.

## 5. Run it

```bash
flux task run isqrt --passes 3 --agent tools --json answer.json
```

`--agent tools` lets the model run checks inside its turns. What happened in a recorded run:

| pass | what the loop did | result |
|---|---|---|
| 1 | the model wrote the algorithm in Python, checked on all 65,536 inputs; too costly, so it was asked for a cheaper one; Flux wrote the RTL; it passed the gate | 342 MHz, 45 um2 |
| 2 | nothing left to try: the pass ended at rest | |
| 3 | explore: asked for a design 30% cheaper | **396 MHz, 25 um2**, the decision |

No design reached 1000 MHz, and the report says so. Left running, the loop keeps trying.

## 6. Read the results

| what | where |
|---|---|
| the decision, the trade-offs, what was refused | the report at the end; `answer.json` |
| the chosen design | `isqrt/out/isqrt.sv` |
| how the search moved, pass by pass | `flux report isqrt/out/isqrt.db` (an HTML page) |
| every prompt and reply | `flux log isqrt/out/isqrt.db` |

## 7. Make it more agentic (one line each)

| to | add |
|---|---|
| let the model pick the next step | `--agent orchestrate`, or `flow: {orchestrate: agent}` |
| let the model plan each pass | `--agent plan`, or `flow: {plan: model}` |
| let a coding agent write the design | `flow: {generate: opencode}` (or `claude`, `codex`) |
| give the model a method note | `flow.knowledge: {files: [method-note.md]}` |
| steer it while it runs | `--tui`, then `f` to type a note |

Next: [build your own](build-your-own.md), or [the loop](loop-shape.md) for what each of these
steps is.
