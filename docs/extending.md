# Extending Flux

What you can change to try something new, from the cheapest to the deepest, and how stable each
extension point is. **Stable** means it will keep working across releases. **Evolving** means it
works and is tested, but its shape may still change.

Start with `flux example python|rtl|sweep NAME`: it writes a problem that runs, and every
section below changes one part of it.

## 1. The problem document (stable)

A `*.problem.yaml` says what to make, how a candidate is refused, how it is measured, what
better means and how the search goes. [`author_reference.md`](../flux/core/loop/src/flux_loop/author_reference.md)
lists every key; `flux task check DOC` loads a document and says what it will do. A different
question is a copy of the document with different `statement`, `params`, `objectives`, `budget`
or `flow`. A changed ask opens its own record.

## 2. The gate and the stages: any command (stable)

A gate and a stage are commands, so any language and any tool will do. A gate may be several
named checks run in order (`test: {lint: ..., golden: ...}`, D652, D789); the first that fails refuses the design.

| command | prints | exit |
|---|---|---|
| a gate check's `run` | its failures as `N failing` (the default count), or counted by `count_re` (one integer group) or one `fail_re` match each | 0 passed, 1 failed, **3 did not build** (a build failure, not a score, D594) |
| the old `gate.build` | why it failed | non-zero: did not build |
| a stage's `command` | `name=value` tokens; the stage reads the `metrics` it lists | non-zero: the stage failed for that candidate |

Placeholders in any command: `{artifact}` (the candidate's file), `{home}` (the document's
folder), `{workdir}`, `{name}`, `{part}`, `{python}`, and `{knob}` for each knob of `flow.orchestrate.space`. A
command starting with `flux` runs this Flux. A stage lists the tools it needs under `needs:`; a
stage whose tools are missing is skipped and reported (`WILL SKIP` in `task check`, `NOT RUN`
in the report).

## 3. Who writes a candidate (stable)

`flow.generate`:
- `model` (the default): the model writes it and repairs it from the gate's output.
- `{command: "..."}`: a script renders it. With a space (`flow.orchestrate.space`), the script runs once per point with
  the knobs as placeholders (`flux example sweep`).
- `{by: opencode|claude|codex}` or `{by: {command: [...]}, timeout_s: N, questions:
  decide|model|operator}`: a coding agent writes it in a work directory. It does not compile or
  test: the loop runs the gate and brings a failure back to its session (D673, D674).
- `{catalog: [files]}`: designs that already exist.

### A prototype before the target (evolving, D604)

When the gate is `flux rtl test ... --golden golden.py` and `budget.prototype: true`, the
model first writes the algorithm as plain Python, `design(<the golden's inputs>) ->
{output: bits}`. Integers and bits only, and a formula, not a lookup of the answers (D616):
the range split where the function saturates or is the identity, a low-degree fixed-point
polynomial per segment, and tables only for its coefficients (at most 64 entries,
`budget.prototype_table_max`), which may be computed with floats at module level.
It is checked in seconds against every vector of the golden model and repaired from the failing
ones. Only a prototype that passes goes on. When its inputs total at most 20 bits, the loop
itself spells it as SystemVerilog (D611): branches become multiplexers and every signal is as wide
as its range measured over every input. Otherwise, or for a construct outside the subset
(a `while`, a floor division of a value that can be negative by a constant that is not a power
of two), the RTL turn transcribes it. A spelled design sent back gets a cost pass on its
prototype (D613): `py2sv.cost` estimates the hardware from the measured widths, and the
prototype stage scores by it with every input still passing, aiming 30% lower. Nothing
oversized is built (D615): a prototype costing more than `budget.prototype_cost_max`
(default 2,000, about 650 um2 on ASAP7) is shrunk first, and if it stays over, it is neither
spelled nor synthesised. If the document
names a `golden.py` that does not exist, the model writes it first from the statement and the
contract. That golden is checked (it imports, makes its vectors, answers every output port),
but read it before trusting a decision made against it. With `flow.generate: {by: ...}`, a coding agent writes the prototype
and the loop checks it with `flux rtl proto FILE --golden golden.py`, the stage's own check (D618).

## 4. How the search goes (stable names, evolving fields)

