---
name: flux-loop-creator
description: Create, tune or restructure a Flux loop -- the folder with its problem.yaml, golden model, scripts, library and sub-loops. Use when asked to write a new Flux problem/loop, turn a prompt or spec into a problem.yaml, add checks or measurement stages, set objectives and budgets, add a design-space search, split a design into parts or sub-loops, migrate an old document, or work out why `flux task check` refuses a document. For running loops and reading results in general, see the `flux` skill; for what a loop's steps and agents can query while it runs, see `flux-loop-commands`.
---

# Flux loop creator

A Flux loop is **a folder**. Its name is the loop's id; inside, `problem.yaml` says what to
design, how a candidate is refused (checks), how it is sized (measurement stages, cheapest
first), what makes one better (objectives) and how much to spend (budget). Flux writes only
`out/` and agents only `workbench/`; everything else is yours.

The authoritative references, in the checkout (read the one you need before writing anything
unusual; never guess a key, unknown keys are refused):

- `flux/core/loop/src/flux_loop/author_reference.md` -- the short form and the golden-model rules.
- `website/docs/guide/parameters.md` -- every key, default and placeholder (generated from code,
  guarded by a unit test, so it is current).
- `references/keys.md` beside this file -- the keys you reach for most, condensed.
- `references/examples.md` beside this file -- three worked loops of different shapes.

## 1. Lay out the folder

```
mul8/                       # folder name = loop id (letters, digits, _ - .; not starting with .)
  problem.yaml              # the document (also .yml / .json). No `id:` key: the folder says it.
  golden.py                 # reference model the golden check compares against (RTL loops)
  *.py / lib/               # your check, stage, generator or search scripts
  library/                  # papers and notes the model reads (digested once, see below)
  knowledge/                # a method sheet / helper files named by flow.knowledge
  skills/                   # skills handed to the model and agents, read unsaid (`skills:` other folders, `off`, `{off: [names]}`)
  ops/<child>/problem.yaml  # sub-loops: a child folder with its own document
  NAME.problem.yaml         # optional alternate ask over the same folder -> record out/<id>.NAME.db
  workbench/{tools,notes}/  # kept across runs; ONLY agents write here
  out/                      # Flux's: <id>.db record, the decided design <id><ext>, report, search state
```

Sub-loops share the parent's `out/` and `workbench/`. Library documents may be PDFs, Office
files (docx/xlsx/pptx and odt/ods/odp), HTML/EPUB, markdown/text/TeX, legacy doc/xls/ppt/rtf
(through LibreOffice) and source files; each is digested once and the summaries reach every prompt.

## 2. Write the document

Start from the smallest shape that answers the ask, then add. Key order is free; this order reads best:

```yaml
statement: >-        # the ask in prose; the model reads it. Name ports, widths, the target.
  ...
contract: >-         # rules EVERY candidate must keep: module name, ports, what is forbidden
  ...
language: systemverilog   # sets the file extension; python, cpp, rust, yaml, ini, ... also fine
flow:
  generate: model         # model | claude | codex | opencode | {command: ...} | {catalog: [...]}
  test:                   # checks, in order; the first that fails refuses and its text is {failure}
    lint: "fluxpreset rtl lint {artifact}"
    golden: "fluxpreset golden {artifact} --golden {home}/golden.py"
  measure:                # stages, cheapest first; costly ones run only on the finalists
    screen: {command: "fluxpreset rtl measure {artifact} --stage synth --clock-ps 1000",
             metrics: [fmax_mhz, area_um2, power_w], needs: [yosys, openroad]}
    confirm: {command: "fluxpreset rtl measure {artifact} --stage place --clock-ps 1000",
              metrics: [fmax_mhz, area_um2, power_w], needs: [yosys, openroad]}
  select: {finalists: 2}
objectives:               # every goal is a limit; goal-less ones then decide, in order
  - {metric: fmax_mhz, direction: maximize, goal: 1000}
  - {metric: area_um2, direction: minimize}
budget: {steps: 4, repair_attempts: 6, prototype: false}
```

