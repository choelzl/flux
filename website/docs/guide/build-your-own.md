# Configure a loop

Start with the [working loop](tutorial.md), a `flux example`, or `flux new NAME`.
The document describes the experiment; the scripts beside it define how to generate, check,
and measure a design. Use the [parameter reference](parameters.md) for defaults and allowed values.

## Define the goal and interface

Write `statement` as the goal and `contract` as the interface and restrictions. State the
function signature or module ports, supported inputs, numerical accuracy, and any implementation
restrictions. Put executable checks in `flow.test`: prose alone does not establish correctness.

Choose the artifact `language`, such as `python`, `systemverilog`, or `cpp`. Flux uses it for
the generated file extension; when omitted it can infer the language from known tools.

```yaml
statement: Find a fast implementation of count_primes(n).
contract: >-
  Export count_primes(n). Return the number of primes strictly below n for
  nonnegative integer n. Do not change the interface or the benchmark.
language: python
```

## Choose how designs are made

| Approach | Configuration | Use it when |
|---|---|---|
| Script | `generate: {command: "..."}` | You can render each setting into a design |
| Model | `generate: model` | You want generated code or algorithms; this is the default |
| Coding agent | `generate: claude` (also `codex`, `opencode`) | The design needs edits across files or interactive checks |
| Fixed candidates | `generate: {catalog: [...]}` | You already have artifacts to compare |
| Tune settings | A search space, with no generator command | Commands consume the chosen settings directly |

A script generator writes `{artifact}`; its stdout is not the artifact. For example:

```yaml
flow:
  generate:
    command: ["{python}", "{home}/render.py", "{artifact}", "{block}"]
```

The search supplies `{block}`. Use `{point}` when the generator needs the entire point as a
JSON file, or `{params}` for settings shared by your own commands. See
[Search and agents](loop-shape.md) for the search and agent setup.

Commands accept a string or a list of arguments. A string is split into arguments, rather
than executed by a shell: if you need a pipeline, put it in a script or explicitly use
`bash -c`. Prefer argument lists when paths or values contain spaces. A command beginning
with `flux` uses the same Flux installation as the loop.

## Build a gate, cheapest check first

Checks run in the order written. The first failure refuses the candidate and later checks
do not run. Name them for the action they perform:

```yaml
flow:
  test:
    build: "{python} -m py_compile {artifact}"
    correctness:
      run: "{python} {home}/check.py {artifact}"
      timeout_s: 120
```

A check named `build` treats every nonzero exit as a build failure. Any check exiting `3`
also means the candidate did not build. Other checks use the default `N failing` pattern
when it is present; otherwise exit `0` passes and a nonzero exit counts as one failure.
Debug output on stderr is retained and is not, by itself, a failure.

For a checker with a different output format, specify the pattern deliberately:

```yaml
flow:
  test:
    tests:
      run: "{python} {home}/check.py {artifact}"
      count_re: 'failures=(\d+)'
```

`count_re` captures an integer failure count. `fail_re` counts matching failures instead.
These patterns scan stdout and stderr; a match can report failure even when the process exits
`0`. Keep debug messages distinct from your machine-readable test summary.

For RTL, define the ports and reference function in `golden.py`, then use:

```yaml
flow:
  test:
    lint: "flux rtl lint {artifact}"
    golden: "flux rtl test {artifact} --golden {home}/golden.py"
```

