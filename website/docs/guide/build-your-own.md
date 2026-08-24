# Build your own loop

A Flux problem is a **document**: one `<name>.problem.yaml` file that says what to make, how a
candidate is checked, how it is measured and what "better" means. The loop (`core/loop`,
package `flux_loop`) does everything else: the campaign record and resuming from it, the
generation-and-repair loop around a model, the search over a design space, the gate, the
chain of stages, the frontier, the decision and the report.

Only when the document cannot say something -- a simulator of its own, a solver, a generator
that invents across rounds -- does a problem add a **world**: a Python object whose methods
fill named hooks of the loop, named once in the document as `world: package.module:World`.

Two applications in the repository are the canonical examples, and neither has a world:

- [`applications/mul8/`](https://github.com/choelzl/flux/tree/main/flux/applications/mul8):
  a model writes an RTL multiplier; the document plus a golden model is the whole problem.
- [`applications/adder16/`](https://github.com/choelzl/flux/tree/main/flux/applications/adder16):
  a design-space sweep over generated adders, with no model at all.

If you would rather describe the problem in words, `flux ask "..."` has a model or a coding
agent write the document and its files for you, checks it and runs it.

## A problem written by a model: mul8

```yaml
id: mul8
statement: >-
  A combinational signed 8x8 -> 16-bit multiplier in SystemVerilog: module `mul8` with inputs
  `a` and `w` (`input logic signed [7:0]`) and output `p` (`output logic signed [15:0]`), the
  smallest that makes 1000 MHz placed on ASAP7. ...
contract: >-
  One module named exactly `mul8`, purely combinational, Verilog-2001 constructs only ...
language: systemverilog

gate: flux rtl test {artifact} --golden {home}/golden.py

stages:
  - name: screen
    command: flux rtl measure {artifact} --stage synth --clock-ps 1000
  - name: confirm
    command: flux rtl measure {artifact} --stage place --clock-ps 1000

objectives:
  - {metric: fmax_mhz, direction: maximize, goal: 1000}
  - {metric: area_um2, direction: minimize}

flow:
  generate: model          # or {agent: opencode}: a coding agent writes the module

budget:
  steps: 4
  repair_attempts: 6
  prototype: false
  finalists: 2
```

The `statement` and `contract` are the prompt. The gate runs `flux rtl test`, which Verilates
the design, drives it with the corners of every input and random vectors, and compares every
output with the golden model; the loop reads the failure count with `count_re`, and a failing
design goes back to the model with its failing vectors. The two stages run `flux rtl measure`,
which prints `metric=value` lines; the loop knows which it prints and which tools it needs, and a
stage whose tool is not on PATH is skipped, and the report says so. For a command of your own,
`metrics:` names what it prints and `needs:` what it runs.

The golden model says *what* the circuit computes, never how:

```python
PORTS = [
    {"name": "a", "dir": "in", "bits": 8},
    {"name": "w", "dir": "in", "bits": 8},
    {"name": "p", "dir": "out", "bits": 16},
]
SEED = 1
COUNT = 24

def golden(a: int, w: int) -> dict:
    """signed int8 x int8 -> int16"""
    return {"p": a * w}
```

## A design-space sweep with no model: adder16

```yaml
id: adder16
statement: >-
  An unsigned 16-bit adder (module `adder16`, inputs `a` and `b`, 17-bit sum `s`): the
  smallest that makes 3000 MHz placed on ASAP7, chosen from the architectures gen.py spells.
language: verilog

space:
  arch: [behavioral, ripple, carry_select, kogge_stone]
  block: [2, 4, 8]

flow:
  dse: sweep
  generate: {command: "{python} {home}/gen.py {artifact} {arch} {block}"}

# gate, stages, objectives: as in mul8, with --clock-ps 300 and a 3000 MHz goal

budget:
  steps: 1
  finalists: 2
  prototype: false
```

`space:` lists the knobs and their choices, in an order where neighbours are a little
different. `flow: dse:` names the policy that walks it: `sweep` (every point), `montecarlo`,
`anneal`, `gradient`, `genetic`, `pareto`, or `llm` (a model names the next points).
`flow: generate: {command: ...}` makes a script the generator: for every point the loop
substitutes the knobs (`{arch}`, `{block}`) and the script writes `{artifact}`. The gate and
the stages see the same placeholders. A placeholder that is neither a knob nor one of the
loop's own is refused when the document loads.

The loop's own placeholders are `{artifact}` (the candidate, written to a file), `{home}` (the
document's directory), `{python}` (the interpreter running Flux), `{workdir}`, `{name}`,
`{part}`, and for a generator command `{failure}` and `{attempt}` (why the last draft was
refused, and which try this is). A command is one string or a list of tokens; a command that
starts with `flux` runs this Flux.

## A numeric function: a prototype first

For a floating-point or fixed-point function (exp, GELU, a reciprocal: anything checked in
ULPs), mul8's document with one change works better: `budget: {prototype: true}`. The model,
or a coding agent, first writes the algorithm as Python `design(**inputs)` on integers. The
loop checks it on every input in about a second (inputs up to 20 bits), with the failures
grouped by the input's sign and exponent, and then writes the SystemVerilog from it itself,
bit for bit. Asking for that RTL directly rarely passes.

- The prototype is a formula, not a lookup of the answers: a module-level table holds at most
  64 entries (`budget.prototype_table_max`), for coefficients.
- A prototype whose estimated hardware cost is over `budget.prototype_cost_max` (default 2,000,
  about 650 um2 on ASAP7) is made cheaper before anything is built.
- `flux rtl proto prototype.py --golden golden.py` is the same check, by hand or for an agent.
- A method note in `knowledge: {files: [...]}` (the method and facts measured on the golden
  model, not a design) helps most. The repository's `applications/gelu_fp16/` is a worked
  example, with a coding agent writing the prototype.

## Check it, run it

```bash
cd flux
nix develop --command flux task check applications/adder16/adder16.problem.yaml
nix develop --command flux task run applications/adder16/adder16.problem.yaml --screen-only
```

`flux task check` loads the document, lists what each part of the loop will do and which
tools each stage needs, and refuses a document that asks for what it cannot measure -- an
objective no stage produces, a misspelled key (with the nearest real key) -- before anything
is spent. `flux task run` writes the record to `<document dir>/out/<id>.db` and the decided
artifact beside it (`--db`, `--out` to move them); a second run resumes from the record and
measures nothing twice. `--replies replies.json` runs a model-driven document with scripted
replies and no model, which is how the tests drive it.

To try another idea, copy the document and edit it, with its own `id:` so the two records
stay apart.

## What a document can say

| key | what it says |
|---|---|
| `id`, `statement` | the name, and what to make in words (with `contract`, the prompt) |
| `contract` | the rules a candidate must follow: the interface, what is allowed |
| `language` | what the artifact is written in (`systemverilog`, `python`, `cpp`, ...); the file extension follows from it |
| `gate` | the test command, or `{build, test}`; it prints `N failing` (else `count_re` or `fail_re` says how to count), or its exit code decides |
| `stages` | the chain of measurements, cheapest first: each a `command` (a custom one says the `metrics` it prints and the tools it `needs`; `flux rtl measure` knows both), an `evaluator` by registry name, or nothing (the world measures); `timeout_s`, `cutoff` |
| `objectives` | a list of `{metric, direction, goal}`: `goal` is the target, judged on the deepest stage unless `stage:` names another; `margin`, `tie`, and `unit` for a metric Flux does not know |
| `space` | knob -> choices, for a `flow: dse:` policy |
| `flow` | who fills each box of the loop: `dse` (a policy, or a list of phases run in order), `generate` (`model`, `{command: ...}`, `{catalog: [...]}`, `{agent: opencode}`), `orchestrate`, `plan`, `critique`, `analytical`, `knowledge`, `extract`, `records`, ...; the only place a box is said |
| `budget` | the loop's knobs: `steps`, `repair_attempts`, `finalists`, `workers`, `passes`, `prototype`, ... |
| `parts` / `subtasks` | pieces of one artifact written one at a time, or child documents each run as its own loop |
| `knowledge` | what the model reads beside the prompt, e.g. `{sheet: methods.md}` beside the document |
| `skills` | skill folders (a `SKILL.md` each) for the model and coding agents |
| `world`, `params`, `hooks` | the world and the settings it is built from; `hooks: {judge: module:callable}` replaces one hook |
| `cache`, `ladder` | `cache: false` for a world that measures in microseconds; the improve steps for a part |

The [usage guide](https://github.com/choelzl/flux/blob/main/docs/usage-guide.md) has the full
list with every option.

## When you need a world

A world is for what prose and commands cannot say. The document names it once,
`world: package.module:World`; the loop calls `World(problem)` and binds every hook the object
has, keeping its own defaults for the rest. The settings a world is built from are the
document's `params:`, so another ask in the same world is still just a copy of the document.

A complete, working example -- a cache sized from a space, judged by a budget rule and
measured by a formula (in a real world, a simulator):

```python
# flux_toy/world.py
from flux_loop import Verdict

class World:
    def __init__(self, problem):
        self.budget = int(problem.task.params.get("max_kib", 64))

    def judge(self, built, cand, subgoal, state):
        kib = cand.knobs["ways"] * cand.knobs["sets"] * 64 // 1024
        if kib > self.budget:
            return Verdict(False, kib - self.budget, f"{kib} KiB is over the {self.budget} KiB budget")
        return Verdict(True, 0.0)

    def measure(self, cand, stage, state):
        ways, sets = cand.knobs["ways"], cand.knobs["sets"]
        return {"miss_rate": 1.0 / (ways * sets) ** 0.5, "kib": ways * sets * 64 / 1024}
```

```yaml
# toy.problem.yaml
id: toy
statement: The cache with the lowest miss rate that fits the budget.
world: flux_toy.world:World
params:
  max_kib: 64
space:
  ways: [1, 2, 4, 8]
  sets: [64, 128, 256, 512]
flow:
  dse: sweep
stages:
  - {name: model, metrics: [miss_rate, kib]}      # no command: the world measures
objectives:
  - {metric: miss_rate, direction: minimize}
  - {metric: kib, direction: minimize, unit: KiB}
budget:
  steps: 1
  prototype: false
```

The run refuses the three points over budget with the reason, measures the thirteen others and
reports the frontier and the knee. The package must be importable: put it under
`applications/<name>/lib/src/` and add that directory to `localSrcDirs` in `flux/flake.nix`
(or, while trying it out, put its parent directory on `PYTHONPATH`).

The hooks a world may fill, by box of the loop (`flux task check` prints the same list and
marks what a world fills; the first ones in each box are the usual ones):

| box | hooks |
|---|---|
| knowledge | `prepare`, `knowledge`, `mentor_sections`, `versions`, `from_record`, `open_records` |
| orchestrate | `review`, `next_work`, `plan_prompt`, `standing` |
| dse | `search`, `space`, `instantiate`, `seeds`, `moves` |
| generate | `design_prompt`, `parse_design`, `rewrite_prompt`, `patch_prompt`, `prompt_prefix`, `tools`, `apply_tools`, `transpile`, `redesign_note` |
| test | `build`, `fast_check`, `judge`, `describe_failure` |
| evaluate | `measure`, `measure_batch`, `cache_key`, `compose`, `analytic_stages`, `analytic_metrics`, `evaluator_name` |
| select | `frontier`, `finalists`, `frontier_axes`, `decide`, `conclusion` |

A few to know first:

- `judge(built, cand, subgoal, state) -> Verdict` is the gate: exhaustive or proof-grade where
  it can be. `Verdict(ok, score, why)`: `score` is the distance from passing, `why` the text a
  repair prompt carries.
- `measure(cand, stage, state) -> dict` runs one stage; numbers become metrics.
  `measure_batch` measures a whole batch in one call, for a tool that runs in parallel.
- `search(state)` is a Python generator that yields batches of candidates and receives each
  batch's scored results back as the value of the `yield`, so a policy that reads results
  before choosing the next batch (a climb, a solver then a model) keeps its state in local
  variables. With a `space:` and a `flow: dse:` policy, you do not need it: `seeds` and `moves`
  are enough to steer the loop's own search.
- `design_prompt` and `parse_design` are how a model is asked for a candidate and how its reply
  becomes one; `fast_check` is the millisecond test the repair loop edits against.

The bigger worlds in the repository are worked examples:
[`flux_bankmap`](https://github.com/choelzl/flux/tree/main/flux/applications/bankmap)
(a proof chain: checker, pigeonhole, z3, model),
[`flux_macarray`](https://github.com/choelzl/flux/tree/main/flux/applications/macarray)
(a generator, golden vectors, a cached measurer, invention rounds) and
[`flux_nlu`](https://github.com/choelzl/flux/tree/main/flux/applications/nlu)
(parts, a prototype stage, a transpiler, an exhaustive gate).

## Using it from other tools

Every document is already scriptable: `flux task run DOC --json answer.json` runs it and
writes the answer (the decision with its metrics and artifact, the frontier, what was refused
and why, what is not established, the lessons, the report's lines and the world's own
`result`). A coding agent drives it the same way, can write the document for `flux ask`, or can
be its generator (`flow: {generate: {agent: opencode}}`). Nothing needs wrapping.

To test a new problem, drive it with scripted replies (`--replies`) and pin the order of what
it asks for and refuses, not the tools' numbers.

## Where the shared pieces live

Everything an application needs already exists as a package; building a loop is mostly wiring.
Paths below are under
[`flux/`](https://github.com/choelzl/flux/tree/main/flux) in the repository:

| you need | use | from |
|---|---|---|
| a measurement cache that survives resumes and dies with a tool bump | `MeasurementCache` (namespaced by tool fingerprints) | `evaluator/cache` |
| a queryable record of every trial, readable back as seeds | `flux_records.Records` over `CampaignStore`: trials, refusals, conclusions, notes | `mentor/records` + `core/stores` |
| laws and head-to-head verdicts extracted from the record | `flux_records.extract.pairwise_laws` / `head_to_head` | `mentor/records` |
| the decision arithmetic: corners, the knee, target-and-floor | `flux_frontier.corner` / `knee_ranked` / `cheapest_meeting` | `core/frontier` |
| frontier, confirmation spread, budget rules for two objectives | `flux_frontier` | `core/frontier` |
| a tree policy allocating waves across the frontier | `flux_frontier.pareto_uct` | `core/frontier` |
| the loop itself: planner, inner loop, judge, compose, chain, decide, record | `flux_loop.run_loop`, `flux task run` | `core/loop` |
| one model call, schema-constrained or with tools, thinking per request | `flux_llm.OpenAIChatProposer` -> `Reply` | `core/llm` |
| a bounded ask-check-repair round: ask, check for real, repair with the failure attached | `flux_llm.ask_until` / `refine` | `core/llm` |
| knowledge as declared sources, assembled under a budget | `flux_knowledge.Mentor` + `Corpus`/`Library`/`RecordReadback`/`Notes` | `mentor/knowledge` |
| operator guidance typed while the loop runs, drained into proposer prompts | `drain_guidance` + `reload_notes` (resumed runs re-show earlier notes) | `mentor/feedback` |
| real silicon numbers on ASAP7 | `measure_rtl` (what `flux rtl measure` runs), `run_synthesis_flow` (seconds) / `run_ppa_flow` (placement) | `evaluator/openroad` |
| Verilator verification of generated RTL against golden vectors | `check_rtl` (what `flux rtl test` runs), `compile_and_run` | `generator/harness_rtl` |
| what a harness is told and what it reports, in any language | `DesignSpec`, `CompositionSpec`, `HarnessRunResult` | `generator/harness_spec` |
| the evaluator contract and the backend registry (zigzag, timeloop, rtl, openroad, ChampSim) | the `Evaluator` ABI, `make_evaluator(name)` | `evaluator/abi` |
| tool fingerprints for cache keys and provenance | `toolchain_fingerprint` | `evaluator/abi` |

## Where things live

The tree follows four module types, so where a thing lives tells you what kind of thing it is:

| directory | what lives there |
|---|---|
| `generator/` | checking generated code: the RTL harness and its spec |
| `evaluator/` | scoring designs with real tools: the ABI, the tool adapters, validity, calibration |
| `mentor/` | knowledge that guides the rest: document corpus, mined facts, benchmarks |
| `applications/` | one folder per design problem: its document, its world package, its README |
| `core/` | the one loop (`core/loop`), the frontier arithmetic, the IR, the stores, the LLM layer, profiling, the TUI |
| `interfaces/` | how it is driven: the CLI |

`applications/` is the one that matters for growth: a design problem is a document (naming its
world when it has one), and the next problem gets the same shape (`<name>.problem.yaml`, and
`lib/` only if it needs a world) rather than new top-level directories. Adding a design problem should not change the top level at all.
