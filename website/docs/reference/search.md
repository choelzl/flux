# Search policies

What `flow.orchestrate` takes as a search. [Search and agents](../guide/loop-shape.md) explains
when to use which.

## Space and seeds

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

## Policies

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
| `command` | Your own search: `orchestrate: {command: ..., timeout_s: 600}` ([contract](commands.md#search-command)) |

## Options every policy takes

Every policy and phase also takes `name` (a label), `knobs` (move only these), `hold` (keep
these fixed; both accept globs such as `prefetch.*`), `metric` and `direction` (its own
target; default the first objective), `floor` (`{metric, at}` or `{metric, keep, above}`:
designs below it are refused), `margin` (minimum improvement, default `0`) and `dse` (an
emphasis, as above). `batch` is another name for `batch_size`. An unknown option is refused,
naming the policy's fields.

A policy's `batch_size` only groups its proposals. `budget.batch` is how many search designs
one pass carries; `budget.parallel` is how many passes run at once. Finishing a finite walk
does not stop an open-ended run: cap it with `budget.passes`.
