---
name: flux
description: Drive Flux, the AI-driven design-space exploration loop for hardware, to design, improve or explore a design (RTL modules such as adders, multipliers, FP units; or the bundled applications such as bank mappings, MAC arrays, prefetchers). Use when the user asks to generate, optimise, search a design space, or evolve a design towards a PPA target (fmax, area, power) with real tools (Verilator, Yosys, OpenROAD on ASAP7), or asks to run, check, write or read a Flux problem document, `flux ask`, `flux task run`, or a campaign record.
---

# Flux

Flux runs one loop: a generator proposes candidates (a model, a script over knobs, or a coding
agent), a **gate** refuses the wrong ones with a real test, **stages** measure the survivors
cheapest first, and the loop **decides** against the objectives and keeps a **record**. What a
run does is said by a **problem document** (`<folder>/problem.yaml`; the folder's name is its id). You never write the design
yourself inside Flux's loop: you write or pick the problem, run it, and read the answer.

## Where it runs

- The checkout is the directory holding `flux/flake.nix`. Every command runs from `flux/`
  inside its dev shell: `cd <checkout>/flux && nix develop --command <cmd>` (first entry builds
  the toolchain; later ones take seconds). `flux --help` lists the commands.
- Models: without configuration the loop's model is a local Ollama. A hosted OpenAI-compatible
  server is chosen with `FLUX_REMOTE_BASE_URL` and `FLUX_REMOTE_MODEL`, its key in
  `FLUX_REMOTE_API_KEY`. **Never write a key into a file, a document, a commit or chat**; pass
  it at launch from wherever the user keeps it (e.g. `FLUX_REMOTE_API_KEY="$(cat <keyfile>)"`).
  Each run prints which model it uses; check that line.
- Scratch space: `FLUX_TMPDIR` (default `~/.cache/flux/tmp`). Put it on a big disk for placement.

## Pick the way in

1. **An existing document** (the ask matches one): `applications/` holds adder16, mul8
   (RTL with a golden model), bankmap, interconnect_mapping, macarray, nlu, prefetcher. Copy the
   folder (its name is the copy's id) and change `params:`, `objectives:`, `budget:`, `flow:` to
   ask a different question; a changed ask opens its own record.
2. **A prompt and files, and let Flux write the problem**:
   `flux ask "what you want" --file spec.pdf --file ref.sv [--author model|opencode|claude|codex] [--dir out/ask_x] [--passes N] [--no-run]`.
   The author writes `problem.yaml` + its golden model/scripts, Flux checks the document, runs
   it, and the author revises between passes. Use `--no-run` to review the document first.
3. **Write the document yourself** (you know exactly what is wanted): read
   `flux/core/loop/src/flux_loop/author_reference.md` in the checkout first -- it is the key
   reference and the rules for golden models. Minimal RTL shape:

```yaml
# mul8/problem.yaml -- the folder's name is the problem's id; the document does not say it
statement: >-            # the ask in prose: the model reads it
  A combinational signed 8x8 -> 16-bit multiplier ...
contract: >-             # rules every candidate must follow: ports, names, what is forbidden
  One module named exactly `mul8`, ports ..., purely combinational.
language: systemverilog

flow:
  test: flux rtl test {artifact} --golden {home}/golden.py   # refuses before anything costs
  measure:                  # cheapest first; `flux rtl measure` knows its metrics and tools
    screen: "flux rtl measure {artifact} --stage synth --clock-ps 1000"
    confirm: "flux rtl measure {artifact} --stage place --clock-ps 1000"
  select: {finalists: 2}
objectives:              # the first is the goal; the second breaks ties among those meeting it
  - {metric: fmax_mhz, direction: maximize, goal: 1000}
  - {metric: area_um2, direction: minimize}
budget: {steps: 4, repair_attempts: 6, prototype: false}  # prototype: true for numeric functions (below)
```

## Run it

**A run never ends on its own** (only `flux stop`, Ctrl-C, or `--passes N`): pass after pass it
resumes from the record, and a pass with nothing left to try is followed by one that sends the
passing designs back to the model to do better -- the goal held, the next objective improved.
So from a tool call, either cap it (`--passes 1` for one pass, `--passes 3` to let it evolve) or
detach it and stop it yourself (below). `--replies` runs are one pass by default.

```bash
flux task check DOC                      # ALWAYS first: loads? keys valid? tools on PATH? stages it WILL SKIP
flux task run DOC --passes 1 --json answer.json   # one pass; the report on stdout, the answer as JSON
flux task run DOC --passes 1 --screen-only   # the cheap stage only (no placement): fast first look
flux task run DOC --replies r.json       # scripted replies, no model (dry runs, tests)
flux task run DOC --tui                  # the curses screen, for a person watching
```

- Long or open-ended runs (evolving a design, placement, prefetcher simulations, the NLU): detach
  and poll instead of blocking: `flux run -- flux task run DOC --db out/x.db`, then
  `flux status out/x.db`, `flux attach out/x.db --lines 40`, and `flux stop out/x.db` when the
  numbers are good enough (it stops at the pass boundary). `flux ask` runs the same way.
- A rerun with the same `--db` resumes the record; a changed document opens a new campaign.
- Exit codes: 0 decided, 1 nothing survived / an error (one line; `FLUX_DEBUG=1` for the
  traceback), 2 a bad document or no record, 130 interrupted.

## Search and evolution

`flow:` says how the search goes; mix them per problem:
- `generate: model` -- the model writes each candidate and repairs it against the gate's failures.
- `orchestrate: {policy: sweep, space: {knob: [ordered choices]}}` + `generate: {command: "{python} {home}/render.py {knob} {artifact}"}`
  -- a script renders each point; no model needed.
- `orchestrate:` with a space, a search (D797): a policy (`sweep`, `gradient`, `anneal`, `genetic`, `montecarlo`, `pareto`, `model`) or a
  list of phases, each continuing from where the last ended:
  `[{name: walk, policy: gradient, budget: 12}, {name: propose, policy: model, rounds: 1, batch: 4}]`.
- `generate: {by: opencode|claude|codex}` -- a coding agent writes the candidate in a work
  directory; the loop still gates and measures it. `{by: claude, questions: model}`
  says who answers when the agent asks (`decide`, the default: nobody; `model`; `operator`).
- Improving an existing design: put it (or the reference) in `flow.knowledge: {files: [...]}` and say
  in `statement` what must get better, and let the run go on: it keeps evolving until stopped.
- `skills: [dir]` / `--skill DIR` gives the loop's model and agents extra instructions.

## Numeric functions: prototype first

For floating-point or fixed-point functions (exp, GELU, reciprocal, anything checked in ULPs),
set `budget: {prototype: true}` with a `golden.py` gate. The model (or the coding agent) first
writes the algorithm as Python `design(**inputs)` on integers, checked on every input in about
a second (inputs up to 20 bits); the loop then spells the RTL itself, bit for bit. Writing that
RTL directly rarely passes.
- Check a prototype by hand: `flux rtl proto prototype.py --golden golden.py`. It prints where
  the failures are, grouped by the input's sign and exponent.
- The prototype must be a formula, not a lookup: module-level tables of at most 64 entries
  (`budget.prototype_table_max`), for coefficients. For a float input, keep the exponent and
  work on the mantissa; one fixed-point format cannot span the range.
- A prototype whose estimated hardware cost is over `budget.prototype_cost_max` (default 2,000,
  about 650 um2 on ASAP7) is made cheaper before anything is built.
- A method note in `flow.knowledge: {files: [...]}` (the method and measured facts, not a design)
  helps a model most. `applications/gelu_fp16/` is a worked example.

## Read the answer

- `answer.json` (from `--json`): `decision` (name, stage, knobs, metrics), `artifact` (the file
  written), `frontier`, `refused` (name + why), `not_established`, `lessons`, `report` (lines),
  and `result` (the application's own answer, e.g. bankmap's Verilog).
- The report leads with the decision and says what is measured vs estimated, and what was
  **NOT RUN** (a stage whose tools were missing). Quote numbers only from the stage that made
  them (a goal is judged on the deepest stage: the placed number, not the synthesis screen).
- `flux report DB` -- how the campaign moved over passes (frontier, best so far), as HTML.

## Rules that save hours

- The gate is the truth: a candidate that fails `flux rtl test` is never "almost right". If
  every candidate fails the same way, suspect the **golden model or the contract**, not the
  generator: run `flux rtl test <a candidate> --golden golden.py --show 5` and read the vectors.
- Golden models compute expected values; never hand-write expected numbers. Float ports carry
  bit patterns (`unsigned: True`); read bits with `np.uint16(x).view(np.float16)`, compute in
  float64, round once; allow `TOLERANCE_ULP = {"y": 1}` for approximations. Special values
  (inf, NaN, -0) must agree between `contract` and `golden.py`.
- Clocked designs: `CLOCK = "clk"` and `LATENCY = n` in golden.py; without them the module must
  have no clock port. `rst_n` is taken as the reset.
- About 1000 vectors (`COUNT = 1000`) per check; tens of thousands take minutes per repair.
- The clock in `--clock-ps` should match the goal (1000 MHz -> 1000 ps).
- A stage's `needs:` lists every tool it runs (place runs Yosys first: `[yosys, openroad]`).
- Don't edit the application code to change a question -- copy and change the document.
