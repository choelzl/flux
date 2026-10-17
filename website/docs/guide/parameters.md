# Document

Everything a loop's document, `problem.yaml`, can say, and the contract every command it names
must follow: arguments, placeholders, what to print, exit codes. It is written from the code
and checked against it.

Use [Configure](build-your-own.md) for why you would choose each setting,
`flux task check FOLDER` to validate a document, and `flux task migrate FOLDER --write` to
upgrade an older one.

## The document at a glance

Only `statement` is required. A complete document; the numbered marks say where each part is
described:

```yaml
statement: Count the primes below n, as fast as possible.  # (1)!
contract: >-  # (2)!
  A Python module defining count_primes(n) that returns
  exactly how many primes are below n.
language: python

flow:  # (3)!
  orchestrate:
    policy: sweep
    space: {method: [trial_division, sieve, slice_sieve]}
  generate:
    command: "{python} {home}/render.py {artifact} {method}"
  test:  # (4)!
    build: "{python} -m py_compile {artifact}"
    correctness:
      run: "{python} {home}/check.py {artifact}"
      timeout_s: 60
  measure:  # (5)!
    quick:
      command: "{python} {home}/bench.py {artifact} 50000"
      metrics: [time_ms]
      cutoff: {metric: time_ms, below: 20}
    full:
      command: "{python} {home}/bench.py {artifact} 1000000"
      metrics: [time_ms]
  select: {finalists: 2}

objectives:  # (6)!
  - {metric: time_ms, direction: minimize, goal: 20}

budget: {passes: 5, workers: 1}  # (7)!
params: {n_max: 1000000}  # (8)!
```

