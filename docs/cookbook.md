# Cookbook: which recipe for which problem

Each recipe says which `flux example` to start from and which lines of the problem document to
change. The document keys are in
[author_reference.md](../flux/core/loop/src/flux_loop/author_reference.md), and the extension
points in [extending.md](extending.md).

## Pick your starting point

| you have | start from | a model? |
|---|---|---|
| a program with settings to tune (flags, block sizes, hyperparameters) | `flux example tune NAME` | no |
| a script that writes a design from knobs (any language) | `flux example sweep NAME` | no |
| a hardware family you can spell from knobs | `flux example rtl-sweep NAME` | no |
| a function you want written and made fast | `flux example python NAME` | yes |
| a hardware module you want written | `flux example rtl NAME` | yes |
| only a description, a spec, some files | `flux ask "..." --file spec.pdf` | yes |

## Tuning: knobs into your own commands

```yaml
flow:
  orchestrate:
    space:
      block: [16, 32, 64, 128, 256]
      threads: [1, 2, 4, 8]
  test: "{python} {home}/check.py {block} {threads}"
  measure:
    bench: {command: "./run.sh --block {block} --threads {threads}", metrics: [time_ms]}
objectives: [{metric: time_ms, direction: minimize}]
budget: {workers: 1}
```

- **A stage that measures time runs alone.** Set `budget.workers: 1`, and pin the program's own
  threads if it has any. In the `tune` template, measuring 15 settings concurrently made block 64
  look 15x better than 256; measured alone, 256 was the fastest.
- The gate is what stops a fast wrong answer, so keep it even when correctness seems obvious.
  A setting that crashes or returns the wrong thing is refused before it is timed.
- A knob is any placeholder: `{block}` works in any command, and in a `generate` script.

## Searching: how many points you can afford

| the space | `flow.orchestrate` | fields worth setting |
|---|---|---|
| up to a few hundred points | `sweep` (the default) | `batch_size` |
| large, and one step at a time finds better neighbours | `gradient` | `steps`, `wave` (knobs moved per round), `patience`, `budget`, `reach: adjacent\|any` |
| large and rugged | `anneal` | `steps`, `temperature`, `cooling`, `seed` |
| large, and good settings combine | `genetic` | `population`, `generations`, `mutation`, `seed` |
| just a feel for the space | `montecarlo` | `samples`, `seed` |
| two objectives traded against each other | `pareto` (and a second objective) | `budget`, `reference`, `scale` |
| a model proposes points from what was measured | `model` | `rounds`, `batch_size`, `shown` |

Every policy also takes `knobs` (move only these) or `hold` (keep these at the incumbent),
`metric` and `direction` (its own objective), `floor` (refuse below a bar) and `margin` (an
improvement smaller than this is none). Phases run in order, each from where the last ended:

```yaml
flow:
  orchestrate:
    - {name: coarse, policy: sweep, knobs: [block]}
    - {name: fine, policy: gradient, hold: [block], steps: 20}
    - {name: ideas, policy: model, rounds: 2}
```

A policy of your own is a class in a file beside the document: `orchestrate: my_search:MySearch`
([extending.md](extending.md), section 4).

## Trade-offs and constraints

- **One goal, then the rest**: `objectives` in order. With a `goal` on the first, the decision is
  the best on the second among those that meet it: "the smallest that makes 1 GHz".
- **A true trade-off**: two objectives without a goal and `orchestrate: pareto`. The report shows the
  front.
- **A hard limit**: make it a gate (refuse what exceeds it) or a phase `floor`
  (`{metric: storage_bytes, at: 98304}`).

## When a model writes the designs

- **The model gets it wrong a lot.** Give it `flow.knowledge: {files: [...]}` (a spec, reference
  code, a paper) and `skills:`. Read `flux log <record>` to see what it was told and what it
  said.
- **Numeric hardware** (floating point, transcendentals, fixed point): `budget.prototype: true`
  with a golden model. The model proves the algorithm in Python on every input first, and the
  loop spells it as RTL bit for bit (inputs of at most 20 bits; above that, the model
  transcribes it). A FP16 GELU went this way from a plain prompt. When that design is sent
  back (it misses the goal, or the run explores after a rest), the loop reworks the
  PROTOTYPE: its hardware cost, estimated in seconds from the measured widths (multipliers,
  dividers, tables), is the score, and every input must still pass. The model is shown what
  the cost is made of. A prototype too costly to be worth synthesising
  (`budget.prototype_cost_max`) is made cheaper before anything is built.
- **Let it run.** A run goes on until you stop it (`flux stop`, Ctrl-C), and each pass resumes
  from the record. Once nothing is left to try, it explores for a better design with the goal
  held. `flux report <record>` shows how it moved.
- **Steer it.** Type a note in the TUI (`f`, or a line on the terminal of a run without it); it reaches the next
  prompt.
- **A coding agent instead of a model turn**: `flow.generate: {by: opencode|claude|codex}`.
  It gets a work directory and writes the file; the loop runs the gate and brings a failure back
  to the same session. With `budget.prototype: true` it writes the Python prototype instead, which
  the loop checks with `flux rtl proto` (every input in seconds). The loop spells the RTL. For hard numeric functions this
  works better than asking an agent for the RTL itself.

## Reading the results

- `flux task run DOC --json answer.json`: the decision, the frontier and the refusals, as JSON.
- `flux report <record>`: an HTML report beside the record, with the front, the best so far over
  time, and every candidate.
- `flux log <record>`: every model and agent turn, for when a run did not go as you expected.
