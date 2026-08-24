# Architecture overview

Codename **Flux**. The design principles, the layering and how the one loop is built. For
what runs now and what is next, see [loop-review-2026-09.md](loop-review-2026-09.md) (the
plan); for the why of every choice, [decisions.md](decisions.md); for the tree and the
packages, [`flux/README.md`](../flux/README.md). The accelerator era's documents are in
[history/](history/).

## Design principles

1. **One loop.** Every design or DSE problem runs on `core/loop` (`flux_loop`, D454 onward):
   a problem is a DOCUMENT (`*.problem.yaml`) plus a world package named once under `world:`
   (D519). No second engine; the ones that existed went with D521.
2. **The document says what, the world says how.** Objectives, stages, budget, parts, the
   campaign identity and who fills each role are the document's; the gate, the tools, the
   prototype language and the prompts are the world's hooks. Whatever the document can say,
   no world codes (D511, D517, D519, D522).
3. **Every number carries its provenance and its uncertainty.** A row on the record says the
   revision, the toolchain, the prompt's hash, the seconds and the tokens (D510, D535); a
   result without a method tag (measured or analytic) is a bug (D446).
4. **Measured decides, modelled orders.** A shallower stage's number orders candidates; the
   goal is judged on the deepest stage, with a declared margin on the shallower ones (D522,
   D535). Calibration between stages goes on the record and never rewrites a measurement
   (D464).
5. **The gate is never delegated.** Correctness (an exhaustive check, golden vectors, a
   proof) is code; a model may critique, never admit (D460).
6. **Four roles, each with a model half and a pre-written half**, swappable from the document
   or the command line (D460): orchestrator, generator, evaluator, mentor.
7. **No pre-generated designs.** The instruments give the model measurements, never
   solutions; every design on the record was made by the loop (D478, D530).
8. **Contracts over monoliths at the edges.** The evaluator ABI (`evaluate(workload, arch,
   mapping, budget) -> Result`) and the IR are what a tool adapter is written against; the
   loop's worlds call the tools through them or directly, and nothing is rewritten that
   Verilator, Yosys, OpenROAD, ZigZag or Timeloop already do.
9. **Agents are a first-class caller.** Every loop is a CHIA node and an MCP tool over its
   document; the catalog pages are generated from the live surface (D387).
10. **The evaluator is never writable by the thing being evaluated.** The sandbox the
    prototype stage runs in has no tools; the tools run outside it (D505).