The [RTL reference](parameters.md#rtl-golden-model) describes vectors, clocked designs, and
floating-point tolerances. `flux tools` lists the built-in checks and their commands.

## Measure surviving designs

Measurements run stage by stage in document order. Start with a cheap screen and put the
costliest measurement last. A custom stage must print the metrics it declares:

```yaml
flow:
  measure:
    screen:
      command: "{python} {home}/bench.py {artifact} --quick"
      metrics: [time_ms, bytes]
      cutoff: {metric: bytes, below: 4096}
    confirm:
      command: "{python} {home}/bench.py {artifact} --full"
      metrics: [time_ms, bytes]
      timeout_s: 1800
  select: {finalists: 2}
```

The `--quick` and `--full` flags here are flags your benchmark must implement. It should print,
for example, `time_ms=2.31 bytes=2048`. Metrics can appear on stdout or stderr; a nonzero
measurement command exit rejects its numbers. Use `metrics_re` for a different output format.

Every stage must provide the metrics required by the objectives. Known RTL measurement
commands infer their metrics and tool requirements:

```yaml
flow:
  measure:
    screen: "flux rtl measure {artifact} --stage synth --clock-ps 1000"
    confirm:
      command: "flux rtl measure {artifact} --stage place --clock-ps 1000"
      timeout_s: 1800
  select: {finalists: 2}
```

`needs: [tool_name]` declares required executables for a custom stage. A stage is skipped when
a declared tool is missing; check the reported stage before treating a screening number as a
confirmed result. `--screen-only` omits the costliest stage of a multi-stage chain.

### Cutoffs and estimates

`cutoff` decides whether a measured design proceeds: `at` is a minimum, `below` is a maximum,
and `within` retains a fraction of the best result for that metric. A list requires every
condition to pass:

```yaml
cutoff:
  - {metric: fmax_mhz, at: 1000}
  - {metric: area_um2, below: 80}
```

An optional `estimate` can screen a design before paying for the tool:

```yaml
estimate: {kind: surrogate, margin: 0.05}
```

The surrogate uses prior measurements at that stage and needs at least three rows.
An estimate outside a cutoff or objective limit by more than the margin skips that stage.
Estimates are not measurements; enable this only when that screening trade-off fits your
experiment. The reference also covers command and model estimators.

## Define what wins

Separate **limits** (objectives with `goal`) from **preferences** (objectives without one):

```yaml
objectives:
  - {metric: fmax_mhz, direction: maximize, goal: 1000}
  - {metric: area_um2, direction: minimize, goal: 80}
  - {metric: power_w, direction: minimize}
```

This asks for at least 1000 MHz, at most 80 µm², then minimum power among feasible designs.
Goal-less preferences are compared in their written order. An absolute goal uses the deepest stage
unless its `stage` names another. If nothing meets every limit, the report describes the
closest standing design and its shortfall; it does not produce a feasible decision artifact.

Use `balance: true` on multiple goal-less objectives for a compromise at the frontier's knee.
A relative limit such as `{metric: speedup, direction: maximize, keep: 0.9, above: 1.0}`
keeps 90% of the best measured gain over the baseline, instead of specifying an absolute goal.

## Control time and concurrency

```yaml
budget:
  passes: 10
  steps: 3
  repair_attempts: 6
  batch: 1
  workers: 1
  parallel: 1
```

`passes` bounds the whole run; `steps` bounds work within one pass. `repair_attempts` bounds
attempts to fix a draft. `batch` is designs from a search within a pass, `workers` controls
measurement concurrency, and `parallel` controls simultaneous passes. They are separate knobs.
On a server, an administrator must allow parallel work for the app; otherwise its concurrency
cap wins. Keep timed benchmarks isolated, including from parallel passes and overlapping part
work. The [budget reference](parameters.md#budget) includes those settings.

Omit `passes`, or use `0`, to run until manually stopped. Finding a feasible design or a
stalled search does not end an unlimited run. Individual attempts still have their budgets
and timeouts; [Run and results](run.md) explains stopping and resuming.

## Add context and reusable settings

```yaml
params: {input_size: 1000000, repetitions: 9}
flow:
  knowledge:
    files: [spec.md, reference.py]
    text: Prefer implementations that are easy to maintain.
    digest: claude
    lessons: mined
```

`params` reaches scripts through the `{params}` JSON file. Knowledge files and notes reach
model prompts; the loop's `library/` and the operator's shared library supply papers.
`digest` chooses who summarizes them; `lessons` reuses lessons from campaign records.
`knowledge: off` disables this reading.

Check each edited document with `flux task check FOLDER`, then do a bounded run with
`flux task run FOLDER --passes 1`. Continue with [Search and agents](loop-shape.md) or look up
the remaining fields in the [parameter reference](parameters.md).
