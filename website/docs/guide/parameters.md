# Parameter reference

This page describes the public `problem.yaml` format. Defaults apply when a field is omitted;
CLI overrides take precedence for that run. Use [Configure a loop](build-your-own.md) for the
reason behind each choice and `flux task check FOLDER` to validate the finished document.

## Document fields

| Field | Values / default | Purpose |
|---|---|---|
| `statement` | Required nonempty string | What to build or optimize |
| `contract` | String; empty | Required interface and implementation restrictions |
| `language` | Inferred from known tools, otherwise `text` | Artifact extension: `python`, `systemverilog`, `verilog`, `cpp`, `yaml`, and others |
| `flow` | Map; defaults below | Generation, checking, measuring, search, and supporting steps |
| `objectives` | List | Measured limits and ordered preferences |
| `budget` | Map; defaults below | Work, time, repair, prototype, and concurrency controls |
| `params` | Map; empty | Task-specific settings provided as the `{params}` JSON file |
| `parts` | List of names, map of descriptions, or `decompose`; empty | Pieces of one artifact |
| `subtasks` | List of child folders/documents, or `decompose`; empty | Child problems, each with its own loop |
| `max_subtasks` | `4` | Maximum children when asking for decomposition |
| `workload` | Path or workload object; absent | Workload for evaluator-backed stages |
| `skills` | List of skill paths; empty | Extra instructions and resources |
| `ladder` | `true`, `false`, or settings map; absent | Enable/configure the part improvement ladder |

The id is the folder name, not a YAML field. Put the main document in `problem.yaml` and
alternate problems in `NAME.problem.yaml`. Each alternate has its own `out/<id>.NAME.db`.
Keep check, measurement, search, and knowledge settings under `flow`.

`parts` and `subtasks` describe different ways to split work; do not combine them.
A child inherits the parent's settings, with `flow`, `budget`, and `params` merged key by key.

## Flow settings

A step takes a name when no options are needed (`generate: claude`); otherwise use
`{by: claude, ...}` with that step's settings and agent options. Checks and measurements
have their own command/evaluator format.