Commands are argument lists, not shell: no pipes, no stdin, cwd is the pass's trace folder,
and `flux ...` / `fluxpreset ...` run in-process. `fluxpreset describe` lists the presets (RTL
lint, golden, OpenROAD stages, program timing, ChampSim, pytest), so most RTL loops need no
scripts of their own. `flux tools --json` is the catalog the web crafter offers.

**Placeholders** (an unknown `{x}` is refused at load): `{artifact}` the design file, `{home}`
the document's folder, `{workdir}`, `{python}`, `{name}`, `{part}`, `{params}` (params.json),
`{knob}` one setting, `{point}` all settings as JSON, `{failure}` and `{attempt}` (generators),
`{history}` and `{state}` (search commands), `{parts}` (composition), `{scope}`.

**Checks** print a failing count or exit non-zero; a check named `build` refuses on any non-zero
exit; exit 3 means "did not build". Options: `{run, count_re, fail_re, timeout_s: 120}`.
**Stages** exit 0 and print `name=value` lines or a JSON object (dictionary metrics:
`timings={"parse": 12}`). Options: `metrics` or `metrics_re`, `needs`, `timeout_s: 600`,
`cutoff` (stop escalating a design that cannot win), `estimate`. Every stage must report every
objective's metric; a missing tool in `needs` skips the stage (reported NOT RUN).

## 3. Choose who works each box

A box is a word, an agent name, or `{by: <who>, ...options}` (the old `{agent: X}` is refused).

| Box | Pick |
|---|---|
| `generate` | `model` for new code; a **coding agent** (`claude`/`codex`/`opencode`) for multi-file or iterative work; `{command}` when a script renders each setting; `{catalog}` when the designs already exist |
| `orchestrate` | `rules` (default); a **search** (see below) when there are settings to walk; `model`/agent to pick the next job |
| `plan` | `off`; `model`/agent writes a plan per round (it may change budgets and roles, never the gate) |
| `validate`, `select` | `rules` / `objectives` almost always; an agent on `select` only breaks ties |
| `critic:` | on the box it judges, e.g. `test: {build: make, critic: claude}` (D1040; there is no `flow.critique`) |
| `knowledge` | `{files, text, sheet, digest: model|<agent>, lessons: off|mined|<agent>, workbench: off|model|<agent>, hidden: [paths]}` or `off` |
| `test`, `measure` | always yours, never an agent's |

Agent options: `timeout_s` (1800), `session: turn|pass`, `questions: decide|model|operator`,
`probe: {gate: 20, stages: 3}` (how often it may run `flux probe`), `allow`, `hidden: [paths]`.

## 4. Add a search (settings to walk)

`orchestrate` becomes a search when it names a policy, a list of phases, or has `space`/`seeds`:

```yaml
flow:
  orchestrate:
    policy: sweep                   # sweep montecarlo gradient anneal genetic pareto model control command
    space:
      multiplier: {values: [behavioral, array, booth4, wallace], from: "out/invented/*.sv"}
      pipeline: [0, 1, 2, 3]        # ordered: gradient/anneal step to neighbours
      depth: {values: [1, 2, 4], when: {pipeline: [1, 2, 3]}}
    seeds: [{multiplier: behavioral, pipeline: 0}]   # the first seed is home
  generate: {command: "{python} {home}/gen.py {artifact} {multiplier} {pipeline}"}
```

A space must reach a command (`{knob}`/`{point}` in some command, or a command/catalog
generator) or loading fails. Policy by space size: under ~50 points `sweep`; hundreds
`gradient`/`anneal`/`montecarlo`; two objectives in real tension `pareto`; open-ended code
changes `model`. Phases chain policies, each continuing from where the last ended.

## 5. Split it: parts and sub-loops

- `parts:` -- one design drafted in pieces (a list, a map name -> description, or `decompose`; at most 8).
- `subtasks: [ops/recip, ops/exp]` -- each a child folder with its own `problem.yaml` and record
  `<parent>/<child>`. Children inherit `contract`, `language`, `flow`, `objectives`, `skills`,
  `params`, `budget`, `ladder` (`flow`/`budget`/`params` merge key by key, the child winning);
  not `statement`, `parts`, `subtasks`, `baseline`. A child often holds only `statement` + `contract`.
