# Document reference

Everything a loop's document, `problem.yaml`, can say, and the contract every command it names
must follow: arguments, placeholders, what to print, exit codes. It is written from the code;
`tests/unit/test_document_reference.py` fails when the code accepts a key this page does not
name.

Use [Configure a loop](build-your-own.md) for why you would choose each setting,
`flux task check FOLDER` to validate a document, and `flux task migrate FOLDER --write` to
upgrade an older one.

**On this page:** [the document at a glance](#the-document-at-a-glance) ·
[files and loading](#files-and-loading) · [document fields](#document-fields) ·
[flow settings](#flow-settings) · [search settings](#search-settings) ·
[agent options](#agent-options) · [objectives](#objective-settings) · [budget](#budget) ·
[baseline](#baseline-pass-0) · [parts and sub-loops](#parts-and-sub-loops) ·
[how commands run](#how-commands-run) · [command placeholders](#command-placeholders) ·
[command contracts](#command-contracts) · [built-in tools](#built-in-tools) ·
[RTL golden model](#rtl-golden-model) · [improvement ladder](#improvement-ladder) ·
[common refusals](#common-refusals)

## The document at a glance

Only `statement` is required. A complete document, with every section and where it is
described:

```yaml
statement: Count the primes below n, as fast as possible.   # what to make (required)
contract: >-                                                # rules every design must follow
  A Python module defining count_primes(n) that returns exactly how many primes are below n.
language: python                                            # the design's file type

flow:                                                       # who does each step
  orchestrate:                                              # what to try next: a search
    policy: sweep
    space: {method: [trial_division, sieve, slice_sieve]}
  generate: {command: "{python} {home}/render.py {artifact} {method}"}   # writes each design
  test:                                                     # checks, in order
    build: "{python} -m py_compile {artifact}"
    correctness: {run: "{python} {home}/check.py {artifact}", timeout_s: 60}
  measure:                                                  # measurements, cheapest first
    quick:
      command: "{python} {home}/bench.py {artifact} 50000"
      metrics: [time_ms]
      cutoff: {metric: time_ms, below: 20}                  # a gate before the next stage
    full: {command: "{python} {home}/bench.py {artifact} 1000000", metrics: [time_ms]}
  select: {finalists: 2}                                    # how many climb to the costly stage

objectives:                                                 # what decides
  - {metric: time_ms, direction: minimize, goal: 20}        # at most 20 ms: a limit

budget: {passes: 5, workers: 1}                             # how much to spend
params: {n_max: 1000000}                                    # your own settings, as {params}
```

## Files and loading

- **The folder is the loop.** Its name is the loop's id: letters, digits, `_`, `-` and `.`,
  not starting with `.`. There is no `id` key; saying one is refused.
- **The document** is `problem.yaml` (or `problem.yml`, `problem.json`). Alternates beside it
  are `NAME.problem.yaml`; each has its own record, `out/<id>.NAME.db`.
- **Beside it:** your scripts, `library/` (papers and notes the model reads), `workbench/`
  (the agents' own tools and notes, kept across runs), `skills/` if you name them, and `out/`
  (the record, the cache and the decided design, written by Flux).
- **Parsing:** a YAML or JSON error reports its line and column; a document that is not a
  mapping is refused. Unknown keys are refused with a "did you mean" hint.
- **Older documents** are never upgraded silently: `flux task migrate FOLDER` shows what
  changes and `--write` saves it.
- **Normal form:** equivalent spellings (`critique: model` and `{by: model}`) load to the same
  document and share one record.

## Document fields

| Field | Values / default | Purpose |
|---|---|---|
| `statement` | Required non-empty string | What to build or optimize |
| `contract` | String; empty | Interface and rules every design must follow (names, ports, what is forbidden) |
| `language` | String; inferred, else `text` | The design's file type (table below) |
| `flow` | Map; defaults below | Who does each step: search, generation, checks, measurements, and the supporting boxes |
| `objectives` | List; empty | Limits and ordered preferences ([objectives](#objective-settings)) |
| `budget` | Map; defaults below | Run length, repairs, concurrency, prototypes ([budget](#budget)) |
| `params` | Map; empty | Your own settings, handed to any command as the `{params}` JSON file |
| `parts` | List of names, map of descriptions, or `decompose` | Pieces of one artifact ([parts](#parts-and-sub-loops)) |
| `subtasks` | List of folders or inline documents, or `decompose` | Child problems, each its own loop ([sub-loops](#parts-and-sub-loops)) |
| `max_subtasks` | Integer; `4` | Most children when `subtasks: decompose` |
| `skills` | Folder or list of folders; empty | Skills (a folder with `SKILL.md`) the model and agents can load |
| `ladder` | `true`, `false`, or a map; absent | The part improvement ladder ([ladder](#improvement-ladder)) |
| `baseline` | `true`, `false`, or a map; absent | A reference measured before pass 1 ([baseline](#baseline-pass-0)) |

**`language`** sets the design file's extension, matched without case:

| Language | Extension | Language | Extension |
|---|---|---|---|
| `systemverilog` | `.sv` | `cpp`, `c++` | `.cpp` |
| `verilog` | `.v` | `c` | `.c` |
| `vhdl` | `.vhd` | `cuda` | `.cu` |
| `python` | `.py` | `opencl` | `.cl` |
| `text` | `.txt` | `rust` | `.rs` |
| `yaml` | `.yaml` | `markdown` | `.md` |
| `json` | `.json` | `shell`, `bash` | `.sh` |
| `chisel`, `scala` | `.scala` | anything else | `.` + the name, lower case, no spaces |

When `language` is absent it is inferred only from a check or stage that starts with a
built-in tool's full command (in practice the Verilator lint line, which gives
`systemverilog`); otherwise it is `text`. Say it explicitly.

## Flow settings

`flow` says who does each box of the loop's drawing. A box takes a word, an agent's name, or
`{by: <word or agent>, ...settings, ...agent options}`:

```yaml
flow:
  critique: claude                              # an agent, no options
  plan: {by: claude, session: pass}             # an agent with options
  validate: model                               # Flux's own model
  calibrate: off                                # a word
```

`claude`, `codex` and `opencode` are built in; a server can add agents of its own (named by
`FLUX_AGENTS`). The old form `{agent: X}` is refused: write `{by: X}`.

| Box | Values (first is the default) | Agent? | Purpose |
|---|---|---|---|
| `flow.validate` | `rules`, `model`, agent | yes | Review the document before the first pass (advisory) |
| `flow.orchestrate` | `rules`, `given`, `model`, `tools`, agent, or a search ([search settings](#search-settings)) | yes | What to try next |
| `flow.orchestrate.dse` | `adaptive`, `explore`, `improve`, `tune`, `finetune`, `variations`, or a policy name | | The emphasis that guides a model or agent ([search settings](#search-settings)) |
| `flow.orchestrate.scope` | `{list: ..., brief: ..., check: ..., timeout_s: 120}` | | The scopes of one whole design a pass may work on ([scope](#scope)) |
| `flow.plan` | `off`, `model`, agent | yes | Write the pass's plan, checked before use |
| `flow.generate` | `model`, agent, `{command: ...}`, `{catalog: [paths]}` | yes | Write each design |
| `flow.test` | A command, or an ordered map of named checks | never | Decide correctness ([checks](#check-settings)) |
| `flow.measure` | An ordered map of named stages | never | Put numbers on passing designs ([measurements](#measurement-settings)) |
| `flow.critique` | `off`, `model`, agent | yes | Object to a division, a part or a decision; never a veto |
| `flow.select` | `objectives`, agent, `{finalists: N}` | yes | Choose the decision; an agent only breaks ties |
| `flow.calibrate` | `on`, `off` | no | Compare a costly stage's numbers with the cheap stage's |
| `flow.feedback` | `human`, `off` | no | Take your notes from Talk into the next prompt |
| `flow.knowledge` | A map ([knowledge](#knowledge-settings)), or `off` | digest, lessons | What the model and agents read |

- **Words for `orchestrate` without a search:** `rules` (rules pick the work), `given`
  (a fixed order: `{given: [part_a, part_b]}`), `model` (the model picks), `tools` (the model
  with tools: standings, history, decisions, knowledge), or an agent. Add `dse: <emphasis>`
  to guide a model or agent: `adaptive` (default), `explore`, `improve`, `tune`, `finetune`,
  `variations`, or a policy name (`sweep`, `montecarlo`, `anneal`, `gradient`, `genetic`,
  `pareto`) as an emphasis. `dse` guides; it never forbids.
- <a id="scope"></a>**`orchestrate.scope`** (D1022), for a loop of one whole design (no parts,
  sub-loops or knobs): each pass works on one scope of it. `list` is a command printing the scopes,
  one a line, its name first; `brief`, given `{scope}`, prints what the design agent is told of it;
  `check`, given `{scope}` and `{artifact}`, checks the scope on a design that passed the gate
  (`N failing of M`, or its exit). Each pass the orchestrator picks its direction and a scope in one
  decision, seeing which scopes the latest passes took and how their checks ended (the rules: each
  in turn). While the scope's check fails, the design goes back to the same agent with the failures,
  until it is clean or the pass's `steps` are spent; every design that passes the gate is kept and
  measured as usual. For a spec kept in the loop's folder: `list: fluxpreset ndf list --spec
  {home}/spec`, `brief: fluxpreset ndf clauses {scope} --spec {home}/spec` (it names the units that
  depend on it), and `fluxpreset ndf scope {scope} --spec {home}/spec` lists the unit and its
  dependents for a check of your own.
- **`generate`** takes exactly one form. `{command: ...}` is your script
  ([generator contract](#generator-command)); `{catalog: [a.py, b.py]}` reads the listed files
  (relative to the folder) in turn; `model` and agents write the design from the brief. A
  generate agent keeps one session per part through its repairs; `session` is refused here.
  A generator command has no `timeout_s` of its own: it uses the largest check timeout.
- **`test` and `measure` are never an agent's.** `by:` on them is refused: they establish
  facts.
- **`select: {finalists: N}`** is how many designs climb to each costlier stage (default `3`).
  `{by: claude, finalists: 2}` adds an agent that breaks ties among designs the objectives
  cannot separate; the objectives' choice stands otherwise.
- **`plan`** is checked before use; a plan that fails the check falls back to the document's
  order.

### Check settings

`flow.test` is one command (a check named `test`) or an ordered map. Checks run in the order
written; the first that reports failures refuses the design and the rest do not run.

```yaml
test:
  build: "{python} -m py_compile {artifact}"                  # a bare command
  correctness:                                                # or a map
    run: "{python} {home}/check.py {artifact}"
    timeout_s: 60
```

| Field | Default | Purpose |
|---|---|---|
| `run` | Required | Command string or argument list |
| `count_re` | `(\d+) failing` | One capture group holding the number of failures |
| `fail_re` | Absent | One match per failure; used instead of the default count pattern |
| `timeout_s` | `120` | Seconds before the check is stopped |

Check names are a letter, then letters, digits, `_` and `-`. A check named `build` uses no
default pattern: any non-zero exit means the design did not build. `count_re` or `fail_re`
at the level of `flow.test` (not inside a check) is refused. How a check's output becomes a
verdict is in the [check contract](#check-command).

### Measurement settings

`flow.measure` is an ordered map of stages, cheapest first. A stage is a command or a map:

| Field | Default | Purpose |
|---|---|---|
| `command` | Required | Command string or argument list |
| `metrics` | Required unless `metrics_re` | Names printed as `name=value`, or typed entries (below) |
| `metrics_re` | Derived from `metrics` | Map of metric name to a regex with one capture group |
| `needs` | Inferred for `fluxpreset prog count`/`size`; else empty | Programs required on PATH; a missing one skips the stage ("NOT RUN") |
| `timeout_s` | `600` | Seconds before the stage is stopped |
| `cutoff` | None | A gate before the next stage, or a list of gates that must all pass |
| `estimate` | Off | Predict first, and skip designs the estimate already rules out |

A typed metric is `{name, type: number|dict, direction: minimize|maximize, unit, aggregate}`.
`type: dict` reads a whole set of named results printed as `name={"a": 1, "b": 2}`: each
finite value becomes `name.a`, `name.b`, and `name` holds their `aggregate`: `mean`
(default), `median`, `min`, `max`, `sum`, or `none`. Missing, `null` and non-finite values
stay unmeasured, never zero. Metric names are unique per stage; `evaluator:` stages are gone
and refused.

**Cutoffs** gate the next stage: `{metric: M, at: N}` keeps designs at least N,
`{metric: M, below: N}` at most N, `{metric: M, within: F}` within the fraction F
(`0 < F <= 1`) of this run's best on that stage. Exactly one rule per condition.

**Estimates:** `{kind: surrogate|command|model, margin: 0.05}`; `command` only with
`kind: command` ([estimate contract](#estimate-command)). The surrogate needs three measured
designs first. A design whose estimate misses a cutoff or a limit by more than the margin is
skipped and never measured; designs that run keep their estimate in the record.

**Between stages:** designs that pass a stage's cutoffs join its frontier; the
`select.finalists` best (spread along the frontier with several objectives) climb to the
next stage. The decision uses the deepest stage a design reached. Every stage should report
every objective's metric, unless a limit is pinned to a deeper stage with `stage:`; otherwise
the run says the question cannot be answered as posed. `--screen-only` stops after the first
stage.

### Knowledge settings

| Field | Default | Purpose |
|---|---|---|
| `files` | Empty | Files the model reads (specs, sources, notes, PDFs), relative to the folder |
| `text` | Empty | Inline context |
| `sheet` | Absent | A methods sheet appended to the text |
| `digest` | `model` | Who summarises the library's papers: `model` or an agent |
| `lessons` | `off` | Lessons drawn from the record: `off`, `mined`, or an agent |
| `off` | `false` | `off: true` alone turns reading off; `knowledge: off` is the same |

`knowledge: claude` is short for `{digest: claude}`. `by:` on the knowledge box is refused:
say `digest:`. While knowledge is on, the loop's `library/` and the server's shared library
are read; PDFs go through `pdftotext`.

## Search settings

A search tries points of a space of settings ("knobs"). `flow.orchestrate` is a search when
it is a policy name, a list of phases, or a map with `policy`, `space` or `seeds`:

```yaml
orchestrate:
  policy: {gradient: {steps: 16, patience: 2}}     # or: policy: gradient
  space:
    algorithm: [list_sieve, slice_sieve, odd_sieve]
    wheel: {values: [0, 1], when: {algorithm: [list_sieve]}}   # only moves for list_sieve
    prefetch:                                       # a component: prefetch.on, .distance, .degree
      optional: true
      distance: [1, 2, 4]
      degree: [1, 2]
    variant: {values: [baseline], from: "variants/*.py"}       # plus file stems found at load
  seeds:
    - {algorithm: list_sieve, wheel: 0}            # measured first; the first seed is home
```

- **`space`:** knob to an ordered, non-empty list of scalar choices. A map with `values` takes
  `when: {knob: choice or [choices]}` (the knob only moves when the condition holds; otherwise
  it sits at its first choice) and `from: glob` (adds the stems of matching files, relative to
  the folder, read at every load). A map with neither is a **component**: its knobs are named
  `component.knob`; `optional: true` adds `component.on`. Order numeric choices: local
  policies step to neighbours.
- **`seeds`:** points measured first; a missing knob takes its first choice.
- **A space must reach a command:** some command names a `{knob}` or `{point}`, or the
  generator is a command or a catalog. A model or agent generator with a space searches
  settings only and does not write designs.
- **Component knobs** reach commands through `{point}` only; `{component.knob}` is not
  substituted.

| Policy | Options and defaults |
|---|---|
| `sweep` | `batch_size: 0` (the whole walk at once) |
| `montecarlo` | `samples: 24`, `batch_size: 8`, `seed: 0` |
| `gradient` | `steps: 16`, `wave: 0` (all moves), `patience: 1`, `budget: 0` (no cap), `reach: adjacent` (or `any`) |
| `anneal` | `steps: 32`, `seed: 0`, `temperature: 1.0`, `cooling: 0.9` |
| `genetic` | `population: 8`, `generations: 6`, `seed: 0`, `mutation: 0.3` |
| `pareto` | `budget: 24`, `wave: 6`, `reference: []`, `scale: []`, `reach: adjacent`; needs two objectives |
| `model` | `batch_size: 4`, `rounds: 8`, `shown: 40`; written `{by: model, ...}` or `{by: claude, ...}` for an agent |
| `control` | `keep: []`: compare home with only these incumbent knobs kept |
| `phases` | `phases: [...]`: phases in order, each `{policy: ..., ...its options}` (default `gradient`); phases do not nest |
| `adaptive`, `explore`, `improve`, `tune`, `finetune`, `variations` | A model proposing points with that emphasis (`model`'s options) |
| `command` | Your own search: `orchestrate: {command: ..., timeout_s: 600}` ([contract](#search-command)) |

Every policy and phase also takes `name` (a label), `knobs` (move only these), `hold` (keep
these fixed; both accept globs such as `prefetch.*`), `metric` and `direction` (its own
target; default the first objective), `floor` (`{metric, at}` or `{metric, keep, above}`:
designs below it are refused), `margin` (minimum improvement, default `0`) and `dse` (an
emphasis, as above). `batch` is another name for `batch_size`. An unknown option is refused,
naming the policy's fields.

A policy's `batch_size` only groups its proposals. `budget.batch` is how many search designs
one pass carries; `budget.parallel` is how many passes run at once. Finishing a finite walk
does not stop an open-ended run: cap it with `budget.passes`.

## Agent options

Beside `by: <agent>` on any box an agent may work:

| Option | Default | Purpose |
|---|---|---|
| `timeout_s` | `1800` (or `FLUX_<NAME>_TIMEOUT_S`) | Most seconds for one turn |
| `session` | `turn` | `turn` or `pass`: a fresh session each turn, or one per pass; refused on `generate` |
| `questions` | `decide` | Who answers a question the agent asks: `decide` (nobody), `model`, `operator` |
| `max_questions` | `2` | Question exchanges per draft |
| `wait_s` | `300` | Seconds to wait for the operator's answer |
| `probe` | `{gate: 20, stages: 3}` | Checks per turn the agent may run with `flux probe`; per stage `{bench: 2}`; `false` turns them off |
| `allow` | `[]` | Give back denied raw tools, e.g. `[yosys]`, or `all` |
| `bin` | The agent on PATH (or `FLUX_<NAME>_BIN`) | The program to run |
| `args` | None (or `FLUX_<NAME>_ARGS`) | Extra arguments, added before the prompt |
| `output` | `text` | Your own agent: how its output is read (`text`, `opencode`, `claude`, `codex`) |
| `resume` | The preset's | Your own agent: the command that resumes a session (`{session}`, `{answer}`) |
| `name` | The program's | Your own agent: its name in the record |

An agent never compiles, simulates or tests by itself: those programs are denied (verilator,
yosys, openroad, sta, klayout, champsim, gcc, g++, cc, c++, clang, clang++, make, cmake,
ninja, pytest, bash, sh, `flux rtl`, `flux task`, `flux run`) unless `allow` gives them back.
It checks its file through the loop's own gate and stages with `flux probe`; `probe: false` keeps it
from running any of them itself (the loop alone checks and measures), and `budget.tools_at_once`
makes its probes wait for a free slot like the loop's own runs. The agent's
model is set on the machine (`FLUX_<NAME>_MODEL` or the web's Agents settings), not in the
document. Your own agent is `by: {command: [my-agent, "{prompt_file}", "{artifact}"]}`
([agent contract](#agent-command)).

## Objective settings

Each entry is a metric name or a map:

| Field | Default | Purpose |
|---|---|---|
| `metric` | Required | A metric a stage reports (`name` or `name.test` for a dictionary metric) |
| `direction` | `maximize`, or the stage metric's | `minimize` or `maximize` |
| `goal` | None | A limit: at most N when minimizing, at least N when maximizing; also `">= 1000"`, `"< 80"` |
| `stage` | The deepest stage for a goal | Where the goal is judged |
| `unit` | Known for common metrics, else the stage's | Display unit |
| `tie` | `0` | Relative band within which two designs tie and the next objective decides |
| `margin` | `0` | Extra headroom required when a goal is judged on a shallower stage |
| `balance` | `false` | Decide by the best trade-off (the knee) of the goal-less objectives |
| `keep` | None | A relative limit: keep this fraction (0..1] of the best gain over `above` |
| `above` | `0` | The reference for `keep` |

**How the decision is made:** a design that meets every limit beats one that misses any. Among
those that meet them, the goal-less objectives decide in the order written (within `tie`,
the next one decides). If none meets every limit, the one missing fewest, then by the
smallest shortfall, is reported as the closest, not as a decision. An unmeasured limit counts
as missed. `goal` cannot be combined with `keep` or `balance`; a dictionary metric with
`aggregate: none` is chosen by its tests (`d.test`), not by its parent.

## Budget

### Run length, repairs, and concurrency

| Field | Default | Purpose |
|---|---|---|
| `passes` | `0` | Passes to run; `0` runs until stopped |
| `steps` | `24` | Work items in one pass |
| `budget_s` | None | Wall-clock seconds for one pass, checked before each step |
| `repair_attempts` | `12` | Repairs of one draft before it is given up |
| `batch` | `1` | Search designs one pass carries |
| `workers` | `0` | Tool runs at once; `0` = half the cores, at most 4. Use `1` for timings |
| `parallel` | `1` | Passes at once (a server limit also applies) |
| `parallel_parts` | `1` | Parts drafted at once |
| `tools_at_once` | `0` | Checks and measurements at once across the whole loop -- passes and parts at once, and an agent's `flux probe` -- for a tool that breaks when two of its runs share the machine; `0` = no limit |
| `ahead` | `true` | Measure a part while the next one is drafted |
| `screen_only` | `false` | Stop after the first stage |
| `max_depth` | `3` | Deepest nesting of sub-loops |
| `regenerate` | `[]` | Parts to draft again instead of reusing admitted ones |
| `exploration_quota` | `0.0` | Least share (0..1) of search choices kept for exploring |
| `explore_every` | `4` | Every Nth draft starts fresh rather than from the best |
| `explore_after` | `2` | Stalled passes before exploring |
| `agent_continues` | `3` | Times a design agent's session is continued past its time limit; then the pass gives up, without a design, its notes on the record |
| `explore` | `0` | Managed by the loop each pass; a value here has no effect |
| `cooldown_after` | `3` | Build failures in a row before a part yields |
| `patching` | `true` | Repair with edits rather than full rewrites |
| `patch_context_lines` | `40` | Lines around a fault shown in a repair prompt |
| `revert_after` | `3` | Failed compile repairs before going back to the last good design |
| `regress_after` | `2` | Worsening edits in a row tolerated at first |
| `max_tolerance` | `4` | Most that tolerance grows |
| `critique_rounds` | `1` | Critic send-backs allowed for a passing part |

`finalists` is set with `flow.select.finalists` and calibration with `flow.calibrate`; as
`budget` keys they are refused. Only `exploration_quota` and `prototype` values are
type-checked; check the others with care.

### Prototypes

A prototype is the algorithm in Python (or SystemC) checked against the golden model before
the target design is written. It applies to problems with a golden model; use `false` for
script-generated designs. Setting it does not create a golden model.

| Field | Default | Purpose |
|---|---|---|
| `prototype` | `true` | `true`, `false`, `python`, or `systemc` |
| `prototype_attempts` | `30` | First model attempts |
| `prototype_patience` | `8` | Attempts earned per new best |
| `prototype_attempts_max` | `90` | Most model attempts |
| `prototype_agent_attempts` | `8` | First coding-agent attempts |
| `prototype_agent_patience` | `2` | Coding-agent attempts earned per new best |
| `prototype_agent_attempts_max` | `20` | Most coding-agent attempts |
| `prototype_table_max` | `64` | Largest module-level table allowed |
| `prototype_cost_max` | `0` (effective `2000`) | Cost ceiling before spelling; negative turns it off |
| `prototype_shrink_attempts` | `8` | Attempts to make a verified prototype cheaper |
| `prototype_unmeasured_stop` | `4` | Unmeasurable attempts in a row before the pass ends |

### Direct-model tools and context

For Flux's own model turns, not a coding agent's.

| Field | Default | Purpose |
|---|---|---|
| `structured` | `true` | Schema-constrained answers where supported |
| `tools` | `true` | Tools inside model turns |
| `compute_timeout_s` | `10.0` | Seconds for the model's `compute` tool |
| `tool_hops` | `6` | Tool rounds before the model must answer |
| `hop_share` | `0.5` | Share of the context window for one tool round |
| `knowledge_share` | `0.5` | Share of the window for static knowledge |
| `compact` | `rules` | `rules` or `llm`: how an over-full context is shortened |
| `compact_share` | `0.6` | Window share that triggers compaction |
| `tool_result_chars` | `40000` | Most characters of a tool's output given to the model |
| `agent` | `[]` | Model halves turned on: `tools`, `orchestrate`, `plan` (also `--agent`) |
| `plan_file` | None | A saved plan to follow |

## Baseline / pass 0

`baseline` runs the checks and the real measurements on a reference before pass 1, so
results can be read relative to it. It is off by default; pass 0 calls no model or agent,
writes no golden model and repairs nothing, and does not count toward `passes`.

```yaml
baseline: true                                   # the project as it is: your scripts as written
# baseline: {file: baseline.py}                  # an unchanged design file
# baseline: {command: "{python} {home}/baseline.py {artifact}", timeout_s: 600}
# baseline: {metrics: [{metric: area_um2, value: 120}, {metric: fmax_mhz, value: 1000, stage: place}]}
# add only: true to stop after pass 0
```

| Form | What pass 0 measures |
|---|---|
| `true` | Your scripts as written; `{artifact}` is an empty file, so use `{home}/...` paths |
| `{file: F}` | F (relative to the folder) copied to `{artifact}`; the original is untouched |
| `{command: C, timeout_s: 600}` | Whatever C writes to `{artifact}` ([contract](#baseline-command)); if it writes nothing, the project as it is |
| `{metrics: [...]}` | Nothing runs: the values are recorded as the reference |

- **`metrics` entries** are `{metric, value, stage}`: a finite number; `stage` defaults to
  the deepest; the metric must be one that stage reports; each stage/metric pair once.
  Provided values supply no design and are never the decision.
- **`only: true`** ends the run after pass 0 (a tool check without optimization).
- **Reuse:** a successful baseline on record stands while the document's `baseline:` section is
  unchanged -- whatever else changes (the folder's files, the tools, Flux). To measure it again,
  the loop's Settings › Maintenance › **Rerun baseline** (or `flux task run ... --rerun-baseline`)
  makes the next start, and only it, run pass 0. A failed one is tried again at the next start. Baselines bypass the cache, estimates and cutoffs.
- Baselines are references: drawn as gray lines, the base of relative values, never the
  decision or a search start. They are not inherited by sub-loops.

## Parts and sub-loops

**`parts`** divides one artifact: `parts: [decoder, encoder]`, a map of descriptions
(`{decoder: "the 8b10b decoder"}`), or `decompose` (the orchestrator divides it, at most 8).
Names are a letter, then letters, digits, `_`, `-`. Parts are drafted and checked one by one
and joined in order, separated by a blank line.

**`subtasks`** divides a problem into child loops: a list of folders beside the document
(`subtasks: [ops/recip, ops/exp]`), inline documents (maps with an `id`), or `decompose`.
Do not combine `parts` and `subtasks`.

- **A folder child** has its own `problem.yaml` (no `id`; the folder names it), its record is
  `<parent>/<child>`, and it shares the parent's `out/` and `workbench/`.
- **Inheritance:** a child takes the parent's `contract`, `language`, `flow` (including
  checks, measurements, space and knowledge), `objectives`, `skills`, `params`, `budget` and
  `ladder` unless it says them; `flow`, `budget` and `params` merge key by key, the child's
  winning. Not inherited: `statement` (each child says its own), `parts`, `subtasks`,
  `max_subtasks`, `baseline`, and a parent's `generate: {command}`.
- **Composition:** a parent's `generate: {command: "... {parts} {artifact}"}` composes the
  children's decided designs into the whole once every child has decided
  ([composition contract](#composition-command)). Without it the parent composes nothing.
- Give inherited `skills` as paths that resolve from the child too (or absolute ones), and
  write inline children in the long form (`{by: model}`).

## How commands run

Every command a document names (checks, stages, estimates, the generator, the search, the
baseline, the composition) runs the same way. Your own agent's command differs; see
[agent command](#agent-command).

- **Written as** a list of arguments or one string. A string is split like a shell would
  split it, but **no shell runs**: for a pipe or redirection, call one yourself:
  `["sh", "-c", "cat {artifact} | wc -l"]`.
- **`flux` first** runs Flux's own CLI with the same interpreter (`flux tools ...`).
- **`fluxpreset` first** runs one of [Flux's presets](#flux-presets) the same way
  (`fluxpreset rtl measure ...`).
- **Working directory** is `{workdir}`, the pass's trace folder, shared by every design in
  the pass. Name your files with `{home}/...`; relative paths do not reach the loop's folder.
- **Environment:** Flux's own, unchanged. Nothing is added.
- **Standard input:** inherited from Flux, not data. A command must not read stdin.
- **Output:** stdout and stderr are captured separately; checks and stages read both,
  joined. Line endings are normalised. Live shows the command, its folder, the ends of its
  output as it runs, and its exit.
- **Timeouts** stop the whole process group. A missing program is reported as "not found on
  PATH" before the run; `flux task check` lists them.
- **Placeholders** are replaced inside every argument, without quoting. At load, a
  `{name}` that is neither the loop's nor a knob is refused (an argument containing spaces,
  such as an `sh -c` script, is not checked). A placeholder with no value for that command is
  passed through as written.

## Command placeholders

| Placeholder | Value | Generator | Check, stage, estimate | Search | Baseline | Composition |
|---|---|---|---|---|---|---|
| `{artifact}` | Path of the design file (`<workdir>/<name><ext>`) | writes it | reads it | `""` | writes it | writes the whole |
| `{home}` | The document's folder (absolute) | ✓ | ✓ | ✓ | ✓ | ✓ |
| `{workdir}` | The pass's working folder | ✓ | ✓ | ✓ | ✓ | the compose folder |
| `{python}` | The interpreter running Flux | ✓ | ✓ | ✓ | ✓ | ✓ |
| `{name}` | The design's name (`mul8#3`, `slice_sieve`) | ✓ | ✓ | `search` | ✓ | the loop's id |
| `{part}` | The part being drafted, else `""` | ✓ | `""` | `""` | `""` | `""` |
| `{params}` | Path of a JSON file of `params` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `{knob}` | One knob's value (`True`/`False` for booleans) | search mode | ✓ | | first choices | |
| `{point}` | Path of a JSON file of every knob | search mode | ✓ | `""` | ✓ | `""` |
| `{failure}` | Why the last attempt was refused (text) | drafting | | | `""` | |
| `{attempt}` | `1`, `2`, ... | drafting | | | `1` | |
| `{history}`, `{state}` | Search history and the search's own state file | | | ✓ | | |
| `{parts}` | Path of a JSON map of the children's designs | | | | | ✓ |
| `{scope}` | The scope a pass works on, in `orchestrate.scope`'s `brief` and `check` | | | | | |
| `{prompt}`, `{prompt_file}`, `{session}`, `{answer}` | Brief and session, for [your own agent](#agent-command) | | | | | |

**File formats:**

- **`{params}`** is `params.json` in `{workdir}`: the document's `params` as a JSON object
  (a sub-loop's merged over its parent's), rewritten for every command.
- **`{point}`** is `<artifact>.point.json`: the knobs as a JSON object, components nested
  (`{"algorithm": "list_sieve", "prefetch": {"distance": 2}}`); an optional component that is
  off is left out.
- **`{failure}`** is the text itself, not a file: the last 4000 characters of the failing
  check's output (prefixed `failed at <check>:` with several checks), or a send-back reason.
  It is empty on the first attempt.
- **`{parts}`** is `parts.json` in the compose folder: `{"<child name>": "<absolute path of
  its decided design>"}`, in declared order.

## Command contracts

### Generator command

`generate: {command: ...}` writes one design to `{artifact}`. Its stdout is not the design.

**Drafting** (no space): each draft is checked; if the checks refuse it, the command runs
again with `{failure}` and `{attempt}` set, up to `budget.repair_attempts` times. A failing
command (non-zero exit, or exit 0 without the file) ends the repairs at once; so does
writing the same design twice.

**Search** (with a space): the command runs once per proposed point, with that point's
`{knob}`s and `{point}`. A failing command drops the point; there is no repair. The design is
named after its knob values.

```python
# document: generate: {command: "{python} {home}/gen.py {artifact} {attempt} {failure}"}
import sys
out, attempt, failure = sys.argv[1], int(sys.argv[2]), sys.argv[3]   # failure is "" at first
design = "def count_primes(n):\n    ...\n" if not failure else "...a design that fixes it..."
open(out, "w").write(design)                                         # exit 0
```

### Check command

A check reads `{artifact}` and reports failures. Flux reads stdout and stderr together:

1. **Exit 3** means the design did not build, whatever was printed. For a check named
   `build`, any non-zero exit means that.
2. Otherwise, with `count_re` (default `(\d+) failing`): the **first** match's number is the
   failure count. With `fail_re` only: each match is one failure.
3. With no match: exit 0 passes, any other exit is one failure.

A printed count wins over the exit code: `2 failing` with exit 0 fails, and `0 failing` with
exit 1 passes. The first check with failures refuses the design; its output (last 4000
characters) is what Live shows and what a model or agent gets back to repair. A check's
debug output on stderr never fails it unless a pattern matches there.

```python
# document: test: {correctness: "{python} {home}/check.py {artifact}"}
import importlib.util, sys
try:
    spec = importlib.util.spec_from_file_location("design", sys.argv[1])
    design = importlib.util.module_from_spec(spec); spec.loader.exec_module(design)
except Exception as exc:
    print(f"did not load: {exc}"); sys.exit(3)                 # 3: did not build
cases = {10: 4, 100: 25, 1000: 168}
bad = [n for n, want in cases.items() if design.count_primes(n) != want]
for n in bad:
    print(f"FAIL n={n}: got {design.count_primes(n)}, expected {cases[n]}")
print(f"{len(bad)} failing of {len(cases)}")
sys.exit(1 if bad else 0)
```

### Measurement command

A stage reads `{artifact}` and prints its numbers. It **must exit 0**; on any other exit its
numbers are discarded and the design is refused at that stage. Flux reads stdout and stderr:

- **`name=value`**, at the start of a line or after a space: the **first** occurrence of each
  metric counts. `value` is a number (`1.5`, `-2e3`); `nan` and `inf` are unmeasured.
- **A JSON object** on a line of its own (it may span lines): its keys that are declared
  metrics are read. A `name=value` line overrides it.
- **A dictionary metric** is `name={"test": 12, "other": null}` (the last occurrence counts).
- **`metrics_re`**, if given, replaces the `name=value` patterns: one capture group each.

Exit 0 with no metric found refuses the design ("no metric matched in the output"). A stage
that reports only some of its metrics is still recorded with those it did. For timings, set
`budget.workers: 1`: runs at the same time disturb each other.

```python
# document: measure: {bench: {command: "{python} {home}/bench.py {artifact}", metrics: [time_ms]}}
import importlib.util, statistics, sys, time
spec = importlib.util.spec_from_file_location("design", sys.argv[1])
design = importlib.util.module_from_spec(spec); spec.loader.exec_module(design)
runs = []
for _ in range(5):
    t = time.perf_counter(); design.count_primes(1_000_000); runs.append((time.perf_counter() - t) * 1e3)
print(f"time_ms={statistics.median(runs):.3f} time_spread_ms={max(runs) - min(runs):.3f}")
```

**The measurement cache:** a design measured once by a stage is not measured again while
nothing that stage depends on changes: the design's text (or knobs), the stage's command,
metrics and patterns, `params`, the knobs the command names, the files in the folder (except
`out/`, `runs/`, `workbench/`, `library/` and the documents), the `{home}/...` files the
command names, and the builds of its programs and `needs`. Change any of them and it is
measured again. Only successful measurements are cached.

### Estimate command

`estimate: {kind: command, command: ..., margin: 0.1}` runs before the stage, once for each
design the cache does not hold, with the stage's placeholders and timeout. It prints the
stage's metrics in the same format. A non-zero exit or no numbers means no estimate: the
tool runs. A design whose estimate misses a cutoff or a limit by more than `margin` (a
fraction of the limit) is skipped and recorded as refused by the estimate.

```python
# document: estimate: {kind: command, command: "{python} {home}/estimate.py {artifact}"}
import sys
print(f"time_ms={len(open(sys.argv[1]).read()) * 0.01:.3f}")
```

### Search command

`orchestrate: {command: ..., timeout_s: 600}` is your own search. Each round, Flux writes
`{history}` and runs the command; the command answers with JSON on stdout.

**`{history}`** (`search-history.json` in `{workdir}`):

```json
{"measured": [{"name": "w4", "knobs": {"w": 4}, "stage": "bench", "metrics": {"time_ms": 3.1}, "by": "mine"}],
 "refused":  [{"name": "w9", "why": "failed at correctness: 2 failing"}],
 "guidance": "operator notes, if any",
 "round": 3}
```

**`{state}`** (`out/<record>.search-state.json`) is yours: read it if it exists, write it
every round; it survives restarts.

**The answer** is the last line of stdout that is a JSON object (or the whole stdout):

| Key | Meaning |
|---|---|
| `candidates` | `[{name, artifact, knobs, why, by}]`: `name` required; `artifact` is the design's **text** (not a path); `knobs` reach the checks and stages as `{knob}`/`{point}` |
| `lessons` | Lines added to the run's lessons |
| `not_established` | Lines added to the report |
| `conclusion` | What the search concluded, recorded when nothing is decided |
| `say` | A line printed with the round |
| `done` | `true`: this is the last batch |

No candidates also ends the search. Candidates go straight to the checks and stages; the
generator does not run for them. A non-zero exit or no JSON ends the search with a note.

```python
# document: orchestrate: {command: "{python} {home}/search.py {history} {state}", timeout_s: 300}
import json, os, sys
history, state_path = json.load(open(sys.argv[1])), sys.argv[2]
state = json.load(open(state_path)) if os.path.exists(state_path) else {"next": 1}
tried = {m["name"] for m in history["measured"]} | {r["name"] for r in history["refused"]}
cands = [{"name": f"w{w}", "artifact": f"WIDTH = {w}\n", "knobs": {"w": w}, "why": "next width"}
         for w in (state["next"], state["next"] + 1) if f"w{w}" not in tried]
state["next"] += 2
os.makedirs(os.path.dirname(state_path), exist_ok=True)
json.dump(state, open(state_path, "w"))
print(json.dumps({"candidates": cands, "done": state["next"] > 8}))
```

### Baseline command

`baseline: {command: ...}` runs once, before pass 1, with the first point (every knob's first
choice, overlaid with the first seed). It either writes the reference design to `{artifact}`,
or prepares the project and writes nothing (then the checks and stages run on the project,
through `{home}/...` paths). A non-zero exit records "baseline failed" and the loop goes on.

### Composition command

A parent with `subtasks` and `generate: {command: "{python} {home}/compose.py {parts} {artifact}"}`
runs it once every child has decided. `{parts}` maps each child to its decided design's
path; the command writes the whole to `{artifact}`. The whole goes to the measurements
directly (it is not checked again). A failure leaves no whole, with a note in the report.

```python
import json, sys
parts = json.load(open(sys.argv[1]))                       # {"recip": "/.../recip.sv", ...}
open(sys.argv[2], "w").write("\n".join(open(p).read() for p in parts.values()))
```

### Agent command

Your own coding agent: `by: {command: [my-agent, "--prompt", "{prompt_file}"], output: text,
resume: [...]}`. Write `command` as a **list**.

- Placeholders use Python formatting: `{prompt}` (the brief; very long briefs become an
  instruction to read the file), `{prompt_file}`, `{artifact}`, `{workdir}` (the session
  folder), `{part}`, `{name}`, `{python}`, `{home}`, `{workbench}`, `{probe}`; a resume
  command also gets `{session}` and `{answer}`. Write a literal brace as `{{` or `}}`; an
  unknown name refuses the draft.
- The brief goes to stdin unless the command names `{prompt}`, `{prompt_file}` or `{answer}`.
- It runs in its session folder with Flux's environment, plus `FLUX_<NAME>_ENV` and
  `FLUX_PROBE`. Default timeout 1800 s.
- It writes `{artifact}`. If it does not, its reply is read: a JSON `artifact` field, else the
  largest fenced code block.

## Built-in tools

`flux tools` prints the catalog; the configurator offers these as check types and
measurement tools. `{x}` other than the loop's placeholders are the tool's parameters. Most are
Flux's presets, `fluxpreset NAME ...` (D968, D970): nothing to copy into the loop's folder.

| Tool | Role | Command | Parameters (default) | Reports / passes |
|---|---|---|---|---|
| `rtl-lint` | check | `fluxpreset rtl lint {artifact}` | | each defect, `N failing`; exit 3 = did not parse |
| `rtl-golden` | check | `fluxpreset golden {artifact} --golden {golden}` | `golden` (`{home}/golden.py`) | `N failing` |
| `python-test-script` | check | `{python} {script} {artifact}` | `script` (`{home}/check.py`) | `N failing` |
| `custom-check` | check | `{command}` | `command` | `N failing`, else exit code |
| `python-cases` | check | `fluxpreset cases {artifact} --call {call} --reference {reference} --random {random}` | `call` (`main`), `reference` (`{home}/reference.py`), `random` (`100`) | `N failing of M` |
| `python-pytest` | check | `fluxpreset pytest {artifact} --tests {tests}` | `tests` (`{home}/test_design.py`) | `N failing of M` |
| `champsim-build` | check | `fluxpreset champsim build {artifact}` | | exit 3 = did not build |
| `champsim-check` | check | `fluxpreset champsim check {artifact} --traces {traces}` | `traces` (`{home}/traces`) | `N failing` |
| `rtl-stat` | stage | `fluxpreset rtl measure {artifact} --stage stat --clock-ps {clock_ps}` | `clock_ps` (`1000`) | `area_um2`, `cell_count` |
| `rtl-synth`, `rtl-place`, `rtl-route` | stage | `... --stage synth` / `place` / `route` | `clock_ps` (`1000`) | `fmax_mhz`, `area_um2`, `power_w`, `cell_count`, `path_ps`; route adds `drc_errors`, `wirelength_um` |
| `rtl-cycles` | stage | `fluxpreset rtl cycles {artifact} --golden {golden}` | `golden` (`{home}/golden.py`) | `latency_cycles`, `latency_min_cycles`, `cycles_per_result` |
| `prog-memory` | stage | `fluxpreset prog memory --build "{build}" --run "{run}" --runs {runs}` | `runs` (`3`) | `peak_rss_kb` |
| `bench-script` | stage | `{python} {script} {artifact}` | `script` (`{home}/bench.py`) | `time_ms` |
| `python-bench` | stage | `fluxpreset bench {artifact} --call {call} --args {args} --runs {runs}` | `call` (`main`), `args` (`()`), `runs` (`5`) | `time_ms`, `time_min_ms`, `time_spread_ms` |
| `prog-size` | stage | `fluxpreset prog size --build "{build}"` | `build` (`c++ -O2 -o {out} {artifact}`) | `text_bytes`, `data_bytes`, `bss_bytes` |
| `prog-time` | stage | `fluxpreset prog time --build "{build}" --run "{run}" --runs {runs} --warmup {warmup}` | `runs` (`10`), `warmup` (`1`) | `time_ms`, `time_ms_stddev`, `time_ms_min` |
| `prog-count` | stage | `fluxpreset prog count --build "{build}" --run "{run}"` | | `instructions`, `d1_misses`, `ll_misses`, `branch_mispredicts` |
| `champsim-run` | stage | `fluxpreset champsim run {artifact} --traces {traces} --warmup {warmup} --sim {sim}` | | `geomean_speedup` |
| `custom-stage` | stage | `{command}` | `command` | your `name=value` lines |

The golden test is Flux's own: `fluxpreset golden DESIGN --golden G` prints
failing vectors and `N failing of M` (exit 0 pass, 1 fail, 3 did not compile; `--extra F.sv` for a
module the design instantiates). `fluxpreset rtl lint DESIGN` prints each hardware
defect and `N failing` (exit 3 when it does not parse); `fluxpreset rtl measure DESIGN --stage stat|synth|place|route --clock-ps N`
runs OpenROAD-flow-scripts on ASAP7 to that stage and prints one line, `fmax_mhz=... area_um2=...
power_w=... cell_count=... path_ps=... worst_slack_ps=...` (`stat`: `area_um2`, `cell_count`;
`route` adds `drc_errors`, `wirelength_um`). `synth` times the netlist unplaced, `place` after
placement, `route` after routing (timing from global routing). `fluxpreset prog` builds with `--build`
(`{out}` is the program it builds), prints `name=value` lines, and exits 3 when the build fails
(before D969 it was `flux prog`; `flux task migrate` rewrites it). `fluxpreset bench DESIGN.py --call f --args "(1000,)"`
loads the design, times `f(1000)` over `--runs` (after `--warmup`) and prints `time_ms` (the median),
`time_min_ms` and `time_spread_ms`; exit 3 when the design does not load, 1 when it raises.
`fluxpreset cases` calls the design's function on cases -- a JSON table (`[args]` or `{"args", "kwargs",
"expected"}`), the reference module's `CASES`, and `--random N` draws of its `inputs(rng)` -- and
compares each result with the expected value or the reference function's (floats within `--tol`).
`fluxpreset pytest` copies the design as `design.py` beside your tests and counts the failing ones.
`fluxpreset rtl cycles` runs a clocked design (`CLOCK`) on the golden vectors, each started when the
last is done, and prints its latency: the most and fewest cycles, and the mean per result (not
overlapped throughput). `fluxpreset prog memory` prints the program's peak resident memory as the
kernel counts it, the most over `--runs`.
`fluxpreset ccases` is `cases` for a C or C++ function: built as a shared library and called through
ctypes with a scalar `--sig`. `fluxpreset sanitize` builds a program with AddressSanitizer and
UndefinedBehaviorSanitizer and counts the distinct errors a run hits. `fluxpreset accuracy` reports how
far a function is from its reference: `max_abs_error`, `mean_abs_error`, `max_rel_error`,
`mean_rel_error`, `max_ulp` (float64 steps). `fluxpreset rtl equiv` proves a design equal to a reference
module (a Yosys miter, SAT; a clocked pair by induction from zero) or prints a counterexample.
`fluxpreset rtl breakdown` groups the synthesised cells' area: `area_seq_um2`, `area_arith_um2`,
`area_xor_um2`, `area_buf_um2`, `area_logic_um2`, `flop_count`. `fluxpreset rtl fmax` runs ORFS a few
times, each at the period the last one's slack points to, and prints the fastest clock that closed:
`fmax_closed_mhz`. `fluxpreset rtl power` records the golden vectors' switching in Verilator and has
OpenROAD report power with it: `power_w`, `power_internal_w`, `power_switching_w`, `power_leakage_w`.
`fluxpreset champsim stats` runs the prefetcher and the run without it on every trace: `l2_mpki`,
`l2_mpki_baseline`, `pf_coverage` (the share of misses removed), `pf_accuracy`, `pf_late`, `pf_useless`,
`pf_issued_pki`, `geomean_speedup`.
`fluxpreset timebound` fails a Python function slower than `--max-ms` (the fastest of `--runs`; one
stuck past the budget is stopped). `fluxpreset rtl energy` turns `rtl power` into per-result numbers:
`energy_pj_per_result`, `edp_pj_ns`, `cycles_per_result`, `time_per_result_ns`. `fluxpreset rtl path`
prints the critical path: `path_ps`, `path_levels`, `path_cell_ps`, `path_wire_ps`, `slack_ps` (its
cells on stderr). `fluxpreset systemc` translates a SystemC module to SystemVerilog through ICSC; alone
it checks that it translates, and `--then "golden --golden g.py"` or `--then "rtl measure --stage
stat"` runs that preset on the translation. `fluxpreset regress -- CMD` compares a command's
`name=value` with a baseline file (`--record` writes it; `--tol` percent, `--higher` for metrics where
more is better). `fluxpreset extract -- CMD` turns a tool's JSON, CSV or text into `name=value`
(`--pick PATH=name`, a dotted path, a column or a regex group).
`fluxpreset complexity` reads a design's source in any common language (lizard) and prints `loc`,
`functions`, `cyclomatic_max`, `cyclomatic_mean`, `tokens`, `max_function_loc`; with
`--max-cyclomatic N` it fails each function over N. `fluxpreset scaling -- CMD {n}` times a command at
doubling sizes (`{n}`; `--input "gen {n}"` feeds its stdin, `--build` makes `{out}` first), takes the
start-up time off and fits time ~ n^k: `scaling_exponent` (about 1 linear, 2 quadratic),
`scaling_r2`, `time_at_max_ms`, `max_n`; with `--max-exponent K` it is a check. `fluxpreset repeat
--n 5 -- CMD` prints each metric's median and `NAME_spread_pct`. `fluxpreset derive --expr
"adp=area_um2*path_ps" -- CMD` adds formula metrics (arithmetic, min, max, abs, sqrt, log, exp) to a
command's own.
`fluxpreset asl tests UNIT --spec {home}/spec` lists a PTO-style spec unit's AVS tests (the unit by id or
mnemonic, the spec a checkout kept in the application's folder); `fluxpreset asl run UNIT --spec
{home}/spec` runs them with ASLRef (several units at once: `run TADD TSUB`) the way the spec's release suite
does -- the model assembled once, the tests on `--jobs` threads (the cores; `--jobs 1`, or a spec without
the page runner: its `scripts/run-asl-test`, test by test), each failing test a line then `N failing of M`
(`--out FILE` writes each result as JSON; needs `aslref` on PATH or `--aslref`).
`fluxpreset ndf list --spec {home}/spec` lists each unit with its NDF clauses by modality and its test count
(`--classification` to narrow); `fluxpreset ndf clauses UNIT` prints the unit's clauses whole; `fluxpreset ndf
check UNIT --results FILE` reads `asl run --out`'s results: each MUST clause failed or uncovered (no test names it
or its unit) a line, then `N failing of M` (`--uncovered warn` lists without counting). It uses the NDF tool
(`ndf`, in the dev shell from nixchip), building the spec's index in a temporary folder each time;
`fluxpreset ndf scope UNIT` lists the unit and every unit depending on it.
`fluxpreset pto check DESIGN --units TADD --spec {home}/spec` checks a pyCircuit design against the spec's
ASL model: the design is one combinational datapath for the spec's elementwise units with ports `op` (8,
the unit's code from `fluxpreset pto ops`), `dtype` (5, the TileDataType's position), `a`, `b` (64) and
`y` (64), `fault` (1); each unit's reference vectors (special and seeded random values for each type of
8 bits or more) are the spec's answers from ASLRef, one run a unit, kept under `--cache`
(~/.cache/flux/pto-ref) by the spec's content -- a vector the spec's model asserts on is listed by
`fluxpreset pto ref UNIT` and not compared. Each wrong vector is a line, then `N failing of M`;
`--dependents` adds the units depending on them, `--build-only` only builds (the gate), `--measure`
prints `vectors_failing`, `units_passing`, `units`. Needs `pycircuit` and `aslref` (both in the dev shell).

## Flux presets

Flux's prebuilt checks and measurements (`flux/presets/`, D968): `fluxpreset NAME ...` in a
document, or in a shell inside the dev shell. `fluxpreset` alone lists them; `fluxpreset describe
[NAME]` prints their catalog entries; `fluxpreset NAME --help` describes one.

| Preset | Commands |
|---|---|
| `golden` | `fluxpreset golden DESIGN --golden golden.py [--extra F.sv] [--module M]` |
| `rtl` | `fluxpreset rtl lint DESIGN`; `fluxpreset rtl measure DESIGN --stage stat\|synth\|place\|route --clock-ps N`; `fluxpreset rtl cycles DESIGN --golden golden.py`; `fluxpreset rtl equiv DESIGN --reference ref.sv`; `fluxpreset rtl breakdown DESIGN`; `fluxpreset rtl fmax DESIGN --stage synth [--steps 6]`; `fluxpreset rtl power DESIGN --golden golden.py --stage place`; `fluxpreset rtl energy DESIGN --golden golden.py --stage place`; `fluxpreset rtl path DESIGN --stage place` |
| `systemc` | `fluxpreset systemc DESIGN.h --top M --golden golden.py [--then "golden --golden golden.py"]` |
| `timebound` | `fluxpreset timebound DESIGN.py --call F --args "(1000,)" --max-ms 50` |
| `regress` | `fluxpreset regress --baseline base.json [--tol 5] [--record] -- CMD` |
| `extract` | `fluxpreset extract [--format json\|csv\|regex] --pick PATH=name -- CMD` |
| `complexity` | `fluxpreset complexity DESIGN [--ext .c] [--max-cyclomatic 15]` |
| `scaling` | `fluxpreset scaling [--start 1000\|--sizes A,B,C] [--input "gen {n}"] [--build "cc -o {out} {artifact}"] [--max-exponent K] -- CMD {n}` |
| `repeat` | `fluxpreset repeat --n 5 [--warmup 1] -- CMD` |
| `derive` | `fluxpreset derive --expr NAME=EXPR [--expr ...] -- CMD` |
| `asl` | `fluxpreset asl tests\|run UNIT... --spec {home}/spec [--jobs N] [--out FILE]` |
| `ndf` | `fluxpreset ndf list\|clauses\|scope\|check UNIT --spec {home}/spec` |
| `pto` | `fluxpreset pto ops [--names]`; `fluxpreset pto ref UNIT...`; `fluxpreset pto check DESIGN --units UNIT... [--dependents] [--build-only\|--measure]` (`--spec {home}/spec`) |
| `cases` | `fluxpreset cases DESIGN.py --call F [--reference ref.py[:F]] [--cases cases.json] [--random N] [--tol R]` |
| `pytest` | `fluxpreset pytest DESIGN.py --tests test_design.py` (the design imported as `design`) |
| `ccases` | `fluxpreset ccases DESIGN.c --call F --sig "long(long,long)" --reference ref.py:F --random N` |
| `sanitize` | `fluxpreset sanitize --build "cc -O1 -o {out} {artifact}" [--run CMD]` |
| `accuracy` | `fluxpreset accuracy DESIGN.py --call F --reference ref.py:F --random N` |
| `prog` | `fluxpreset prog time\|count\|memory\|size --build "c++ -O2 -o {out} {artifact}" [--run CMD]` |
| `bench` | `fluxpreset bench DESIGN.py --call F --args "(1000,)" [--runs 5] [--warmup 1]` |
| `champsim` | `fluxpreset champsim build\|check\|run\|stats ARTIFACT --traces DIR [--warmup N --sim N]` |

**Every preset keeps one interface** (D970; `tests/unit/test_flux_presets.py` checks it):

| | |
|---|---|
| Arguments | `fluxpreset NAME [SUBCOMMAND] ARTIFACT [--option VALUE ...]`: the design file first, every other input an option with a default |
| stdin | Never read |
| stdout | A check: each failure on a line of its own, then `N failing` (or `N failing of M`) last. A measurement: `name=value` for each metric it reports |
| stderr | Diagnostics for a person; never the count or the numbers |
| Exit code | `0` passed or measured; `1` failed, or the measurement could not be taken; `2` wrong arguments; `3` the design did not build, parse or load |
| Files | Only in a temporary folder of its own; never in the loop's folder |

**Writing a preset** is adding a module `flux_presets/NAME.py` (or a package) with `main(argv) ->
int` (its command line) and `catalog() -> [entry]`: one [catalog entry](#built-in-tools) per check
or stage it offers, its `run` starting `fluxpreset NAME`. `flux tools`, the configurator's **Flux
presets** list and the loader's language and `needs` inference read those entries; nothing else
in Flux names a preset.

## RTL golden model

Define `PORTS` and `golden(**inputs)` in the file given to `--golden`:

```python
PORTS = [
    {"name": "x", "dir": "in", "bits": 16, "unsigned": True},
    {"name": "y", "dir": "out", "bits": 16, "unsigned": True},
]

def golden(x):
    return {"y": x}
```

| Setting | Purpose |
|---|---|
| `COUNT` | Random vectors (default `32`), besides generated corner cases |
| `SEED` | Random seed |
| `VECTORS` | Extra input dictionaries; expected outputs still come from `golden()` |
| `EXHAUSTIVE` | Every input combination (at most 20 input bits) |
| `CLOCK` | The clock port of a clocked design, e.g. `"clk"` |
| `LATENCY` | Cycles from `start` to `done` for a clocked design |
| `TOLERANCE_ULP` | Map of floating-point output port to allowed ULP error; absent means exact |

Match the ports and clocking in the contract and the design. Floating-point bit-pattern ports
are unsigned; reinterpret their bits in `golden()` rather than converting the integer to a
float. A Python prototype is checked against the same file with
`python -m flux_loop.golden_proto PROTOTYPE.py --golden golden.py` (prints `N failing of M`).

## Improvement ladder

`ladder: true` turns on the default steps. A map overrides these fields:

| Field | Default | Purpose |
|---|---|---|
| `steps` | `[sweep, take, import, depth, contender, redesign]` | Improvement actions, in order |
| `sweep` | `[2, 4, 8, 16, 24, 32]` | Pipeline register counts to compare |
| `depth_goal` | `0.8` | Target fraction of the incumbent's logic depth |
| `redesigns` | `2` | Different algorithms the ladder may ask for per run |
| `contender_reach` | `0.7` | Standing at which a contender is considered |
| `contender_passes` | `2` | Passes a contender may spend on one incumbent |
| `stalls` | `2` | Redesign passes allowed to come back to the same result |
| `alone` | The deepest stage | The stage that measures a part on its own |

The ladder applies when the problem supports prototypes and parts. Its limits bound those
actions; they do not stop an open-ended run. Most loops omit it.

## Common refusals

| Message (start) | Fix |
|---|---|
| `keys a problem document does not have: X (did you mean Y?)` | A misspelled or removed key; `flux task migrate` handles old ones |
| `a document does not say its id` | Remove `id:`; rename the folder instead |
| `` `statement` must be a non-empty string `` | Say what is to be made |
| `flow: X is not a box of the drawing` | Use one of the boxes in [flow settings](#flow-settings) |
| `flow.X: who works it is by -- {by: X}` | Write `{by: claude}`, not `{agent: claude}` |
| `... never delegated to an agent: it establishes facts` | `test` and `measure` are commands only |
| `flow.X: K is not a setting of this box or an agent's` | An option on the wrong box ([agent options](#agent-options)) |
| `timeout_s is an agent's option, not model's` | Agent options need `by: <agent>` |
| `flow.generate is "model", {command: [...]}, {catalog: [...]} ...` | One form only; no `timeout_s` on generate |
| `flow.dse is orchestrate: ...` / `flow.extract is knowledge: ...` | Old names: `orchestrate` and `knowledge.lessons` |
| `... says {x}, which is neither a knob ... nor the loop's` | A misspelled placeholder, or a knob not in the space |
| `flow.dse.space knobs (...) are said by no command` | Name a `{knob}` or `{point}` in a command, or generate with a command |
| `a command stage needs metrics ... or metrics_re` | Say what the stage prints |
| `a task divides into parts ... or into subtasks ..., not both` | Choose one |
| `budget keys [...] are not loop knobs` | See [budget](#budget); `finalists` and `calibrate` belong in `flow` |
| `pareto needs two objectives` | Add a second objective or choose another policy |
| `baseline takes file OR command OR metrics` | One source per baseline |
