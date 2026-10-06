# flux-loop

The design loop every Flux problem runs on. A problem is a document (`<name>.problem.yaml`):
what to make, how a candidate is checked (the gate), how it is measured (the stages), what
"better" means (the objectives) and how the search goes (`flow:`). The loop generates
candidates (a model, a script, a catalog or a coding agent), gates them, measures the
survivors cheapest stage first, decides, and keeps a record a later run resumes from.

Entry points:

- `flux_loop.load_task(path)` reads a document; `PromptProblem(task)` is the problem it
  describes; `request_for(task, ...)` the loop's knobs; `run_loop(problem, request,
  proposer=...)` runs a pass and returns the decision, the frontier, the lessons and the
  refusals.
- `flux_loop.dse`: the search policies (sweep, montecarlo, anneal, gradient, genetic, pareto,
  llm, control) and phases.
- `flux_loop.author`: `flux ask` -- an author writes the problem from a prompt and files.
- `flux_loop.agent`: a coding agent as the generator or the author.
- `flux_loop.skills`: skills for the model and for the agents.

Where things are:

| module | what it holds |
|---|---|
| `document/` | what a problem document says: `TaskSpec` (`spec.py`), `load_task` (`load.py`), the keys, the placeholders (`commands.py`), the gate and stages, the `flow:` vocabulary (`flow.py`, `surface.py`, `layout.py`), `describe_flow` |
| `task.py` | the problem that runs a document: `PromptProblem` (prompts, gate, stages, generators, the prototype stage's hooks) |
| `loop.py` | the step loop: parts, batches, improvements, the chain of stages, the decision |
| `passes.py` | pass after pass until stopped; exploring after a rest |
| `dse.py` | the search policies and phases |
| `generation.py`, `prototype.py`, `patch.py` | a model writing and repairing a design, a prototype first, edits |
| `golden_proto.py`, `py2sv.py` | the prototype checked against a golden model, and spelled as SystemVerilog |
| `records.py`, `report.py`, `ops.py` | the campaign record and its reload, `flux report`, `flux status/stop` |
| `author.py`, `agent.py`, `skills.py` | `flux ask`, coding agents, skills |

The command line is `flux` (`interfaces/cli`); see `docs/usage-guide.md`,
`docs/extending.md` and `docs/architecture.md` at the repository root.