1. What to make. The only required field.
2. Rules every design must follow: names, ports, what is forbidden.
3. Who does each box of the loop: here a search over three methods, and a script that
   writes each design ([flow settings](#flow-settings)).
4. The checks, in order; the first that fails refuses the design
   ([check settings](#check-settings)).
5. The measurements, cheapest first. `cutoff` decides which designs go on to the next stage;
   `select.finalists` how many climb ([measurement settings](#measurement-settings)).
6. What decides. A `goal` makes an objective a limit: here at most 20 ms
   ([objectives](#objective-settings)).
7. How much to spend ([budget](#budget)).
8. Your own settings, handed to every command as the `{params}` file.

## Files and loading

- **The folder is the loop.** Its name is the loop's id: 1 to 60 letters, digits, `_` and `-`.
  There is no `id` key; saying one is refused.
- **The document** is `problem.yaml` (or `problem.yml`, `problem.json`). Alternates beside it
  are `NAME.problem.yaml`; each has its own record, `out/<id>.NAME.db`.
- **Beside it:** your scripts, `library/` (papers and notes the model reads), `workbench/`
  (the agents' own tools and notes, kept across runs), `skills/` (read unless `skills:` says otherwise), and `out/`
  (the record, the cache and the decided design, written by Flux).
- **Parsing:** a YAML or JSON error reports its line and column; a document that is not a
  mapping is refused. Unknown keys are refused with a "did you mean" hint.
- **Older documents** are never upgraded silently: `flux task migrate FOLDER` shows what
  changes and `--write` saves it.
- **Normal form:** equivalent spellings (`plan: model` and `plan: {by: model}`) load to the same
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
| `skills` | `off`, a folder or list of folders, or `{from, off}`; `skills/` | Skills (a folder with `SKILL.md`) the model and agents can load ([skills](#skills)) |
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
  generate: claude                              # an agent, no options
  plan: {by: claude, session: pass}             # an agent with options
  validate: model                               # Flux's own model
  calibrate: off                                # a word
```

`claude`, `codex` and `opencode` are built in; a server can add agents of its own (named by
`FLUX_AGENTS`). The old form `{agent: X}` is refused: write `{by: X}`.

| Box | Values (first is the default) | Purpose |
|---|---|---|
| `flow.validate` | `rules`, `model`, agent | Review the document before the first pass (advisory) |
| `flow.orchestrate` | `rules`, `given`, `model`, `tools`, agent, or a search ([search settings](../reference/search.md)) | What to try next |
| `flow.orchestrate.dse` | `adaptive`, `explore`, `improve`, `tune`, `finetune`, `variations`, or a policy name | The emphasis that guides a model or agent ([search settings](../reference/search.md)) |
| `flow.orchestrate.scope` | `{list: ..., brief: ..., check: ..., timeout_s: 120}` | The scopes of one whole design a pass may work on ([scope](#scope)) |
| `flow.plan` | `off`, `model`, agent | Write the pass's plan, checked before use |
| `flow.generate` | `model`, agent, `{command: ...}`, `{catalog: [paths]}` | Write each design |
| `flow.test` | A command, or an ordered map of named checks; never an agent | Decide correctness ([checks](#check-settings)) |
| `flow.measure` | An ordered map of named stages; never an agent | Put numbers on passing designs ([measurements](#measurement-settings)) |
| `flow.select` | `objectives`, agent, `{finalists: N}` | Choose the decision; an agent only breaks ties |
| `flow.calibrate` | `on`, `off` | Compare a costly stage's numbers with the cheap stage's |
| `flow.feedback` | `human`, `off` | Take your notes from Talk into the next prompt |
| `flow.knowledge` | A map ([knowledge](#knowledge-settings)), or `off` (an agent may digest and draw lessons) | What the model and agents read |

The configurator's Graph names the boxes by what they do: `orchestrate` is **Search**,
`generate` **Design**, `test` **Check**, `measure` **Measure**, `select` **Choose**.

- **Words for `orchestrate` without a search:** `rules` (rules pick the work), `given`
  (a fixed order: `{given: [part_a, part_b]}`), `model` (the model picks), `tools` (the model
  with tools: standings, history, decisions, knowledge), or an agent. Add `dse: <emphasis>`
  to guide a model or agent: `adaptive` (default), `explore`, `improve`, `tune`, `finetune`,
  `variations`, or a policy name (`sweep`, `montecarlo`, `anneal`, `gradient`, `genetic`,
  `pareto`) as an emphasis. `dse` guides; it never forbids.
- <a id="scope"></a>**`orchestrate.scope`**, for a loop of one whole design (no parts,
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
  ([generator contract](../reference/commands.md#generator-command)); `{catalog: [a.py, b.py]}` reads the listed files
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

### Critics

A critic is a second opinion on one box, said as `critic:` inside it. Only four boxes take one:

| Box | What the critic judges | A rejection | `rounds` |
|---|---|---|---|
| `orchestrate` | The split into parts, or the pass's direction | The work is divided again with the objection | yes |
| `test` | A design the checks passed | The design fails; the objection is its note on the record | no |
| `measure` | A stage's numbers | The result is marked rejected and never chosen; it is not measured again | no |
| `select` | The pick | The decision is made again without it | yes |

```yaml
flow:
  orchestrate: {by: claude, critic: model}
  test:
    correctness: "{python} {home}/check.py {artifact}"
    critic: claude
  select: {finalists: 2, critic: {by: claude, rounds: 2, timeout_s: 600}}
```

A critic is `model`, an agent, your own agent (`{by: {command: [...]}}`), or
`{by: ..., rounds: N, ...agent options}`. `rounds` (1 to 5, default 1) is how many times the box
answers an objection, on `orchestrate` and `select` only. A rejection holds for later passes.
`budget.critique_rounds` bounds how often a test critic sends a passing design back; `0` turns
the critics off. The older `flow.critique` is refused; `flux task migrate` turns it into critics.

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
verdict is in the [check contract](../reference/commands.md#check-command).

### Measurement settings

`flow.measure` is an ordered map of stages, cheapest first. A stage is a command or a map:

| Field | Default | Purpose |
|---|---|---|
| `command` | Required | Command string or argument list |
| `metrics` | Required unless `metrics_re` | The names printed as `name=value` -- a number, or a dictionary (below) |
| `metrics_re` | Derived from `metrics` | Map of metric name to a regex with one capture group |
| `needs` | Inferred for `fluxpreset prog count`/`size`; else empty | Programs required on PATH; a missing one skips the stage ("NOT RUN") |
| `timeout_s` | `600` | Seconds before the stage is stopped |
| `cutoff` | None | A gate before the next stage, or a list of gates that must all pass |
| `estimate` | Off | Predict first, and skip designs the estimate already rules out |

A stage names its metrics only. A metric printed as a dictionary, `name={"a": 1, "b": 2}`
(or inside a JSON object of metrics), is read as one: each finite value becomes `name.a`,
`name.b`, a test each. Missing, `null` and non-finite values stay unmeasured, never zero. To rank
by the dictionary as a whole, its objective says so: `{metric: name, type: dict, aggregate: sum,
direction: minimize, unit: us}` -- `name` then holds the `aggregate` of its tests: `mean`
(default), `median`, `min`, `max` or `sum`; an objective may instead rank one test,
`{metric: name.a}`. Metric names are unique per stage; `evaluator:` stages are gone and refused.

**Cutoffs** gate the next stage: `{metric: M, at: N}` keeps designs at least N,
`{metric: M, below: N}` at most N, `{metric: M, within: F}` within the fraction F
(`0 < F <= 1`) of this run's best on that stage. Exactly one rule per condition.

**Estimates:** `{kind: surrogate|command|model, margin: 0.05}`; `command` only with
`kind: command` ([estimate contract](../reference/commands.md#estimate-command)). The surrogate needs three measured
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
| `files` | Empty | Files and folders the model reads (specs, sources, notes, PDFs), relative to the folder; a folder is each of its files |
| `text` | Empty | Inline context |
| `sheet` | Absent | A methods sheet appended to the text |
| `digest` | `model` | Who summarises the library's papers: `model` or an agent |
| `lessons` | `off` | Lessons drawn from the record: `off`, `mined`, or an agent |
| `workbench` | who digests | Who sums up the agents' `workbench/` when it changed: `off`, `model`, or an agent (`{by: claude, timeout_s: 600}` with its options); unsaid, whoever digests the papers |
| `hidden` | Empty | Files, folders or globs (relative to the folder) the AI agents may not read: tests, checks, a gate's files and their parameters; see [Hidden from AI](#hidden-from-ai) |
| `off` | `false` | `off: true` turns reading off (`lessons`, `workbench` and `hidden` may stand beside it); `knowledge: off` is the same |

`knowledge: claude` is short for `{digest: claude}`. `by:` on the knowledge box is refused:
say `digest:`. While knowledge is on, the loop's `library/` and the server's shared library
are read; PDFs go through `pdftotext`.

### Hidden from AI

`knowledge.hidden` keeps an agent from gaming the gate by reading it: the loop's own tools (its
checks, stages, scripts and probes) read these paths as ever, and a check's failure text still
reaches the agent; no agent and no prompt reads them.

```yaml
flow:
  knowledge:
    hidden: [tests/, golden.py, "params/*.json"]
```

Each path is relative to the loop folder, inside it, and never the document itself. Sub-loops
hide their parent's paths too, beside their own. An agent's own `hidden:` option adds paths for that
agent alone. Hidden paths are left out of the library, the knowledge files, the skills, the
workbench's index and `flux loop library`; `flux loop diff`, `gate` and `measure` refuse them.
Every agent turn's brief names them as off limits, and the agent's own permission settings refuse
them:

- **Claude Code:** `--settings` with `permissions.deny`: `Read(...)` and `Edit(...)` on each path
  (absolute, `//...`), and `Bash(...)` on a command that names it.
- **OpenCode:** `permission.read`, `permission.edit` and `permission.bash` deny patterns in its
  config (`OPENCODE_CONFIG_CONTENT`).
- **Codex:** no per-path rule a user can set; only the brief says so.

This stops an agent from targeting a path; it is no sandbox. A script the agent writes could still
open the file, so for secrets use the run's sandbox. In the web app, a file or folder is hidden from
the Files tab (Actions, Hide from AI), when it is added, or in the configurator's Knowledge box.

### Skills

A skill is a folder with a `SKILL.md` (front matter `name` and `description`, then the
instructions). The loop's `skills/` folder is read without a word in the document. The model
gets an index of them and a `skill` tool; every coding agent of the loop (the design agent, a
box's, a critic, the lessons' and the digests') finds them in its work directory
(`.claude/skills`, `.opencode/skills`, `.agents/skills`), together with the built-in
`flux-loop-commands` (how the loop's record is read with `flux loop ...`).

```yaml
skills: off                                  # none, not the built-in one either
skills: [skills, ../shared-skills]           # these folders instead of skills/
skills: {off: [flux-loop-commands, rtl-style]}   # skills/ and the built-in one, these two off
skills: {from: [../shared-skills], off: [old-style]}
```

## Objective settings

Each entry is a metric name or a map:

| Field | Default | Purpose |
|---|---|---|
| `metric` | Required | A metric a stage reports (`name` or `name.test` for a dictionary metric) |
| `direction` | `maximize`, or the stage metric's | `minimize` or `maximize` |
| `goal` | None | A limit: at most N when minimizing, at least N when maximizing; also `">= 1000"`, `"< 80"` |
| `stage` | The deepest stage for a goal | Where the goal is judged |
| `unit` | Known for common metrics | Display unit |
| `type` | `number` | `dict`: the metric is a dictionary of tests, ranked by `aggregate` |
| `aggregate` | `mean` with `type: dict` | How a dictionary's tests make its number: `mean`, `median`, `min`, `max`, `sum` |
| `tie` | `0` | Relative band within which two designs tie and the next objective decides |
| `margin` | `0` | Extra headroom required when a goal is judged on a shallower stage |
| `balance` | `false` | Decide by the best trade-off (the knee) of the goal-less objectives |
| `keep` | None | A relative limit: keep this fraction (0..1] of the best gain over `above` |
| `above` | `0` | The reference for `keep` |

**How the decision is made:** a design that meets every limit beats one that misses any. Among
those that meet them, the goal-less objectives decide in the order written (within `tie`,
the next one decides). If none meets every limit, the one missing fewest, then by the
smallest shortfall, is reported as the closest, not as a decision. An unmeasured limit counts
as missed. `goal` cannot be combined with `keep` or `balance`; a dictionary metric no objective
says `type: dict` for has no number of its own: an objective names one of its tests (`d.test`).

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
| `agent_continues` | `3` | Times a design agent's session is continued past its time limit (the last one is told so). Then the design as it stands is handed in if the gate passes it; else a final short turn (`agent_grace_s`) whose only job is the gate; else the last file a `flux probe gate` passed in this design's turns. Only with none of these does the pass give up, without a design, its notes on the record |
| `agent_grace_s` | a quarter of the agent's time limit, 300–600 s | The final turn after the continues: told the gate's failure on its file, one gate probe, no stages, no experiments; the last file the gate passed is offered to copy back. Its file is handed in when the gate passes it. `0` turns the final turn off (the passing file, if any, is still handed in) |
| `explore` | `0` | Managed by the loop each pass; a value here has no effect |
| `cooldown_after` | `3` | Build failures in a row before a part yields |
| `patching` | `true` | Repair with edits rather than full rewrites |
| `patch_context_lines` | `40` | Lines around a fault shown in a repair prompt |
| `revert_after` | `3` | Failed compile repairs before going back to the last good design |
| `regress_after` | `2` | Worsening edits in a row tolerated at first |
| `max_tolerance` | `4` | Most that tolerance grows |
| `critique_rounds` | `1` | Times a [critic](#critics) may send a passing design back; `0` turns critics off |

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
| `{command: C, timeout_s: 600}` | Whatever C writes to `{artifact}` ([contract](../reference/commands.md#baseline-command)); if it writes nothing, the project as it is |
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
  ([composition contract](../reference/commands.md#composition-command)). Without it the parent composes nothing.
- Give inherited `skills` as paths that resolve from the child too (or absolute ones), and
  write inline children in the long form (`{by: model}`).

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

## Moved to their own pages

Older links to this page land here.

- <a id="search-settings"></a>Search settings and policies: [Search policies](../reference/search.md)
- <a id="agent-options"></a>Agent options: [Agent options](../reference/agents.md)
- <a id="how-commands-run"></a><a id="command-placeholders"></a><a id="command-contracts"></a>How commands run, placeholders and contracts: [Commands and placeholders](../reference/commands.md)
- <a id="generator-command"></a><a id="check-command"></a><a id="measurement-command"></a><a id="estimate-command"></a><a id="search-command"></a><a id="baseline-command"></a><a id="composition-command"></a><a id="agent-command"></a>Each command's contract: [Command contracts](../reference/commands.md#command-contracts)
- <a id="built-in-tools"></a><a id="flux-presets"></a>Built-in tools and Flux presets: [Presets](../reference/presets.md)
- <a id="rtl-golden-model"></a>The RTL golden model: [Golden model](../reference/presets.md#golden-model)
- <a id="common-refusals"></a>Common refusals: [Common refusals](../reference/refusals.md)
