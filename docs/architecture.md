# Architecture overview

How Flux is built: the loop, the principles it follows, and the layers of code under it. For
the commands, see [usage-guide.md](usage-guide.md). For the directory tree and the packages,
see [`flux/README.md`](../flux/README.md). For terms, see [glossary.md](glossary.md). The
reason behind each choice is in the design log, [decisions.md](decisions.md); the code cites
its entries as D-numbers.

## The loop in one picture

```
 problem document (.problem.yaml)
        │   what to build, the design space, the gate, the stages, the objectives, the budget
        ▼
 ┌─ orchestrator ── picks the next work: a part to write, a batch of design points,
 │        │         an improvement step. Rules, a DSE policy (sweep, anneal, ...), or a model.
 │        ▼
 │   generator ──── drafts a candidate: a model (with a prototype and repair turns),
 │        │         a script run once per design point, or a coding agent.
 │        ▼
 │   gate ───────── is it correct? Golden vectors, an exhaustive check, a proof. Code, never
 │        │         a model. A candidate that fails is refused, with the reason recorded.
 │        ▼
 │   stages ─────── measure with real tools, cheapest first (screen → confirm → route).
 │        │         A cutoff decides who pays for the next stage.
 │        ▼
 │   objectives ─── the front across the objectives; the goal is judged on the deepest stage;
 │        │         the decision names one design.
 │        ▼
 └── record ─────── every candidate, measurement and refusal, with provenance, in a SQLite
                    file. The next pass, a relaunch and `flux report` all read it back.
```

A run is one or more *passes* over this loop. Between passes the record is the memory: a
relaunch resumes from it and never measures the same design twice.

The **document** says WHAT: the statement, the design space, the parts, the gate and stage
commands, the objectives, the budget, and who fills each role. Many problems are a document
and nothing else: `applications/mul8/` is a document plus a golden model, and
`applications/adder16/` adds a design space and a generator script.