## Layering

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ INTERFACES     flux task run/check · flux report/status/stop/gc/migrate       │
│                CHIA nodes · MCP tools · the TUI                               │
├──────────────────────────────────────────────────────────────────────────────┤
│ THE LOOP       core/loop: the document, the roles, the prototype stage and    │
│                its language, the ladder, the costed chain, calibration, the   │
│                record and its reload, the report                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ WORLDS         applications/<name>: the gate, the tools, the prompts, the     │
│                transpiler -- nlu, macarray, prefetcher, bankmap,              │
│                interconnect_mapping, omni                                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ EVALUATORS     evaluator/abi (the contract, the registry) · openroad · rtl ·  │
│                zigzag · timeloop · the prefetcher's ChampSim adapter ·        │
│                calibration · validity · redaction · the measurement cache     │
├──────────────────────────────────────────────────────────────────────────────┤
│ MENTOR         knowledge (corpus, library, mined facts) · records read back   │
│                as laws · operator feedback · protocol specs · benchmarks      │
├──────────────────────────────────────────────────────────────────────────────┤
│ SUBSTRATE      core/stores (the record, schema v2) · core/ir · core/llm ·     │
│                core/frontier · core/profile · generator/harness_*             │
└──────────────────────────────────────────────────────────────────────────────┘
```

| layer | doc | packages |
|---|---|---|
| the loop | [usage-guide.md](usage-guide.md), [loop-review-2026-09.md](loop-review-2026-09.md) | `core/loop` |
| evaluators | [evaluator-abi.md](evaluator-abi.md), [calibration.md](calibration.md) | `evaluator/*` |
| the IR | [ir.md](ir.md) | `core/ir` |
| the stores | [stores.md](stores.md) | `core/stores`, `mentor/records` |
| the agent surface | [agent-surface.md](agent-surface.md) | `interfaces/*` |

**The dependency rule (D428).** `core/`, `evaluator/`, `generator/` and `mentor/` never
import an application; `interfaces/` may (that is what an interface is for); an application
imports whatever it needs. What is one application's lives with it (the prefetcher's ChampSim
evaluator and code generator) and is registered by name like every other backend
(`flux_evaluator_abi.registry`, D426), found through the registry, not through the directory.

**Five words for prompt-bound text, on purpose (D445).** They name different things and
stay distinct: a *note* is what the operator typed (`flux_feedback`); a *lesson* is a line
the run itself keeps and reports (`flux_loop`); a *conclusion* is what a run inferred from its
measurements, stored beside them and labelled INFERENCE (`flux_records`); a *fact* is mined
from stored results with its provenance and boundary (`flux_records.mining`); a *law* or a
*duel* is extracted from controlled one-knob pairs (`flux_records.extract`). Only laws, duels and
conclusions are read back into prompts under one header (D429).

**A problem declares what its mentor knows (D449).** `flux_knowledge.Mentor` over sources:
`Corpus` (a sheet), `Library` (the operator's papers, retrieved lexically), `RecordReadback`
(the flywheel's other half), `Mined` (D462) and `Notes`. The loop assembles the mentor tab's
sections and the generator's static prompt prefix from that one declaration; a source that
cannot change during a run is read once per run.

## The loop as code

`core/loop` is the drawing every application draws -- input, validity, orchestration with
its strategies, the mentor boxes over the record, generation with repair, test, the costed
chain with calibration, the winner, the output -- as one runner, and the document plus its
world is what a problem supplies. The map of each box onto the code, with which half exists,
is [loop-review-2026-09.md §4.1](loop-review-2026-09.md); `flux_loop/graph.py` holds the
boxes as data.

**One step loop, three kinds of work in one vocabulary** (D446/D455/D457). A step spends
itself on a PART to write (divide the goal with `parts:` or `decompose`, plan one per step,
have the generator write it against the fast check until the gate admits it), a SUB-TASK to
run as its own loop (`subtasks:`, a `SubLoop`), or a BATCH to gate and measure together
(`search(state)` yields batches; the loop gates each, measures the admitted ones on the first
stage in one call, and hands the batch's results back, so an enumeration, a solver chain, a
climb or an invention round is one generator with local state). When a part is waiting while
a search is live, `next_work` is the orchestration decision: rules or a model.

**The ladder** (D517). A part that stands is improved by declared steps -- `sweep` (the
pipeline register sweep), `take` (the best measured design), `import` (a sibling campaign's
verified design, D540), `depth` (a shallower prototype at the same function), `contender`,
`redesign` (a different algorithm) -- each with a why on the ledger, and a part rests when
nothing on the ladder is due.

**An evaluator has two edges out** (D463). Measured results always reach the orchestrator;
`route(stage, scored, state)` is the other edge, back to the generator with the numbers and,
since D526, the critical path as data. The route acts only on the stage the parts are judged
on or deeper (D535). `budget_s` and `good_enough` are the optional early stops; `validate`
is the drawing's "input valid?" box, refusing a mis-posed run before anything is spent.

**The chain is N stages with a cutoff between them** (D454): any evaluators in any order,
each labelled by what the document declares it to be (`analytic_stages` tags the modelled
ones). `cutoff` says what is worth the next stage (`at`, `below`, `within`), `frontier`
reduces to the non-dominated set, `finalists` picks who pays for the costly stage. The
decision is made on the highest stage that has results.

**`calibrate` is a node** (D464): after every step up the chain the loop says how far the
costly stage's numbers were from the cheap stage's over the designs both measured, with the
spread and the count, on the record.

**Each role is a swappable component with a model half and a no-model half** (D460).
`flux_loop.roles` carries the bundle (`Roles`), the registry (`make_role`, `available_roles`,
`register_role`) and `rig(orchestrator="rules", ...)`. Orchestration: `rules`, `given`, `llm`,
`agent` (D505, with tools and every pick on the ledger). Generation: a `sources` source --
`Model` (the prototype stage, transpile, repair with the gradient and its tolerance, D504,
D535), `Template` (a command that renders), `Catalog`, `Solver`. Knowledge: a `Mentor` over
declared sources, `mined` for what the record taught. Evaluation: the document's stages; the
surrogate half that orders but never decides is plan step R9. A component may decline one
decision and keep the others; an empty rig is the loop's own defaults.

**The prototype stage** (D424, D478, D514-D516). A part is first a Python-integer prototype
(`flux_loop.pyint`: vectorised, rule-checked, its SPACE searched at the check) proven on
every input, then transpiled mechanically to the target; the model never writes the RTL by
hand, and the instruments (`error_map`, `quantisation`, `compare`, `family`, `timing`,
D530, D535) give it measurements to reason from.

## What this explicitly does not do

- Does not replace CHIA, TVM, Deeploy or MLIR, and does not attempt full-system simulation.
- Does not keep two engines: an application either runs on the one loop as a document or
  is not here (D521).
- Does not ship hand-written designs, pre-generated RTL or a remembered constant that leads
  a design (D478, the corpus' provenance rule).
- Does not model training; the IR reserves room for it.