- A parent `generate: {command: "... {parts} {artifact}"}` composes the children's designs.

## 6. Tune

- **Cheapest first, everywhere.** Lint before golden; `--stage stat` < `synth` < `place` < `route`.
  Put a `cutoff` on the cheap stage so hopeless designs never reach placement.
- **Clock matches the goal.** `--clock-ps` = 1e6 / goal MHz (1000 MHz -> 1000 ps, 1600 -> 625).
- **`finalists`** (default 3; apps use 2-4) is how many climb to the costly stage. Raise it when
  the cheap stage ranks poorly against the costly one (`calibrate: on` measures that).
- **"Smallest that makes X"** = goal on the first objective, minimize the second. A real trade-off
  = `pareto` or `balance`. A limit relative to the best: `keep: 0.9, above: 1.0`. A shallow stage
  against a deep one: `margin`/`tie` around 0.03.
- **Golden models**: about 1000 vectors with a fixed seed; a tolerance in ULPs for float ops.
  `prototype: true` (numeric RTL with a golden model) lets the model build a Python prototype
  first; `false` for plain logic and script-rendered designs.
- **Budget**: `steps` (24) is designs per pass; `repair_attempts` (12) per design (mul8 6, NLU 20);
  `workers: 1` for timed stages (parallel runs skew timings); `passes` caps a run (0 = run until
  stopped -- a run never ends on its own).
- **Exploration**: `explore_every` (4), `explore_after` (2), `cooldown_after` (3) trade new ideas
  against refining the best; `revert_after`/`regress_after`/`max_tolerance` govern backing off.
- **Knowledge costs prompt space.** `knowledge: off` for small RTL loops (mul8); a method `sheet`
  plus a few `files` for hard numeric ones (NLU).
- **Hide what grades the design.** `knowledge: {hidden: [tests/, golden.py]}` keeps the agents from
  reading (and gaming) the gate's tests, expected outputs or parameters; the gate and stages still
  use them, and a check's failure text still reaches the agent. Paths are relative to the loop
  folder, never the document; sub-loops inherit them; it stands beside `off: true` too.

## 7. Check, migrate, run small

```bash
cd <checkout>/flux && nix develop --command bash
flux task check applications/mul8          # ALWAYS: keys, tools on PATH, stages it WILL SKIP, NOT ANSWERABLE
flux task migrate my_loop --write          # an older document -> today's form (keeps <file>.orig)
flux task run my_loop --passes 1 --screen-only   # cheap first look
flux task run my_loop --passes 1 --json answer.json
```

`task check` exits 2 when the file is not a document (`not a task: <why>`) and 1 when something is
missing or refused (`MISSING:` a tool, `REFUSED: <why>` -- a file a key names is not in the folder, the
golden model does not run). Every path it names is relative to the loop folder, each missing file with the
key that names it and what the folder holds.

**When Flux runs you as a loop's author** (New loop › Agent, `flux ask`), your brief names the exact check
command (`<python> -m flux_cli task check . --no-sandbox`): it is the check Flux runs when your turn ends.
Run it before you end every turn and fix what it says until it passes -- a refused draft comes back to you
with the refusal, and the same refusal twice means your last change did not touch what it names. Write the
golden model yourself, and name files as `{home}/<file>` in commands and by paths relative to the loop
folder elsewhere, never by an absolute path: the draft's folder moves when the loop is made.
`task run` exits 0 when a design meets every goal, 3 when correct designs were measured but none
qualifies (the closest is reported), 1 when nothing was measured. A changed document opens a new
campaign in the same record.

## Pitfalls

- No top-level `gate:`, `stages:`, `space:`, `seeds:`, `knowledge:`, `roles:`, `generator:`,
  `critique:`, `world:`, `hooks:`; these are old forms (`flux task migrate` converts most).
- `finalists` and `calibrate` belong in `flow.select` / `flow.calibrate`, not `budget`.
- `generate` takes no `timeout_s`; it uses the largest check timeout.
- `knowledge: {by: ...}` is refused; the digest's worker is `digest:`.
- Never write an API key into the folder; pass it at launch from the environment.
