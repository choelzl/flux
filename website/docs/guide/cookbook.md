# Recipes

Which loop shape fits which problem. Start with `flux new NAME`, or copy a bundled application
from [`flux/applications/`](https://github.com/choelzl/flux/tree/main/flux/applications)
including its scripts.

## Pick a starting point

| You have | Start from | Model? |
|---|---|---|
| A program with settings to tune (flags, block sizes) | `flux new NAME`, commands using knob placeholders | No |
| A script that writes a design from settings | `flux new NAME`, a generator command and a search space | No |
| A hardware family you can spell from settings | Copy `applications/adder16/` | No |
| A function you want written and made fast | Copy `applications/primes/` | Yes |
| A hardware module you want written | Copy `applications/mul8/` | Yes |
| Only a description, a spec, some files | [`flux ask`](../start/ask.md) | Yes |

## Tune a program's settings

```yaml
statement: The fastest block size and thread count for my kernel.
flow:
  orchestrate:
    space:
      block: [16, 32, 64, 128, 256]
      threads: [1, 2, 4, 8]
  test: "{python} {home}/check.py {block} {threads}"
  measure:
    bench:
      command: "{home}/run.sh --block {block} --threads {threads}"
      metrics: [time_ms]
objectives: [{metric: time_ms, direction: minimize}]
budget: {workers: 1}
```

- **Time alone.** Set `budget.workers: 1`, and pin the program's own threads if it has any.
  Measured side by side, runs disturb each other: a block size can look many times better
  than it is.
- **Keep the gate** even when correctness seems obvious: a setting that crashes or returns the
  wrong answer is refused before it is timed.
- **A knob is any placeholder:** `{block}` works in any command, and in a generator script.

## Choose a search for the space

| The space | Policy | Options worth setting |
|---|---|---|
| Up to a few hundred points | `sweep` (the default) | `batch_size` |
| Large, and better neighbours are found step by step | `gradient` | `steps`, `wave`, `patience`, `reach` |
| Large and rugged | `anneal` | `steps`, `temperature`, `cooling`, `seed` |
| Large, and good settings combine | `genetic` | `population`, `generations`, `mutation` |
| Just a feel for the space | `montecarlo` | `samples`, `seed` |
| Two objectives traded against each other | `pareto` | `budget`, `reference`, `scale` |
| A model proposing points from what was measured | `model` | `rounds`, `batch_size`, `shown` |

Phases run in order, each from where the last ended:

```yaml
flow:
  orchestrate:
    space: {block: [16, 32, 64, 128], order: [ijk, ikj, jki]}
    policy:
      - {name: coarse, policy: sweep, knobs: [block]}
      - {name: fine, policy: gradient, hold: [block], steps: 20}
```

Every option is in [Search policies](../reference/search.md).

## Trade-offs and limits

- **One limit, then the rest:** a `goal` on the first objective, then the next decides among
  the designs that meet it: "the smallest that makes 1 GHz".
- **A true trade-off:** two objectives without a goal and the `pareto` policy. The report
  shows the front; `balance: true` picks its knee.
- **A hard limit on a cost:** make it a check (refuse what exceeds it), a stage `cutoff`, or a
  policy `floor` (`{metric: storage_bytes, at: 98304}`).

## When a model writes the designs

- **It gets it wrong a lot:** give it the facts in `flow.knowledge` (a spec, reference code, a
  method note) and [skills](knowledge.md#skills). `flux log RECORD` shows what it was told and
  what it said.
- **Numeric hardware** (floating point, transcendentals, fixed point): use a golden model and
  `budget.prototype: true`. The model proves the algorithm in Python on every input first, and
  the loop spells it as RTL. A prototype too costly to be worth synthesising
  (`budget.prototype_cost_max`) is made cheaper before anything is built.
- **A coding agent instead:** `flow.generate: claude` (or `codex`, `opencode`). It writes in
  its own folder; the loop runs the gate and brings a failure back to the same session. With a
  prototype stage, it writes the prototype, which suits hard numeric functions better than
  asking for RTL directly.
- **Let it run:** a run goes on until you stop it, and each start resumes from the record.
  When nothing is left to try, it explores for a better design.
- **Steer it:** a note typed in the TUI (`f`), or in the web app's **Talk**, reaches the next
  prompt.

## Read the results

- `flux task run FOLDER --json answer.json`: the decision, the front and the refusals as JSON.
- `flux report RECORD`: an HTML report with the front, the best so far over time, and every
  design.
- `flux log RECORD`: every model and agent turn, for when a run did not go as you expected.
