# The loop

Every document runs through one loop. A design is proposed, written, checked by the gate,
measured stage by stage, and the best one is chosen. Every step is written to the record.

## Words used here

| word | meaning |
|---|---|
| **document** | the `*.problem.yaml` file that describes one problem |
| **gate** | the check that refuses a wrong design: a test against the golden model, a checker script |
| **stage** | one measurement, from cheap (synthesis, a formula) to costly (placement, a simulation) |
| **objective** | what "better" means: a metric, up or down, optionally with a goal to reach |
| **pass** | one round of the loop; a run is a series of passes |
| **record** | the database of every design, number and refusal (`out/<id>.db`) |

## The boxes

Each box has one job and a set of *halves*: who can fill it. Choose a half in the document's
`flow:` block, one key per box.

| box | what it does | halves | example |
|---|---|---|---|
| validate | checks the document before anything runs | rules (default), model, coding agent | `validate: llm` |
| orchestrate | picks the next piece of work | rules, given, model, coding agent | `orchestrate: agent` |
| plan | plans a pass before it starts | none (default), model, coding agent | `plan: llm` |
| dse | searches the knobs of `space:` | `sweep`, `montecarlo`, `anneal`, `gradient`, `genetic`, `pareto`, a list of phases, model, coding agent | `dse: sweep` |
| generate | writes each design | model (default), a script, a fixed list, coding agent | `generate: {command: "..."}` |
| test | the gate | the document's command or the world's check; **never delegated** | |
| critique | challenges the parts and the decision | none (default), model, coding agent | `critique: llm` |
| analytical | cheap stages: formulas, cost models | the stages named, a learned estimate (`surrogate`); **never delegated** | `analytical: [screen]` |
| simulation | stages run by real tools | the other stages; **never delegated** | |
| calibrate | compares each cheap stage with the costly one | on (default), off; **never delegated** | `calibrate: off` |
| select | chooses from the objectives | objectives; a coding agent may break ties | |
| feedback | notes you type during a run (`--tui`, `f`) | human (default), none | `feedback: none` |
| knowledge | what the model reads | `sheet`, `library`, `digest` (a model's summary of the library) | `knowledge: [digest]` |
| extract | lessons mined from the record | none (default), mined, coding agent | `extract: mined` |
| records | keeps everything; a rerun resumes from it | always on; **never delegated** | |

A coding agent (Claude Code, Codex, OpenCode) answers a box with `{agent: claude}` (or `codex`,
`opencode`) on validate, orchestrate, plan, dse, generate, critique, select or extract. The loop
checks its answer and falls back to the rules half if the answer is unusable.

`flux task check <document>` prints this table for a given document, with the half in force.

## Rules that never change

- **The gate is never delegated.** No model or agent decides whether a design is correct; a
  design that fails the gate is refused, never ranked.
- **A cheap stage orders, it never concludes.** Quick estimates only choose which designs go on
  to the costly stage. The decision quotes the deepest stage that ran, and the report names it.
- **Measured and modelled are kept apart.** The report says which numbers come from real tools
  and which from estimates.
- **A run ends only when you stop it** (Ctrl-C, `q`, `flux stop`) or at `--passes N`. When a
  pass finds nothing new, the next one explores: it sends the best designs back to be improved.
- **Nothing is measured twice.** The record keys every measurement by the tools and the exact
  source.

## The world (optional)

When a problem needs code a document cannot hold (a solver, a simulator, a special search), the
document names a Python class once: `world: package.module:World`. The loop calls the methods it
has and uses its own defaults for the rest. See [build your own](build-your-own.md#when-you-need-code-a-world).
