# Tutorial: a new problem, explored by the loop

This walks through setting up a problem of your own and letting Flux explore it: a model
invents designs, real tools check and measure them, and the loop keeps going, pass after pass,
toward a goal. The example is a combinational 16-bit integer square root in SystemVerilog,
fast and small on ASAP7. Its two files are in [tutorial/isqrt/](tutorial/isqrt/). Every
number below comes from a real run with a hosted 35B model (`qwen3.6-35b-a3b-apex`).

## What "agentic" means here

The loop has four roles, and each can be filled by a model (or a coding agent) or by
pre-written rules:

| role | what a model can do there | how you turn it on |
|---|---|---|
| generator | write each design, repair it from the failures, make it cheaper | `flow: {generate: model}` (the default), or `opencode` |
| orchestrator | pick the next piece of work, with its reasons on the record | `--agent orchestrate`, or `flow: {orchestrate: agent}` |
| planner | write the plan of a pass: parts, order, method, budgets | `--agent plan`, or `flow: {plan: model}` |
| search | propose the next points of a knob space from what was measured | `flow: {orchestrate: {by: model}}`, or a `model` phase |

With `--agent tools`, a model turn can also call tools inside the turn: run Python, run the
problem's own check on a draft, read the history. Two things are never handed to a model: the
gate, which decides whether a design is correct, and the tools that measure it.

## Before you start

```bash
cd flux && nix develop --accept-flake-config
flux selftest                 # PASS/FAIL per check: tools, a sweep, the model server, a model-written problem
```

Point Flux at a model first: a local or remote Ollama (`FLUX_LLM_MODEL`, `OLLAMA_BASE_URL`), or
any OpenAI-compatible server (`FLUX_REMOTE_BASE_URL`, `FLUX_REMOTE_MODEL`,
`FLUX_REMOTE_API_KEY`). [models.md](models.md) has the recipes.

## 1. Start from a template

```bash
flux example rtl isqrt
```

This writes `isqrt/` with a problem document, a golden model and a README. It runs as it
stands (an 8-bit adder); the next two steps make it the square root.

## 2. Say what is correct: the golden model

`isqrt/golden.py` declares the ports and computes the right answer for any input. It is the
specification, never an implementation to copy:

```python
import math

PORTS = [
    {"name": "x", "dir": "in", "bits": 16, "unsigned": True},
    {"name": "r", "dir": "out", "bits": 8, "unsigned": True},
]
COUNT = 200

def golden(x: int) -> dict:
    return {"r": math.isqrt(x)}
```

The gate runs every design against it: Verilator drives the corner cases and `COUNT` random
inputs and compares each output.

## 3. Say what you want: the problem document

```yaml
# isqrt/problem.yaml -- the folder's name is the problem's id
statement: >-                     # the ask, in words: the model reads it
  A combinational integer square root in SystemVerilog: module `isqrt`, input `x` (16 bits,
  unsigned), output `r` (8 bits), r = floor(sqrt(x)). As fast as possible on ASAP7, then as
  small as possible.
contract: >-                      # the rules every design must follow
  One module named exactly `isqrt`, purely combinational (no clock, no reset), ports
  `input logic [15:0] x` and `output logic [7:0] r`. r must equal floor(sqrt(x)) for every x.

language: systemverilog

flow:

  test: flux rtl test {artifact} --golden {home}/golden.py   # refuses a wrong design first

  measure:                           # measurement, cheapest first
    screen: flux rtl measure {artifact} --stage synth --clock-ps 1000  # Yosys synthesis: seconds
    confirm: flux rtl measure {artifact} --stage place --clock-ps 1000  # OpenROAD placement: the numbers the report quotes
  select: {finalists: 2}

objectives:                       # a goal first, then what to minimise among those that meet it
  - {metric: fmax_mhz, direction: maximize, goal: 1000}
  - {metric: area_um2, direction: minimize}

budget: {steps: 3, repair_attempts: 6}
```

Everything else is inferred: the file extension from `language`, the record's name from `id`,
what `flux rtl measure` prints and needs, the units, and that a goal is judged on the deepest
stage. With a golden model the prototype stage is on by default (`budget.prototype: false`
turns it off, for plain logic like an adder), and that is what matters for a numeric function
like this one. The model first writes the algorithm as Python `design(x)` on integers. It is checked on every one of the
65,536 inputs in about a second, and the loop then writes the SystemVerilog from it, bit for
bit. Asking a model for that RTL directly rarely passes. Every key a document may use is in
[author_reference.md](../flux/core/loop/src/flux_loop/author_reference.md).

## 4. Check it

```bash
flux task check isqrt
```