`flow.orchestrate` names a policy: `sweep`, `montecarlo`, `anneal`, `gradient`, `genetic`, `pareto`,
`control`, or `model` (the model proposes points from what was measured). It can also be a list of
phases, each starting from where the last ended:
`[{name: walk, policy: gradient, budget: 12}, {name: propose, policy: model, rounds: 1}]`. The
fields each policy takes are listed by the error a wrong one gets.

**A policy of your own** (evolving, D602) is a `flux_loop.dse.Policy` subclass in a file beside
the document, named as `module:Class`, alone or as a phase:

```python
# every_other.py, beside the document
from dataclasses import dataclass
from flux_loop.dse import Policy, points

@dataclass
class EveryOther(Policy):
    name: str = "every_other"
    stride: int = 2                          # a field is a key the document may set

    def walk(self, problem, state, space, seen):
        # yield batches of candidates; `got = yield batch` receives their scores, and
        # state.scored holds everything measured so far
        yield self.batch(problem, state, points(space)[::self.stride], seen)
```

```yaml
flow:
  orchestrate: every_other:EveryOther                              # or, as a phase:
  # dse: [{policy: "every_other:EveryOther", stride: 3}]
```

## 5. What a document cannot say: commands beside it (evolving)

When a problem needs code -- a simulator, a solver, a search of its own, a composition -- the
code is a command the document names in the box it belongs to (D798-D803):

| what the document cannot say | the command beside it | what it reads and writes |
|---|---|---|
| a check of your own | `flow.test: {name: "{python} {home}/check.py {artifact}"}` | prints `N failing`; exit 3 = did not build |
| a measurement | `flow.measure: {name: {command: ..., metrics: [...]}}` | prints `name=value` |
| a generator over a space | `flow.generate: {command: "... {knob} {artifact}"}` or `{point}` | writes `{artifact}` |
| a search of your own -- a solver, a proof, a model it asks itself | `flow.orchestrate: {command: "... {history} {state} {params}"}` | reads what was measured and refused, keeps its state, prints the next candidates, lessons, a conclusion (D799) |
| a composition of sub-loops | the parent's `flow.generate: {command: "... {parts} {artifact}"}` | reads each sub-loop's answer, writes the whole (D801) |
| settings | `params:` | `{params}`: a JSON file any command reads |

The applications are worked examples: `macarray` (a generator, a check and an invention problem
beside the study), `bankmap` (a search command: baseline, proof, z3, model rounds),
`interconnect_mapping` (a search command over pairs written as JSON), `nlu` (seven sub-loops in
folders). Their step commands live in a package beside the document (`python -m flux_<app>.steps`).

## 6. Who fills a role (evolving)

The loop has four roles: orchestrator, generator, evaluator, knowledge. Each can be switched
per run (`--role orchestrator=rules`) or per document (its box in `flow:`, e.g. `flow: {orchestrate: rules}`); `flux task check` lists the
choices. A component of your own registers with
`flux_loop.register_role(role, name, factory)` from a module the document imports.

## 7. Instructions for the model and the agents (stable)

A skill is a folder with a `SKILL.md` (a `name`, a `description`, then instructions) and any
files it brings. `skills: [dir]` in a document, or `--skill DIR`, gives it to the loop's model
(in every prompt, or through the `skill` tool for large libraries) and to coding agents (copied
where they look for skills). `flow.knowledge: {files: [...]}` puts specs, reference code and PDFs in
every prompt. The agents keep their own tools and notes in `workbench/` beside the document,
across runs; the loop never reads it (D677).

## 8. Driving Flux from a script (stable)

- `flux task run DOC --passes N --json answer.json` writes the decision, the frontier, what was
  refused and why, what is not established and the report, as JSON.
- `flux log RECORD` lists every model and agent turn.
- A run goes on until stopped: cap it with `--passes N`, or detach it with `flux run` and end it
  with `flux stop RECORD`.

In Python:

```python
from flux_loop import PromptProblem, TaskSpec, load_task, request_for, run_loop

task = load_task("my")                       # the folder: my/problem.yaml, id `my`
problem = PromptProblem(task)
out = run_loop(problem, request_for(task, db="my.db"), proposer=None)   # None: no model
print(out.decision, out.frontier, out.refused)
```

`flux_loop.passes.run_passes` runs pass after pass with the exploring rule (D593).