What a document cannot say is a **command beside it**, in the box it belongs to: a check
(`flow.test`), a measurement (`flow.measure`), a generator (`flow.generate`), a search of its own
(`flow.orchestrate: {command}`, D799), a composition of sub-loops (the parent's `generate`, D801);
`params:` reach any of them as `{params}`. There are no worlds (D803).

## Design principles

1. **One loop.** Every problem runs on `core/loop` (`flux_loop`). There is no second engine.
2. **The document says what; a command beside it says how.** Whatever a document can say, no
   code says.
3. **Every number carries its provenance.** A row on the record names the revision, the tool
   versions, the prompt's hash, the seconds and the tokens. A result is labelled measured or
   analytic.
4. **Measured decides, modelled orders.** A shallower stage's number orders candidates. The
   goal is judged on the deepest stage, with a declared margin on the shallower ones. The
   difference between stages is recorded and never rewrites a measurement.
5. **The gate is never delegated.** Correctness is code. A model may critique; it never
   admits a design.
6. **Four roles, each with a model half and a pre-written half**: orchestrator, generator,
   evaluator, knowledge (the mentor). The document or `--role` swaps them, so a run can have
   no model, or a model in exactly one role.
7. **No pre-generated designs.** The tools give the model measurements, never solutions.
   Every design on the record was made by the loop.
8. **Contracts at the edges.** The evaluator ABI (`evaluate(workload, arch, mapping, budget)
   -> Result`) and the IR are what a tool adapter is written against. Nothing is rewritten
   that Verilator, Yosys, OpenROAD, ZigZag or Timeloop already do.
9. **Agents are first-class callers.** A script or an agent drives Flux through the same CLI
   a person uses: `flux task run --json` hands back the answer, `flux ask` starts from a
   prompt, and a coding agent can be the author or the generator
   ([agent-surface.md](agent-surface.md)).
10. **The evaluator is never writable by the thing being evaluated.** The sandbox the
    prototype runs in has no tools; the tools run outside it.

## Layering

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ INTERFACES     flux task run/check · flux ask · flux rtl · flux report/status │
│                /stop/attach/gc/migrate · --json for scripts · the TUI         │
├──────────────────────────────────────────────────────────────────────────────┤
│ THE LOOP       core/loop: the document, the roles, the author, skills, the    │
│                prototype stage, the ladder, the chain, calibration, the       │
│                record and its reload, the report                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ APPLICATIONS   applications/<name>: a document, and the commands it names     │
│                -- adder16, mul8, gelu_fp16, primes, npu_gemm, nlu, macarray,  │
│                prefetcher, bankmap, interconnect_mapping                      │
├──────────────────────────────────────────────────────────────────────────────┤
│ EVALUATORS     evaluator/abi (the contract, the registry) · openroad · rtl ·  │
│                zigzag · timeloop · champsim ·                                 │
│                calibration · redaction · the measurement cache                │
├──────────────────────────────────────────────────────────────────────────────┤
│ MENTOR         knowledge (corpus, library, mined facts) · records read back   │
│                as laws · operator feedback · benchmarks                       │
├──────────────────────────────────────────────────────────────────────────────┤
│ SUBSTRATE      core/stores (the record) · core/ir · core/llm · core/frontier  │
│                · core/profile · core/tui · generator/harness_*                │
└──────────────────────────────────────────────────────────────────────────────┘
```

| layer | doc | packages |
|---|---|---|
| the loop | [usage-guide.md](usage-guide.md) | `core/loop` |
| evaluators | [evaluator-abi.md](evaluator-abi.md), [calibration.md](calibration.md) | `evaluator/*` |
| the IR | [ir.md](ir.md) | `core/ir` |
| the stores | [stores.md](stores.md) | `core/stores`, `mentor/records` |
| agents and scripts | [agent-surface.md](agent-surface.md) | `interfaces/cli`, `core/loop` |

**The dependency rule.** `core/`, `evaluator/`, `generator/` and `mentor/` never import an
application. `interfaces/` may. An application imports whatever it needs. An evaluator lives under
`evaluator/` and is registered by name in `flux_evaluator_abi.registry`; the domain library an
application evaluates with lives with that application.

**Five words for text that reaches a prompt.** They name different things and stay distinct.
A *note* is what the operator typed (`flux_feedback`). A *lesson* is a line the run keeps and
reports (`flux_loop`). A *conclusion* is what a run inferred from its measurements, stored
beside them and labelled INFERENCE (`flux_records`). A *fact* is mined from stored results,
with its provenance and its limits (`flux_records.mining`). A *law* or a *duel* is extracted
from controlled pairs that differ in one knob (`flux_records.extract`). Only laws, duels and
conclusions are read back into prompts.

**A problem declares what its mentor knows.** `flux_knowledge.Mentor` combines sources:
`Corpus` (a sheet), `Library` (papers, retrieved by keyword), `RecordReadback` (the record,
read back), `Mined` and `Notes`. The loop builds the model's fixed prompt prefix from that one
declaration; a source that cannot change during a run is read once.

## Inside the loop

**Three kinds of work, one step loop.** A step works on one of three things:

- a PART to write: the goal divided with `parts:` (or `decompose`, for the model to divide),
  one planned per step, written by the generator until the gate admits it;
- a SUB-TASK run as its own loop (`subtasks:`);
- a BATCH to gate and measure together. A search yields batches; the loop gates each,
  measures the admitted designs on the first stage in one call, and hands the results back.
  A sweep, a solver chain, a climb or an invention round is one generator with local state.

When a part waits while a search runs, the orchestrator (rules or a model) picks which goes
next.

**Design-space exploration.** A document's `flow.orchestrate.space` lists knobs and their choices. A DSE
policy under `flow: {orchestrate: ...}` searches it: `sweep`, `montecarlo`, `anneal`, `gradient`,
`genetic`, `pareto`, or `model` (the model names the next points). A list of *phases* runs
several policies in order, each starting where the last one ended.

**The ladder.** A part that already stands is improved by declared steps: `sweep` (sweep the
pipeline registers), `take` (the best measured design), `import` (a verified design from a
sibling campaign), `depth` (a shallower prototype for the same function), `contender`,
`redesign` (a different algorithm). Each step records why it ran. A part rests when nothing on
the ladder is due.

**The chain is N stages with a cutoff between them.** Stages run in any order the document
gives, each labelled measured or analytic. `cutoff` says what is worth the next stage (`at`,
`below`, `within`); `frontier` keeps the non-dominated designs; `finalists` picks who pays for
the costly stage. The decision uses the deepest stage that has results.

**Calibration between stages.** After each step up the chain, the loop records how far the
costly stage's numbers were from the cheap stage's, over the designs both measured, with the
spread and the count.

**An evaluator has two edges out.** Measured results always reach the orchestrator. The
second edge goes back to the generator with the numbers and the critical path, so the next
draft can fix what was slow. `validate` refuses a badly posed run before anything is spent.

**Roles are swappable components.** `flux_loop.roles` holds the registry (`make_role`,
`available_roles`, `register_role`). Orchestration: `rules`, `given`, a DSE policy, `model`, or
`agent` (a model with tools, every pick on the ledger). Generation: `Model` (the prototype
stage, translation and repair), `Template` (a command), `Catalog`, `Solver`, or a coding agent
(`flow: {generate: {by: opencode}}`). Knowledge: a `Mentor` over declared sources,
`mined`, `digest`. Evaluation: the document's stages, each with an optional estimate before its
tool (`estimate:`, D665) that may skip a design, never choose one.

**The prototype stage.** In the documents that use it (each NLU operator), a part is first
written as a Python-integer prototype (`flux_loop.pyint`: vectorised, rule-checked) and proven
on every input, then translated mechanically to SystemVerilog. The model never writes that RTL
by hand. Tools such as `error_map`, `quantisation`, `compare`, `family` and `timing` give it
measurements to reason from.

**The author.** `flux ask` puts one more role in front of the loop: an author (the model or a
coding agent) turns a prompt and files into a problem document and its golden model. The loop
checks the document, runs it, and gives the report back to the author, who may revise the
document for the next pass. Skills (folders with a `SKILL.md`) extend what the author and the
generators know.

## What this does not do

- It does not replace TVM, Deeploy or MLIR, and it does not attempt full-system
  simulation.
- It does not keep two engines: an application runs on the one loop as a document, or it is
  not here.
- It does not ship hand-written designs, pre-generated RTL, or a remembered constant that
  steers a design.
- It does not model training; the IR leaves room for it.
