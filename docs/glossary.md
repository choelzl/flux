# Glossary

The words Flux uses, in plain terms. Commands are in [usage-guide.md](usage-guide.md); the
structure is in [architecture.md](architecture.md).

**Problem document.** A YAML file, `<name>.problem.yaml`, that states one design problem:
what to build, how to check it (the gate), how to measure it (the stages), what to optimise
(the objectives), and the budget. `flux task check <doc>` validates it; `flux task run <doc>`
runs it.

**Step command.** A command a document names for what it cannot say in prose or numbers -- a
check, a measurement, a generator, a search (`orchestrate: {command}`), a composition -- often
`python -m flux_<app>.steps ...` from a package beside it. There are no worlds (D803).

**Gate.** The correctness check every candidate must pass before it is measured: golden
vectors, an exhaustive check over every input, or a proof. The gate is always code, never a
model. A candidate that fails is *refused*, with the reason on the record.

**Stage.** One measurement step with a real tool. Stages run cheapest first, and a cutoff
decides which designs go on to the next one. The usual RTL chain is:
- **screen**: Yosys synthesis with OpenSTA timing. Seconds per design; good for ordering.
- **confirm**: OpenROAD placement. Minutes per design; closer to the truth.
- **route**: full place-and-route. The costliest, for the finalists only.

A stage whose tool is missing is skipped; `flux task check` says `WILL SKIP` ahead of time.

**Objective, goal.** An objective is a metric and a direction (maximise `fmax_mhz`, minimise
`area_um2`). The objectives form a vector: the first carries the *goal* (for example 800 MHz,
judged on the deepest stage), and the rest break ties.

**Front (Pareto frontier), knee.** The front is the set of designs that no other design beats
on every objective at once. The knee is the point on the front where improving one objective
starts to cost much more of another.

**Record, campaign.** The record is the SQLite file a run writes (by default
`<document dir>/out/<id>.db`): every candidate, measurement and refusal, with its
provenance. A campaign is one named line of work inside a record, named by the document's `id`. Relaunching resumes the campaign from the record.

**Pass.** One trip through the loop over the campaign: pick work, generate, gate, measure,
decide. `--passes N` runs N passes; without it the run goes on until `flux stop`.

**Part.** A piece of one design that is written and checked on its own, such as one function
of the NLU's seven. `parts:` lists them; `decompose` lets the model divide the goal itself.

**Generator.** Whatever drafts candidates: a model, a script run once per design point
(`flow: {generate: {command: ...}}`), a catalog of existing designs, a solver, or a coding
agent (`flow: {generate: opencode}`).

**Author.** In `flux ask`, the one who writes the problem document and its golden model from
your prompt and files: the model by default, or a coding agent (`--author
opencode|claude|codex`). After each pass it reads the report and may revise the document.

**Skill.** A folder with a `SKILL.md` file (a name, a description, then instructions) and
any files it needs. Skills give the author, the model and coding agents extra know-how.
Add them with `skills:` in a document or `--skill DIR`.

**Workbench.** The coding agents' own folder beside the document (`workbench/`): tools they
build and notes they keep, shared by every agent of the problem and kept across runs. The loop
provides it and never reads it.

**Golden model.** A short Python file, `golden.py`, that says what a design must compute
(`PORTS` and a `golden(**inputs)` function), not how. `flux rtl test` checks RTL against it.

**ULP tolerance.** ULP is a "unit in the last place": the gap between two neighbouring
floating-point numbers. "Within 1 ULP" means the design's answer is at most one step away
from the exact result, for every input. The NLU's gate uses this.

**DSE, phases.** Design-space exploration: searching a space (`flow.orchestrate.space`) of knobs and their choices
for the best designs. A DSE policy (`sweep`, `montecarlo`, `anneal`, `gradient`, `genetic`,
`pareto`, or `model`) picks which points to try. *Phases* are a list of such searches run in
order, each starting where the last one ended, each with its own knobs.

**Ladder.** The steps that improve a part that already works: `sweep` (pipeline registers),
`take` (the best measured design), `import` (a verified design from a sibling campaign),
`depth` (a shallower prototype), `contender`, `redesign` (a different algorithm). A part
rests when no step is due.

**Mentor, knowledge.** What the model reads besides the problem: a methods sheet
(`flow.knowledge: {sheet: ...}`), a library of papers, facts mined from past records, and the
record read back. The mentor is the fourth role, next to orchestrator, generator and
evaluator.

**Role.** One of four jobs in the loop: orchestrator (what next), generator (draft),
evaluator (measure), knowledge/mentor (what to read). Each has a model half and a
pre-written half; `--role` swaps them.

**Coding agent.** A terminal tool that takes a brief and writes files: Claude Code, Codex CLI,
OpenCode or any command. One can be the author in `flux ask` or the generator of any document
(`flow: {generate: opencode}`); scripts and agents alike read a run's answer with
`flux task run --json` ([agent-surface.md](agent-surface.md)).

**ASAP7.** A free, predictive 7 nm process design kit from Arizona State University, used
for teaching and research. Flux's area, timing and power numbers are on ASAP7, not on a
commercial process.

**D-numbers.** Entries of the design log, [decisions.md](decisions.md): D1, D2, and so on,
each a decision with its reason and what was checked. Code comments and older documents cite
them.
