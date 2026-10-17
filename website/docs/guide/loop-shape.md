# Search and agents

A loop separates **what to try next** (`flow.orchestrate`) from **how to make the design**
(`flow.generate`). The gate and measurement stages establish the results. You can change the
search or generator while keeping those checks, measurements, and objectives.

![The loop as the configurator's Graph draws it: Search, Design, Check, Measure and Choose on a ring, with the setup boxes beside it](../assets/web/graph-light.webp#only-light)
![The loop as the configurator's Graph draws it: Search, Design, Check, Measure and Choose on a ring, with the setup boxes beside it](../assets/web/graph-dark.webp#only-dark)

The configurator's Graph draws the loop as a ring: **Search** (`orchestrate`) chooses what to
try, **Design** (`generate`) makes it, **Check** (`test`) and **Measure** (`measure`) establish
the facts, and **Choose** (`select`) decides. A failed check goes back to Design. The setup
boxes beside the ring run before the passes or feed every prompt.

## Search a space of settings

Use a finite space when you can list meaningful choices. The generator and other commands
receive those choices as placeholders:

```yaml
flow:
  orchestrate:
    policy: sweep
    space:
      algorithm: [list_sieve, slice_sieve, odd_sieve]
      wheel: [0, 1]
    seeds:
      - {algorithm: list_sieve, wheel: 0}
  generate: {command: "{python} {home}/render.py {artifact} {algorithm} {wheel}"}
```

The seed is measured first; an omitted knob uses its first choice. The first seed is the
home point. Order numeric choices meaningfully because local search uses that order.

| Policy | How it chooses experiments |
|---|---|
| `sweep` | Enumerate distinct combinations; a useful baseline for a small space |
| `montecarlo` | Sample distinct random points |
| `gradient` | Follow measured improvements among nearby choices |
| `anneal` | Make local moves and sometimes accept worse points to escape a region |
| `genetic` | Evolve a population through selection and mutation |
| `pareto` | Explore trade-offs; requires at least two objectives |
| `model` | Ask a model to propose legal points from the space and measured history |
| `control` | Compare the home point with only some of the incumbent's knobs kept (`keep: [...]`) |
| `phases` | Run several of these in order, each with its own options |
| `command` | Your own search script: `orchestrate: {command: "...", timeout_s: 600}` ([contract](../reference/commands.md#search-command)) |

One pass normally carries one point. `budget.batch` lets it carry several; `budget.parallel`
runs several passes simultaneously. A policy finishing its finite walk does not, by itself,
stop an unlimited run. Use a pass cap for a bounded sweep.

Configure a policy's own options under its name:

```yaml
flow:
  orchestrate:
    policy:
      gradient: {steps: 16, patience: 2, reach: adjacent}
    space:
      block: [16, 32, 64, 128]
      order: [ijk, ikj, jki]
```

The [search reference](../reference/search.md) lists policy options and defaults.
For a multi-phase search, keep the space and put a `phases` policy around its ordered phases:

```yaml
flow:
  orchestrate:
    space: {block: [16, 32, 64, 128], order: [ijk, ikj, jki]}
    policy:
      phases:
        phases:
          - {policy: montecarlo, name: sample, samples: 8, seed: 0}
          - {policy: gradient, name: tune, knobs: [block], steps: 8}
```

### Conditional choices and components

Avoid measuring a knob that has no effect on a chosen algorithm:

```yaml
space:
  algorithm: [list_sieve, slice_sieve, odd_sieve]
  wheel: {values: [0, 1], when: {algorithm: [list_sieve]}}
```

This is the `space` inside `flow.orchestrate`. Inactive choices sit at their home values and
are not repeatedly measured as different designs. A nested component groups its knobs:

```yaml
space:
  prefetch:
    optional: true
    distance: [1, 2, 4]
    degree: [1, 2]
```

The search sees `prefetch.on`, `prefetch.distance`, and `prefetch.degree`. `{point}` provides
these as nested JSON to a generator. A choice can also use `from: "out/invented/*.sv"` beside
`values` to include filenames discovered when the document loads.

## Let a model or agent explore designs

A model or agent can invent algorithms and architectures without a predefined knob space.
The default guidance is adaptive: reasoned risks are welcome, and exploration can happen
before the incumbent stalls. Say who decides what to try next, and with which emphasis, in
`flow.orchestrate`; who writes the code is `flow.generate`, set apart:

```yaml
flow:
  orchestrate: {by: claude, dse: adaptive}
  generate: claude
budget:
  exploration_quota: 0.25
```

| `flow.orchestrate.dse` | Emphasis |
|---|---|
| `adaptive` | Choose local improvements or a new approach from the evidence |
| `explore` | Try materially different algorithms, architectures, or representations |
| `improve` | Improve the objectives through local or structural changes |
| `tune` | Favor parameter and implementation changes near promising designs |
| `finetune` | Favor small changes whose effect can be attributed |
| `variations` | Develop distinct versions of promising approaches |
| `sweep`, `montecarlo`, `anneal`, `gradient`, `genetic`, `pareto` | Apply the corresponding search emphasis to reasoning and prompts |

Here `dse` is prompt and choice guidance, not a hard ban on broader changes. It is distinct
from `policy`, which selects an actual search algorithm over a space. Prefer `policy: sweep`
for enumerating knob combinations; use `{by: claude, dse: explore}` to guide an agent's work.
The algorithm names all have implemented walks: `gradient` is coordinate descent over
discrete choices, rather than derivatives; `pareto` uses a tree guided by trade-offs across
the first two objectives. Without a space, these names guide the reasoning instead of
running those walks. Preferences influence choices but do not force every experiment to
follow them, and a script that writes a design does not read model prompts.

In the configurator, the whole search is the **Search** box on the Graph: a **Kind**, then a
**Policy**, then the **Settings to search** (each a name and its values); Extra only names it.

`exploration_quota` reserves a minimum share of recorded search choices for exploration,
including across parallel passes. It counts attempts, not elapsed seconds, successful designs,
or a guaranteed fraction of a finite grid. Use `0` for no enforced quota. Existing verified
candidates remain available when an experiment is worse or fails. Correctness checks and the
contract still apply to every new design.

Guidance reaches initial generation, repairs, prototype work, and later improvements. Logs
record the selected move and its reason. A finite budget or manual stop controls the run;
reaching a goal or keeping the same incumbent does not stop an unlimited run.

## Put agents in the right steps

Name an installed agent directly, and use `{by: ..., ...}` only when adding options:

```yaml
flow:
  validate: rules
  orchestrate: {by: claude, dse: adaptive}
  plan: {by: claude, session: pass}
  generate: {by: claude, timeout_s: 1800, probe: {gate: 20, stages: 3}}
  select: {by: claude, finalists: 2}
  knowledge: {digest: claude, lessons: mined}
```

`claude`, `codex`, and `opencode` are built in; a server can offer additional named agents.
They can plan, generate, and advise decisions. `flow.test` and `flow.measure` remain
commands: an agent cannot replace their factual results. Selection still uses
the objectives; an agent's advice is checked and can fall back to the rules.

A **critic** is a second opinion on one box: `critic:` under `orchestrate` (the split or the
direction), `test` (a design the checks passed), `measure` (a stage's numbers) or `select` (the
pick). It is `model`, an agent, or `{by: ..., rounds: N}`. A rejection is a verdict: the design
fails, the numbers are marked, or the pick is made again without it.

```yaml
flow:
  test:
    correctness: "{python} {home}/check.py {artifact}"
    critic: claude
  select: {finalists: 2, critic: {by: model, rounds: 2}}
```

See [critics](parameters.md#critics) for each box's rules.

A generation agent resumes one session for a part through its repairs until that part is
admitted; a later improvement starts fresh. Do not set `session` on `generate`. Decision
steps such as `plan` and a critic accept `session: turn` (default) or `session: pass`.

Agents can use `flux probe` to check their current file during a turn. `probe` budgets those
checks per turn. Direct use of certain raw tools is restricted by default; `allow` can give
specific tools back when that suits the task. See [agent options](../reference/agents.md).

For the direct model, use `orchestrate: {by: model, dse: adaptive}` and `generate: model`.
[Models and agents](models.md) explains endpoint and agent setup.

## Split a larger design

A design too large for one piece can be split into parts, or into sub-loops whose decided
designs a parent composes: see [Split a larger design](parts.md).