| Field | Values / usual default | Purpose |
|---|---|---|
| `flow.validate` | `rules` (default), `model`, agent | Validate the problem before work |
| `flow.orchestrate` | `rules` (default), `given`, `model`, `tools`, agent, policy, phase list, or search settings | Decide the next experiment |
| `flow.orchestrate.dse` | `adaptive` guidance by default; values in [Search and agents](loop-shape.md#let-a-model-or-agent-explore-designs) | Emphasis for reasoned exploration and improvements |
| `flow.plan` | `off` (default), `model`, agent | Plan work within a pass |
| `flow.generate` | `model` (default), agent, `{command: ...}`, `{catalog: [paths...]}` | Produce candidates; a catalog is a nonempty list of file paths |
| `flow.test` | Command or ordered map of named checks | Establish correctness |
| `flow.measure` | Ordered map of named stages | Measure passing candidates |
| `flow.critique` | `off` (default), `model`, agent | Challenge a passing design or decision |
| `flow.select` | `objectives` (default), agent, `{finalists: N}` | Choose by objectives; normally confirm `3` finalists |
| `flow.calibrate` | `on` (default), `off` | Compare cheaper and more expensive stages |
| `flow.feedback` | `human` (default), `off` | Operator notes |
| `flow.knowledge` | Knowledge settings below, or `off` | Context and lessons used by models and agents |

### Check settings

Inside `flow.test.<name>`, use a command directly or this map:

| Field | Default | Purpose |
|---|---|---|
| `run` | Required | Command string or argument list |
| `count_re` | `(d+) failing` | One capture containing the number of failures; unmatched falls back to the exit status |
| `fail_re` | Absent | One match per failure; used instead of the default count pattern |
| `timeout_s` | `120` | Maximum duration of the check |

Exit `3` always means the candidate did not build. A check named `build` treats every nonzero
exit that way. Other checks count failures using their configured pattern and exit fallback.
Patterns read stdout and stderr; stderr alone does not fail a check. A positive printed count
can fail an exit-0 script. With both patterns supplied, `count_re` takes precedence.

### Measurement settings

Inside `flow.measure.<stage>`, use a known tool command or a map:

| Field | Default | Purpose |
|---|---|---|
| `command` | One of `command` / `evaluator` required | Command string or argument list |
| `evaluator` | Alternative to `command` | Registered backend, such as `zigzag` or `timeloop` |
| `metrics` | Inferred for known RTL commands | Names printed as `name=value`; required for ordinary custom commands unless using `metrics_re` |
| `metrics_re` | Derived from `metrics` | Map of metric name to regex with a numeric capture |
| `needs` | Inferred for known tools; otherwise empty | Executables required on PATH; missing tools skip the stage |
| `timeout_s` | `600` | Command timeout in seconds |
| `cutoff` | None | Condition or list of conditions to proceed to the next stage |
| `estimate` | Off | Optional screening before the measurement tool runs |

A stage's name is its map key. Its command must exit `0` for its numbers to be accepted.
All stages must provide the objective metrics; put cheap stages first and confirmation last.

A cutoff is `{metric: name, at: N}` (minimum), `{metric: name, below: N}` (maximum), or
`{metric: name, within: F}` (retain within the fraction of this run's best, `0 < F <= 1`).
A list of cutoffs must all pass. Metric direction matters for a relative cutoff.

An estimator takes `kind: surrogate|command|model` and `margin` (default `0.05`, a nonnegative
fraction of the limit). Only `kind: command` takes a `command`, which prints the stage's metrics.
The surrogate needs at least three measured rows. A model estimator requires a working model.
An estimate failing a limit beyond the margin skips the stage; it is never recorded as a
measurement from the real tool.

### Objective settings

| Field | Default | Purpose |
|---|---|---|
| `metric` | Required | A metric supplied by the measurements |
| `direction` | `maximize`; `minimize` or `maximize` | Which direction is better; state it explicitly for custom metrics |
| `goal` | None | Maximum for minimize, minimum for maximize; a feasibility limit |
| `stage` | Deepest for an absolute `goal`; otherwise unset | Stage at which the objective is judged |
| `unit` | Known metric's unit | Display unit for a custom metric |
| `tie` | `0.0` | Relative difference treated as a tie |
| `margin` | `0.0` | Minimum relative headroom for judging a goal on a shallower stage, before calibration |
| `balance` | `false` | Combine goal-less objectives at the frontier's knee |
| `keep` | None | Relative limit: fraction of the best measured gain to preserve |
| `above` | `0` | Baseline for a relative `keep` limit |

Goal-less preferences decide in written order. Limits decide feasibility first. A design that
misses a limit can be the closest standing design without becoming a feasible decision.
`keep` is an alternative to an absolute `goal`.
`goal` may also be a comparison string such as `">= 1000"` or `"<= 80"`, which supplies the
direction when it is omitted. The explicit numeric-goal examples are easier to compare.

### Knowledge settings

| Field | Default | Purpose |
|---|---|---|
| `files` | Empty | Specs, source files, PDFs, and notes relative to the document |
| `text` | Empty | Inline context |
| `sheet` | Absent | Knowledge-sheet file |
| `digest` | `model` | Agent or model that summarizes library papers |
| `lessons` | Off | `mined`, `off`, or agent for lessons from records |
| `off` | `false` | `off: true` alone disables reading; `knowledge: off` is shorter |

The loop's `library/` and the operator's shared library are available while knowledge is on.
Use `digest:` for the paper summarizer, not `by:` on the knowledge step.

## Search settings

Inside `flow.orchestrate`, a knob search takes `policy`, `space`, and optionally `seeds`.
A policy can be a name or `{policy_name: {options...}}`. See the complete examples in
[Search and agents](loop-shape.md#search-a-space-of-settings).

| Setting | Purpose |
|---|---|
| `space` | Knob to ordered choices; nested components and conditional values are supported |
| `seeds` | Initial points; omitted knobs use their first choice, and the first seed is home |
| `policy` | Search algorithm, its configuration, or configured phases |

A phase's common options are `name` (label), `knobs` (move only these), `hold` (keep these
fixed), `metric` and `direction` (phase objective), `floor` (admission limit), and `margin`
(minimum improvement, default `0`). Knob filters accept globs such as `prefetch.*`.
A floor is `{metric, at}` or `{metric, keep, above}`.

| Policy | Additional options and defaults |
|---|---|
| `sweep` | `batch_size: 0` (the policy's whole walk in one yield) |
| `montecarlo` | `samples: 24`, `batch_size: 8`, `seed: 0` |
| `gradient` | `steps: 16`, `wave: 0`, `patience: 1`, `budget: 0`, `reach: adjacent` |
| `anneal` | `steps: 32`, `seed: 0`, `temperature: 1.0`, `cooling: 0.9` |
| `genetic` | `population: 8`, `generations: 6`, `seed: 0`, `mutation: 0.3` |
| `pareto` | `budget: 24`, `wave: 6`, `reference: []`, `scale: []`, `reach: adjacent`; at least two objectives |
| `model` | `batch_size: 4`, `rounds: 8`, `shown: 40`, plus common phase options; `agent` can supply a coding agent for the policy |
| `control` | `keep: []`; compare home with only these incumbent knobs retained |
| `phases` | Ordered `phases: [...]` list, with each phase's own policy and settings |

The policy's batch size groups its proposals; the document's `budget.batch` controls how many
search candidates a pass actually carries. They are not the number of simultaneous passes.
`reach` accepts `adjacent` or `any`. For `gradient`, `budget: 0` means no separate point cap.

A custom search uses `orchestrate: {command: "... {history} {state} {params}", timeout_s: N}`.
It prints one JSON object containing `candidates` (each with `name`, `artifact`, `knobs`, `why`),
optional `lessons`, `not_established`, `conclusion`, and `done`. Its state file and history let
it continue across rounds. A custom search command's completion is separate from the outer
campaign's pass limit. See the repository's
[extension guide](https://github.com/choelzl/flux/blob/main/docs/extending.md) for protocol details.

## Agent options

Place these beside `by: claude|codex|opencode` on an agent-backed step. A custom agent uses
`by: {command: [...]}` and can supply its own `resume` command.

| Option | Default | Purpose |
|---|---|---|
| `bin` | Agent executable on PATH | Override a preset's program |
| `args` | Preset's arguments | Override the preset's command arguments |
| `timeout_s` | `1800` | Maximum duration of one agent invocation |
| `session` | `turn` for decision steps | `turn` or `pass`; generation's session lifetime is fixed and does not accept this setting |
| `questions` | `decide` | `decide`, `model`, or `operator`: who answers agent questions |
| `max_questions` | `2` | Question exchanges allowed for a draft |
| `wait_s` | `300` | Time waiting for an operator's answer |
| `probe` | `{gate: 20, stages: 3}` | Checks per turn; stage names can have individual budgets; `false` disables probes |
| `allow` | `[]` | Restore specific denied raw tools, e.g. `[yosys]`; `all` restores all |
| `output` | `text` | Output interpretation for an agent of your own |
| `resume` | Preset behavior | Resume command for a custom agent; receives `{session}` |
| `name` | Preset/program name | Name for a custom agent |

The `bin` and `args` options adjust a preset; put those arguments directly in `command` for a
custom agent. Keep executable paths and credentials in the machine or web settings when they
should not travel with the document.

## Baseline / pass 0

The optional top-level `baseline` setting runs checks and real measurements before pass 1
and before any parallel passes, if no successful baseline is recorded or its inputs, configuration,
or tools have changed. Unchanged restarts reuse a successful outcome without running the
preparation command, checks, or measurements again. Failed baselines are retried on the next
start, even when inputs are unchanged. It defaults to off. In the configurator, open
**Extra → Baseline / pass 0** to choose when it runs and its source.

```yaml
baseline: true                 # current project: run the existing scripts as written
# Or use an unchanged design file:
# baseline: {file: baseline.py}
# Or prepare the baseline with a command, using the normal command placeholders:
# baseline: {command: "{python} {home}/baseline.py {artifact}", timeout_s: 600}
```

A file is relative to the loop folder and is copied into the pass's working directory for
`{artifact}`; the original is left untouched. A command runs once with the first seed's knob
values, or the defaults (first choices), and may write `{artifact}` or prepare the project for
your scripts. With no file or command, scripts run as written; `{artifact}` is an empty
placeholder file, so use project paths such as `{home}/src` when measuring the current project.

Pass 0 calls no model or coding agent, authors no golden model, and performs no repairs.
Measurements bypass caches, estimators, and search cutoffs to exercise every available stage;
a failing check stops measurement, and a failing measurement stops later stages. Failures and
numbers remain in the run's logs and record. Normal passes continue afterward, and pass 0 does
not count toward `budget.passes` or `--passes`. Baseline source files can become starting designs;
project-only measurements provide no source artifact to resume as a candidate.

The fingerprint covers source files and data, parameters, the baseline file (including one outside
the loop folder), the loop document, tool builds, and package/build environment paths and flags.
Generated `out/`, `runs/`, and `workbench/`
folders, caches, and the library do not trigger a rerun. Reuse requires a persistent loop record;
without one, each invocation runs pass 0. Older records without a fingerprint run it once to
establish one. If inputs change during pass 0, the next start runs it again for those new inputs.
Graphs draw baseline measurements as gray reference lines, separate from the search points, with a
**Baseline (pass 0)** legend entry. Search points keep their group colors and requirement status.

For a tool check or baseline measurement without optimization, set `only: true`, for example
`baseline: {only: true}` or `baseline: {file: baseline.py, only: true}`. This explicitly ends the
run after pass 0 and needs no model or generation agent. A command's timeout defaults to 600
seconds; each check and measurement retains its own timeout.

## Budget

### Run length, repairs, and concurrency

| Field | Default | Purpose |
|---|---|---|
| `passes` | `0` | Run until stopped; positive values cap passes |
| `steps` | `24` | Work items within a pass |
| `budget_s` | None | Wall-clock budget within a pass, checked before each step |
| `repair_attempts` | `12` | Attempts to repair a draft |
| `batch` | `1` | Search designs carried by one pass |
| `workers` | `0` | Automatic measurement concurrency (up to four, based on cores); use `1` for timed benchmarks |
| `parallel` | `1` | Passes at once; server admin cap also applies |
| `parallel_parts` | `1` | Parts drafted at once |
| `ahead` | `true` | Overlap a part's measurement with work on the next part |
| `screen_only` | `false` | Skip the costliest stage of a multi-stage chain |
| `max_depth` | `3` | Maximum nesting depth for sub-loops |
| `regenerate` | `[]` | Parts to draft again instead of using admitted starting points |
| `exploration_quota` | `0.0` | Minimum share of recorded search choices reserved for exploration, from `0` to `1` |
| `explore_every` | `4` | Periodic fresh generations rather than starting from the best |
| `explore_after` | `2` | Stalled passes before fallback exploration; adaptive agents can explore sooner |
| `explore` | `0` | At-rest/exploration counter used to send admitted designs back; usually managed by the loop |
| `cooldown_after` | `3` | Consecutive build failures before a part yields |
| `patching` | `true` | Repair using edits rather than full rewrites |
| `patch_context_lines` | `40` | Lines around a fault included in a patch prompt |
| `revert_after` | `3` | Failed compile repairs before reverting to the last good design |
| `regress_after` | `2` | Initial tolerance for consecutive worsening edits |
| `max_tolerance` | `4` | Maximum growth of that tolerance |
| `critique_rounds` | `1` | Allowed critic send-backs for a passing part |

Configure the number of confirmed finalists in `flow.select.finalists`, and calibration in
`flow.calibrate`. They are flow settings rather than public `budget` entries.

### Prototypes

Prototype work applies to problems with a compatible golden-model capability. It checks the
algorithm before spelling the target design. Use `false` for ordinary logic or script-generated
artifacts; choose `systemc` for a SystemC prototype. Setting it on does not create a golden model.

| Field | Default | Purpose |
|---|---|---|
| `prototype` | `true` | `true`, `false`, `python`, or `systemc` |
| `prototype_attempts` | `30` | Initial direct-model prototype attempts |
| `prototype_patience` | `8` | Extra attempts earned per new best |
| `prototype_attempts_max` | `90` | Maximum expanded direct-model budget |
| `prototype_agent_attempts` | `8` | Initial coding-agent prototype attempts |
| `prototype_agent_patience` | `2` | Extra coding-agent attempts per new best |
| `prototype_agent_attempts_max` | `20` | Maximum expanded coding-agent budget |
| `prototype_table_max` | `64` | Largest module-level coefficient table |
| `prototype_cost_max` | `0` (effective default `2000`) | Cost ceiling before spelling; negative disables the ceiling |
| `prototype_shrink_attempts` | `8` | Attempts to make a verified prototype cheaper |
| `prototype_unmeasured_stop` | `4` | Consecutive unmeasurable attempts before ending this pass |

### Direct-model tools and context

These control Flux's direct model turns, rather than a coding agent's own token budget.

| Field | Default | Purpose |
|---|---|---|
| `structured` | `true` | Schema-constrained decoding where supported |
| `tools` | `true` | Enable tools during model turns |
| `compute_timeout_s` | `10.0` | Timeout for the model's compute command |
| `tool_hops` | `6` | Tool-call rounds before the model must answer |
| `hop_share` | `0.5` | Context-window share allowed for a tool round; `0` uses the turn's cap |
| `knowledge_share` | `0.5` | Context-window share available for static knowledge |
| `compact` | `rules` | `rules` or `llm` compaction of over-window context |
| `compact_share` | `0.6` | Window fraction that triggers compaction of old tool rounds |
| `tool_result_chars` | `40000` | Maximum tool-output characters handed to the model |
| `agent` | `[]` | Direct-model capabilities: `tools`, `orchestrate`, `plan`; also enabled through CLI `--agent` |
| `plan_file` | None | Saved loop plan to follow |

## Command placeholders

A command only receives placeholders meaningful for its step. Knob values additionally
supply `{knob}` placeholders from the search space.

| Placeholder | Meaning |
|---|---|
| `{artifact}` | Candidate artifact path; generator writes it, checks and measurements read it |
| `{home}` | Document folder |
| `{workdir}` | This candidate's working directory |
| `{python}` | Interpreter running Flux |
| `{name}`, `{part}` | Candidate name and current part |
| `{point}` | JSON file for the entire knob point, including nested components |
| `{params}` | JSON file containing top-level `params` |
| `{failure}`, `{attempt}` | Repair feedback and attempt number for a generator command |
| `{prompt}`, `{prompt_file}` | Brief text or its file for a custom agent |
| `{history}`, `{state}` | Recorded search history and persistent state for a search command |
| `{parts}` | JSON of child answers for composition |
| `{session}` | Session id in a custom agent's resume command |

## RTL golden model

Define `PORTS` and `golden(**inputs)` in the file supplied to `flux rtl test --golden`:

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
| `COUNT` | Random vectors (default `32`), in addition to generated corner cases |
| `SEED` | Random seed |
| `VECTORS` | Additional input dictionaries; expected outputs still come from `golden()` |
| `CLOCK` | Clock port for a clocked design, e.g. `"clk"` |
| `LATENCY` | Expected latency in cycles for a clocked design |
| `TOLERANCE_ULP` | Map of floating-point output port to allowed ULP error; absent means exact |

Match the ports and clocking in the contract and design. Floating-point bit-pattern ports
should be unsigned; reinterpret their bits in the golden function, rather than converting
an integer bit pattern numerically to a float. Derive expected values from the reference.

Known RTL stage commands include `flux rtl measure {artifact} --stage stat|synth|place|route`.
`stat` provides area and cell count; timed stages also provide frequency and power. Use
`--clock-ps N` to set the timing target. `flux tools` prints the full tool catalog.

## Improvement ladder

`ladder: true` enables default steps. A map can override these fields:

| Field | Default | Purpose |
|---|---|---|
| `steps` | `[sweep, take, import, depth, contender, redesign]` | Ordered improvement actions |
| `sweep` | `[2, 4, 8, 16, 24, 32]` | Pipeline register counts to compare |
| `depth_goal` | `0.8` | Target fraction of incumbent logic depth |
| `redesigns` | `2` | Alternative algorithms the ladder may request per run |
| `contender_reach` | `0.7` | Relative standing value for considering a contender |
| `contender_passes` | `2` | Passes a contender may spend on one incumbent |
| `stalls` | `2` | Ladder redesign passes allowed to return to the same result |
| `alone` | Deepest stage | Stage for measuring a part on its own |

The ladder applies when the problem supports its prototype and part operations. Its limits
bound those actions; they do not prohibit the adaptive orchestrator from choosing new experiments
or stop an unlimited campaign. Most first loops can omit it.