It lists the parts, the roles you can switch, the stages and their tools, and the model it
would use ("ready", or why not). It runs nothing, and refuses a document that asks for
something no stage measures.

## 5. Run it

```bash
flux task run isqrt --passes 3 --agent tools --json answer.json
```

Without `--passes` a run goes on until you stop it (Ctrl-C, `flux stop`, or `q` in `--tui`).
The run above took about ten minutes:

- **Pass 1: a first design.** The model's prototype passed all 65,536 inputs on its second
  attempt, at an estimated hardware cost of 4,870. That is over the ceiling
  (`budget.prototype_cost_max`, 2,000 by default), so before anything was built the loop asked
  for a cheaper one: 1,044. The loop wrote it as 310 lines of SystemVerilog, and it passed the
  gate. Synthesis screened it and OpenROAD placed it: **342 MHz, 45 um2, 571 cells**.
- **Pass 2: nothing left to try,** so the pass ended at rest.
- **Pass 3: exploring.** A pass after a rest sends the design back to be improved. The loop
  asked for a prototype 30% cheaper, the model wrote one, and it passed every input again. It
  was spelled (414 lines) and placed: **396 MHz, 25 um2, 328 cells**, faster and smaller, and
  the decision.

No design reached the 1,000 MHz goal, and the report says so ("nothing reaches fmax_mhz 1000;
the most fmax_mhz"). Left running, the loop would keep exploring toward it.

## 6. Read the results

| what | where |
|---|---|
| the decision, the front, what was refused and why | the report at the end of the run; `answer.json` with `--json` |
| the chosen design | `isqrt/out/isqrt.sv` |
| how the campaign moved, pass by pass | `flux report isqrt/out/isqrt.db` writes `isqrt-report.html` |
| every prompt, reply and tool call | `flux log isqrt/out/isqrt.db` |
| everything live | `flux task run ... --tui` (`f` types a note into the next prompt) |

A rerun with the same record resumes: nothing already measured is paid for again.

## 7. Make it more agentic

Each of these is one flag or one line; mix them.

- **Let a model steer the work:** `--agent orchestrate` (or `flow: {orchestrate: agent}`). The
  model reads the standings and the record with tools, picks the next step, and records why.
- **Let a model plan each pass:** `--agent plan` (or `flow: {plan: model}`): the parts, the order,
  the method to try first, the budgets. It is checked against the problem before it applies.
- **Hand the writing to a coding agent:** `flow: {generate: opencode}` (or `claude`,
  `codex`). The agent writes; the loop runs the gate and brings failures back to it. With
  `prototype: true` the agent writes the Python prototype, which the loop checks with
  `flux rtl proto` before it writes the RTL.
- **Give it knowledge:** `flow.knowledge: {files: [method-note.md]}`: a method, measured facts, a
  paper. Not a design. `applications/gelu_fp16/` shows a method note for a hard function.
- **Steer it while it runs:** type a note in the TUI (`f`); it reaches the next prompt.

## 8. Explore a space of knobs

When the designs come from parameters rather than from a model, declare the knobs and a
search policy. A script writes each point, and the gate and the stages judge it as before:

```yaml
flow:
  generate: {command: "{python} {home}/gen.py {artifact} {arch} {block}"}
  orchestrate:
    space:
      arch: [ripple, carry_select, kogge_stone]
      block: [2, 4, 8]
    - {name: coarse, policy: sweep, knobs: [arch]}              # every architecture
    - {name: fine, policy: gradient, hold: [arch], steps: 10}   # then tune the block size
    - {name: ideas, policy: model, rounds: 2}                     # then a model proposes points
budget: {steps: 12}  # every batch is a step: enough for all three phases
```

On the `rtl-sweep` template (its knobs are `arch` and `chunk`) with 12 steps, `coarse` swept
the architectures, `fine` moved `chunk` and raised fmax from 3,166 to 3,577 MHz, then `ideas`
asked the model. With the template's `steps: 1` only the first phase runs.
`flux example rtl-sweep NAME` starts from this shape. The policies are `sweep`,
`gradient`, `anneal`, `genetic`, `montecarlo`, `pareto` and `model` (the model proposes). The
[cookbook](cookbook.md) says which suits which space.

## 9. Other kinds of problem

| you have | start with |
|---|---|
| a Python function to write and speed up | `flux example python NAME` |
| a program whose settings to tune | `flux example tune NAME` |
| a script that writes designs from knobs | `flux example sweep NAME` |
| only a description and some files | `flux ask "what you want" --file spec.pdf` |
| an accelerator architecture for a workload | `applications/npu_gemm/`: a script writes the architecture, ZigZag measures it |

For a search policy, a checker or a search command of your own, see [extending.md](extending.md).
