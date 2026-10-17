# problem.yaml keys, condensed

The full, current list is `website/docs/guide/parameters.md`, with the placeholders and command
contracts in `website/docs/reference/commands.md` (checked against the code). This
file is the subset you need most often; defaults in parentheses.

## Top level

| Key | Meaning |
|---|---|
| `statement` | Required. The ask, in prose. |
| `contract` | Rules every candidate keeps (names, ports, forbidden constructs). |
| `language` | Sets the extension: systemverilog `.sv`, verilog `.v`, vhdl, python, cpp, c, cuda, opencl, rust, yaml, markdown, json, shell, chisel/scala; anything else `.<name>` (prefetcher: `ini`). |
| `flow` | The boxes (below). |
| `objectives` | List; see Objectives. |
| `budget` | Map; see Budget. |
| `params` | Free map, reaches commands as `{params}` (params.json). |
| `parts` | List / map name -> description / `decompose` (max 8). Not with `subtasks`. |
| `subtasks` | List of child folders, inline maps with `id`, or `decompose`; `max_subtasks` (4). |
| `skills` | Unsaid: `skills/` beside the document, plus the built-in `flux-loop-commands` for every agent. `off` (none at all), a folder or list of folders, or `{from: [folders], off: [skill names]}` (a built-in one too). |
| `baseline` | `true`, `{file}`, `{command, timeout_s}`, `{metrics: [{metric, value, stage}]}`, `only: true`. |
| `ladder` | true / false / map. |

Refused: `id` (the folder is the id) and every old top-level form (`gate`, `stages`, `space`,
`seeds`, `knowledge`, `roles`, `generator`, `critique`, `decompose`, `world`, `hooks`).

## flow

| Box | Values (default first) |
|---|---|
| `validate` | rules, model, agent |
| `orchestrate` | rules, given, model, tools, agent, or a search; `dse:` adaptive/explore/improve/tune/finetune/variations; `scope: {list, brief, check, timeout_s: 120}` |
| `plan` | off, model, agent |
| `generate` | model, agent, `{command}`, `{catalog: [paths]}` |
| `test` | one command, or an ordered map of named checks `{run, count_re, fail_re, timeout_s: 120}` |
| `measure` | ordered map of stages `{command, metrics | metrics_re, needs, timeout_s: 600, cutoff, estimate}` |
| `select` | objectives, agent (tie-breaker only), `{finalists: 3}` |
| `calibrate` | on, off |
| `feedback` | human, off |
| `knowledge` | `{files, text, sheet, digest: model|<agent>, lessons: off|mined|<agent>, workbench: off|model|<agent>, hidden: [paths]}` or `off` (`workbench` unsaid: whoever digests; `hidden` also beside `off: true`) |

Any box: a word, an agent name (`claude`, `codex`, `opencode`, or a server agent), or
`{by: <who>, ...}`. Critics: `critic: <who>` on the box judged (orchestrate, test, select).

`cutoff: {metric, at|below|within}` (or a list) stops escalation. `estimate: {kind:
surrogate|command|model, margin: 0.05}` predicts a costly stage (surrogate needs 3 measured rows).

## Search (`flow.orchestrate`)

`space:` values: `[choices]` | `{values, when: {knob: [..]}}` | `{values, from: "glob"}` | a
component map (`optional: true` adds `<c>.on`; component knobs reach commands only via `{point}`).
`seeds:` list, the first is home. Every policy takes `name`, `knobs`, `hold` (globs), `metric`,
`direction`, `floor`, `margin`, `dse`.

| Policy | Options |
|---|---|
| sweep | `batch_size: 0` |
| montecarlo | `samples: 24, batch_size: 8, seed: 0` |
| gradient | `steps: 16, wave: 0, patience: 1, budget: 0, reach: adjacent` |
| anneal | `steps: 32, temperature: 1.0, cooling: 0.9` |
| genetic | `population: 8, generations: 6, mutation: 0.3` |
| pareto | `budget: 24, wave: 6, reference, scale` (needs 2 objectives) |
| model | `batch_size: 4, rounds: 8, shown: 40` |
| control | `keep: []` |
| command | your script: reads `{history}` `{state}`, prints `{candidates:[{name, artifact, knobs, why, by}], lessons, not_established, conclusion, say, done}` |

A list of phases chains policies.

## Agent options (on any `{by: <agent>}` box)

`timeout_s` (1800, or `FLUX_<NAME>_TIMEOUT_S`), `session: turn|pass`, `questions:
decide|model|operator`, `max_questions` (2), `wait_s` (300), `probe: {gate: 20, stages: 3}` or
`false`, `allow: [] | all`, `hidden: [paths]` (more paths this agent may not read); a custom agent
`by: {command: [...], output: text}` plus `bin`, `args`, `output`, `resume`, `name`.

## Hidden from AI (`knowledge.hidden`)

`flow: {knowledge: {hidden: [tests/, golden.py, "params/*.json"]}}`: files, folders or globs
relative to the loop folder (inside it, never the document) that no agent or prompt reads; the
loop's gate, stages and scripts still do. Sub-loops add their parent's. Each agent turn's brief
names them; Claude Code gets `--settings` deny rules (Read/Edit/Bash), OpenCode `permission`
deny patterns; Codex has no per-path rule (the brief only). The library, knowledge files,
skills, the workbench index and `flux loop library` leave them out; `flux loop diff|gate|measure`
refuse them.

## Objectives

`{metric, direction: maximize, goal, stage, unit, type: dict, aggregate: mean|median|min|max|sum,
tie: 0, margin: 0, balance, keep, above}`. `goal` may be written `">= 1000"`. Every goal is a
limit; among designs meeting all limits the goal-less objectives decide in order; with none
meeting them the closest is reported and nothing is decided.

## Budget

| Key | Default | | Key | Default |
|---|---|---|---|---|
| passes | 0 (until stopped) | | explore_every | 4 |
| steps | 24 | | explore_after | 2 |
| budget_s | none | | cooldown_after | 3 |
| repair_attempts | 12 | | revert_after | 3 |
| batch | 1 | | regress_after | 2 |
| workers | 0 (half the cores, max 4) | | max_tolerance | 4 |
| parallel / parallel_parts | 1 / 1 | | critique_rounds | 1 |
| screen_only | false | | patching | true |
| max_depth | 3 | | agent_continues | 3 |
| prototype | true/false/python/systemc | | prototype_attempts / _patience / _max | 30 / 8 / 90 |

Direct-model keys: `structured`, `tools`, `tool_hops` (6), `hop_share` (0.5),
`knowledge_share` (0.5), `compact`, `tool_result_chars` (40000), `plan_file`.

## Placeholders

`{artifact} {home} {workdir} {python} {name} {part} {params} {knob} {point} {failure}
{attempt} {history} {state} {parts} {scope}`; custom agents also `{prompt} {prompt_file}
{session} {answer} {workbench} {probe}`.
