# Glossary

The words Flux and its web app use, one term each.

**Loop.** A folder holding a document, `problem.yaml`, and the scripts and files it names. The
folder's name is the loop's id. Flux generates designs, checks and measures them, and decides,
pass after pass.

**Document.** The loop's `problem.yaml`: what to make (`statement`, `contract`), who does each
box (`flow`), what wins (`objectives`) and how much to spend (`budget`). See the
[Document reference](../guide/parameters.md).

**Start.** One launch of a loop, from **Start** (or `flux task run`) until it stops. A loop
keeps one log and one record across its starts; each start resumes from the record.

**Pass.** One trip round the loop: choose what to try, make a design, check it, measure it,
decide. `budget.passes` or `--passes N` caps them; without a cap a run goes on until stopped.

**Box.** One step of the loop as the configurator's Graph draws it: validate, orchestrate
(Search), plan, generate (Design), test (Check), measure (Measure), select (Choose), calibrate,
feedback, knowledge. `flow` says who works each box.

**Check.** A command that decides whether a design is correct (`flow.test`). Checks run in the
order written; the first that reports failures refuses the design. Together they are the
**gate**: a design that fails it is never measured.

**Measurement.** A command that puts numbers on a design that passed the gate. Each
measurement is a **stage** of `flow.measure`; stages run cheapest first, and a cutoff decides
which designs go on to the next.

**Design.** One candidate the loop made: a file (the artifact) written by a script, a model or
an agent, with its checks and measurements on the record.

**Decision.** The design that meets every limit and is best on the objectives. Flux writes it
under `out/`. When none meets every limit, the closest is reported, not decided.

**Record.** The SQLite file a loop writes, `out/<id>.db`: every design, check, measurement and
refusal, with where it came from. A rerun reuses it.

**Campaign.** The command line's word for one line of work inside a record, named after the
loop. `--campaign` on `flux report`, `flux stop` and the others picks one; by default, the latest.

**Objective, limit, preference.** An objective is a metric and a direction. With a `goal` it
is a **limit** (at least or at most a value); without one it is a **preference**, compared in
the order written.

**Front, knee.** The front (Pareto frontier) is the set of designs no other design beats on
every objective at once. The knee is where improving one objective starts to cost much more of
another.

**Knob, space.** A knob is one setting with a list of choices; the space is all of them
(`flow.orchestrate.space`). A point is one choice for every knob.

**Search policy.** How the next points are chosen: `sweep`, `montecarlo`, `gradient`,
`anneal`, `genetic`, `pareto`, `model`, and others. See [Search policies](search.md).

**Part, sub-loop.** A part is a piece of one design, written and checked on its own
(`parts`). A sub-loop is a child loop in its own folder (`subtasks`), whose decided designs a
parent can compose. See [Split a larger design](../guide/parts.md).

**Model.** Flux's own language model, an OpenAI-compatible endpoint. It can propose points,
write designs, review the document, and digest papers.

**Agent.** A coding agent (Claude Code, Codex, OpenCode, or one a server adds) that takes a
brief and writes files. It runs on its own model settings, never on Flux's. See
[Models and agents](../guide/models.md).

**Critic.** A second opinion on one box (orchestrate, test, measure or select). Its rejection
is a verdict: the design fails, the numbers are marked, or the pick is made again.

**Author.** In `flux ask` and **New loop › Agent**, the model or agent that writes the
document and its files from your prompt.

**Knowledge, library.** What the model and agents read besides the document: the files and
text in `flow.knowledge`, the loop's `library/` of papers, the server's shared library, and
lessons from the record.

**Skill.** A folder with a `SKILL.md` (a name, a description, then instructions) that the model
and agents can load. The loop's `skills/` folder is read by default.

**Workbench.** The agents' own folder beside the document, `workbench/`: tools they build and
notes they keep, shared by the loop's agents and kept across runs.

**Hidden from AI.** Paths no agent or prompt may read, such as the gate's tests
(`knowledge.hidden`). The loop's own commands still read them.

**Golden model.** A short Python file, usually `golden.py`, that says what a hardware design
must compute (`PORTS` and `golden(**inputs)`), not how. See
[Presets](presets.md#golden-model).

**Prototype.** The algorithm written first in Python (or SystemC) and checked against the
golden model before the hardware design is written.

**Baseline.** A reference measured before pass 1 (pass 0), such as the design you have today.
Results can be read relative to it; it is never the decision.

**Preset.** One of Flux's own checks and measurements, run as `fluxpreset NAME`. See
[Presets](presets.md).

**Sandbox.** The container `flux task check`, `flux task run` and `flux ask` run in, so a
loop's code and agents cannot touch the rest of the machine. See
[Run, stop, resume](../guide/run.md#sandbox).

**ULP.** A unit in the last place: the gap between two neighbouring floating-point numbers.
"Within 1 ULP" means at most one step from the exact result.

**ASAP7.** A free, predictive 7 nm process design kit from Arizona State University. Flux's
area, timing and power numbers are on ASAP7, not on a commercial process.
