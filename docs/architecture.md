# Architecture overview

Codename **Flux**. The design principles, layering, and repository structure that every topic
doc below implements. For current build status and what's next, see [roadmap.md](roadmap.md).

## Design principles

1. **Contracts over monoliths.** The IR and the evaluator ABI are the product. Everything else is
   a replaceable implementation behind them.
2. **Every number carries its provenance and its uncertainty.** A result without a calibration id
   and a confidence interval is a bug.
3. **Fidelity is a budget, not a mode.** Callers state what they can afford; the framework picks
   the stage and escalates when it matters.
4. **Reuse aggressively at the edges, own the middle.** Don't rewrite Ray, Accelergy plug-ins,
   Verilator, or Hammer. Do own the IR, the contract, the calibration store, and the search core.
5. **Agents are a first-class caller, not an add-on.** Every capability is available as a typed
   function, an MCP tool, and a CLI — from the same definition.
6. **The evaluator is never writable by the thing being evaluated.** Structural isolation, per
   CHIA's containerization model.
7. **Fast path stays fast.** A single mapping evaluation should be sub-millisecond and
   allocation-free in the hot loop, or agentic-scale search is impossible (a first real native
   core now exists, [decisions.md D75](decisions.md)/[D76](decisions.md) — see [ir.md](ir.md) and
   §Performance engineering below for what it does and doesn't cover yet).

## Layering

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ L6  FLOWS            CHIA loops · CLI · notebooks · MCP tool surface         │
│                      ── reuse CHIA; contribute Flux nodes upstream ──        │
├──────────────────────────────────────────────────────────────────────────────┤
│ L5  SEARCH           strategy plug-ins over the Evaluator ABI                │
│                      exhaustive · CP/MIP · annealing · gradient (DOSA-style) │
│                      · Bayesian · evolutionary · LLM-agent                   │
│                      + warm-start from the Result Store                      │
├──────────────────────────────────────────────────────────────────────────────┤
│ L4  EVALUATOR ABI    evaluate(workload, arch, mapping, budget)               │
│      ★ THE CONTRACT     → {metrics, uncertainty, bottlenecks, provenance}    │
│                      backends: Flux-native · ZigZag · Timeloop/Accelergy ·   │
│                      Sparseloop · CiMLoop · Stream · RTL-sim · FPGA ·        │
│                      OpenROAD                                                │
├──────────────────────────────────────────────────────────────────────────────┤
│ L3  CALIBRATION      validated-domain registry · residual models ·           │
│                      ground-truth store · drift detection · escalation policy│
├──────────────────────────────────────────────────────────────────────────────┤
│ L2  FLUX IR          Workload IR · Architecture IR · Mapping IR              │
│      ★ THE FORMAT    versioned, serialisable, schema-checked, hashable       │
├──────────────────────────────────────────────────────────────────────────────┤
│ L1  FRONTENDS        ONNX · MLIR (linalg/tosa/stablehlo) · PyTorch export ·  │
│                      handwritten YAML · architecture template library        │
├──────────────────────────────────────────────────────────────────────────────┤
│ L0  STORES           Artifact/Result store (content-addressed) ·             │
│                      Calibration store · Benchmark corpus                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

The two starred layers (IR, Evaluator ABI) are what makes this a *new tool* rather than another
cost model. Each layer has its own topic doc:

| Layer | Doc | Package(s) |
|---|---|---|
| L2 Flux IR | [ir.md](ir.md) | `ir/` |
| L4 Evaluator ABI | [evaluator-abi.md](evaluator-abi.md) | `evaluator/abi/`, `evaluator/*` |
| L3 Calibration | [calibration.md](calibration.md) | `calibration/`, `validity/` |
| L5 Search | [search.md](search.md) | `search/*` |
| L6 Flows / agent surface | [agent-surface.md](agent-surface.md) | `interfaces/chia_nodes/`, `interfaces/mcp/`, `interfaces/cli/`, `knowledge/` |
| L0 Stores | [stores.md](stores.md) | `stores/`, `corpus/` |
| L1 Frontends | (see `frontends/README.md`) | `frontends/onnx/` |

**The dependency rule between the role directories and the applications (D428).**
`mentor/`, `orchestrator/`, `generator/`, `evaluator/`, `core/` never import an
application; `interfaces/` may (that is what an interface is for); an application
imports whatever roles it needs. What is one application's lives with it: an evaluator
that reads one application's own config (`applications/prefetcher/evaluator`, the two
interconnect stages under `applications/interconnect/`) or a code generator for one
target (`applications/prefetcher/codegen`) is registered by name like every other
backend (`flux_evaluator_abi.registry`, D426) and found through the registry, not
through the directory. The campaign's search kinds and strategies work the same way
(`flux_search_campaign.strategies.register_candidate_generator` / `register_strategy`).
`tests/unit/test_campaign_registries.py` guards the orchestrator side of the rule.

**Five words for prompt-bound text, on purpose (D445).** They name different things and
stay distinct: a *note* is what the operator typed (`flux_feedback`); a *lesson* is a line the
run itself keeps and reports (`flux_loop`, every study's `lessons`); a *conclusion* is what a
run inferred from its measurements, stored beside them and labelled INFERENCE
(`flux_records`); a *fact* is mined from stored results with its provenance and boundary
(`flux_knowledge_mining`); a *law* or a *duel* is extracted from controlled one-knob pairs
(`flux_extract`). Only laws, duels and conclusions are read back into prompts under one
header (`read_back`, D429).

**A problem declares what its mentor knows (D449).** `flux_knowledge.Mentor` over
`KnowledgeSource`s -- `Corpus` (a sheet), `Library` (the operator's papers, retrieved
lexically), `RecordReadback` (the flywheel's other half) and `Notes`, or a study's own -- and
the loop assembles both the mentor tab's sections and the generator's static prompt prefix
from that one declaration. A source that cannot change during a run is read once per run,
which is what keeps a library lookup out of every generation turn.

## Performance engineering

Target, partially reached — a first real native core exists ([decisions.md D75](decisions.md)/
[D76](decisions.md), `core/`), narrow in scope (see `core/README.md`):

| Concern | Approach | Status |
|---|---|---|
| Hot loop | Native core (Rust/C++); no allocation per candidate; SoA layouts | Real for one narrow cost model (`core/src/roofline.rs`, a compute-bound lower bound) and one combinatorial primitive (`core/src/flat_mapping.rs`, flat-mapping candidate enumeration) — not a general evaluation path |
| Language boundary | PyO3/nanobind; batch across the boundary, never per-candidate | Real (PyO3, no `nanobind`); both a full-JSON-document batch call and a genuine numeric SoA hot-loop call exist |
| Enumeration | Bitset-encoded loop nests; prune by dominance before costing | Partial — real flat-mapping enumeration exists and is measurably faster than Python (D76); no bitset encoding, no dominance pruning yet |
| Memoization | Content-addressed cache keyed on `(w,a,m,evaluator_version)`; shared across users | Real since Phase 1 ([decisions.md D19](decisions.md), `flux_store.CachingEvaluator`) — was never actually blocked on a native core |
| Incrementality | Dependency-tracked re-evaluation — change one arch parameter, recompute only what depends on it | Real for two real consumers now, plus a second, structurally different mechanism ([decisions.md D79](decisions.md)/[D86](decisions.md)/[D89](decisions.md)): `flux_characterize_memory_level` and `flux_sweep_dynamic_shape`/`flux_sweep_moe_routing` (`flux_store.CachingEvaluator` around an already-reduced sub-document or a per-sample `Candidate`, D79/D86); `codegen/rtl_harness`'s own real Yosys/ASAP7 synthesis calls use a genuinely different mechanism (`ToolResultCache`, content-hash-keyed, D89/D92) since there's no reducible sub-document there — Yosys needs the whole real design. `systemc_dse`'s own Verilator calls remain the one real piece still uncached (no Yosys-equivalent synthesis step to hang the same mechanism off) |
| Parallelism | Data-parallel within a node (rayon/OpenMP); across nodes via CHIA/Ray | Cross-node real (CHIA/Ray, D6 addendum/D34); within-node data-parallelism (rayon/OpenMP) not started |
| Determinism | Explicit seeds everywhere; a deterministic mode that disables nondeterministic parallel reduction | Real for every search strategy (explicit `seed`); no native-core-specific nondeterminism exists yet to disable |

**Target:** ≥10⁵ dense-layer mapping evaluations/second/core for the native backend, and a full
model × architecture sweep that today takes hours reduced to minutes. **The raw throughput number
is genuinely cleared for both real native primitives above** (D75: ~7.2×10⁵–1.8×10⁷ evals/s
depending on call shape; D76: ~4.7×10⁵ candidates/s for full enumeration) — but neither is a
general "evaluate a full workload" path, so this target is honestly only partially met; see
`core/README.md` for the real, measured finding about exactly when native beats Python here (once
real per-candidate branchy work exists, not for a computation as cheap as one division).

## The loop as code

`core/loop` (`flux_loop`, D421) is the five-node loop every application draws --
orchestration, generation <-> test/validate, evaluation, records feeding back -- as a
runner, and `Problem` is what an application supplies. A new problem implements what
makes it different (candidate, prompts, build, fast test, gate, stages, compose,
decide); the runner owns the record and its read-back, feedback, resuming, planning
with cooldowns, the generation<->test inner loop with structured decoding, patching,
revert and problem tools, composition, the cached chain, frontier, decision and
conclusion.

**One step loop, three kinds of work in one vocabulary** (D446/D455/D457). A step
spends itself on a PART to write (divide the goal with `subgoals`/`decompose`, plan one
per step, have the generator write it against the fast test until the gate admits it --
what NLU and a task document do), a SUB-TASK to run as its own loop (`SubLoop`), or a
BATCH to gate and measure together (`search(state)` yields batches, the loop gates each
and measures the admitted ones on the first stage in one call (`measure_batch`), then
hands that batch's results back to the generator, so an enumeration, a solver chain, a
climb, a cross product or a model-directed menu is one function with local state). Both
kinds are asked for every pass, and when a part is waiting while the search is still
live, `Problem.next_work` is the orchestration decision -- code, a rule or a model. All
of them then take the same frontier, spread the same finalists onto the costlier stage,
decide and conclude. Every application runs on it:
macarray, bankmap, interconnect_mapping, prefetcher and interconnect in batches,
NLU and the task document part by part, omni as rounds of tool steps (D458) -- a round is
a batch, its catalog validation is the gate, running a tool is the measurement. The orchestrator's model-directed MENU is
part of the loop too (`flux_loop.actions`: `Action`, `Outcome`, `DirectedSearch`), which
is where `flux_directed_search` went.

**An evaluator has two edges out** (D463). Measured results always reach the orchestrator,
which decides what to try next; `Problem.route(stage, scored, state)` is the other edge --
back to the GENERATOR, to improve the design in hand, told what the numbers were. It is asked
after every stage, an improvement is a kind of work like a part or a batch, and climbing the
chain is a kind of work too, so evaluation -> generation -> evaluation is a cycle inside one
pass. `LoopRequest.budget_s` and `Problem.good_enough` are the optional early stops (both off
unless asked for), and `LoopResult.stopped` says which ended the pass. `Problem.validate` is
the drawing's "input/problem valid?" node, refusing a mis-posed run before anything is spent.

**The chain is N stages with a cutoff between them** (D454), and fast-then-slow is the common
case rather than the rule: any evaluators in any order, each labelled by what the problem
declares it to be.
The loop walks the declared `stages` pairwise: `cutoff(stage, scored, state)` says what is
worth the next one, `frontier` reduces to the non-dominated set and `finalists(front,
state, stage)` picks who pays for it. `flux_loop.cutoff` carries the two rules that are
needed in practice -- an absolute floor or budget (`above`, `below`) and a band around
this run's own leader (`within_best`) -- each returning `(survivors, why)` so the reason
for a cut travels with it into the log and the lessons. A task document declares one per
stage (`cutoff: {metric, at | below | within}`), validated at load. The decision is made
on the highest stage that has results, so a run whose costly stage refused everything
decides on the cheaper one and says so.

**A part may be another loop** (D455). A work item is `str | SubLoop`: a `SubLoop` runs
`run_loop` on its own `Problem`, with its own gate, stages and record, and what the child
DECIDED becomes the parent's admitted part, its lessons and refusals prefixed with the
sub-task's name. The division comes from either side -- a problem declares it
(`subproblems`, a document's `subtasks`) or the orchestrator decides it at runtime and
returns sub-loops from `decompose` -- and `LoopRequest.max_depth` bounds the nesting with
a refusal that names the limit.

**The DSE box is part of the orchestration role** (D465): a problem declares its space
(`space() -> {knob: [choices]}`) and `sweep`, `montecarlo` or `anneal` proposes the points --
no search code of its own. **`calibrate (CI)` is a node** (D464): after every step up the
chain, the loop says how far the costly stage's numbers were from the cheap stage's over the
designs both measured, with the spread and the count, and hands that to `Problem.calibrated`
for a problem that corrects its own fast model with it. A correction never rewrites a
measurement.

**Each of the four roles is a swappable component with an AI and a no-AI half** (D460).
`flux_loop.roles` carries the bundle (`Roles`), the registry (`make_role`,
`available_roles`, `register_role`) and `rig(orchestrator="rules", ...)`. Orchestration can
be `Rules` (code and logic decide, no model), `Given` (the user's own division, in order) or
`ModelOrchestrator` (a model decides -- the loop's own default, named); generation is a
`sources` source; evaluation is the problem's `stages` (analytical models and real tools) or
`Surrogate` (D461: a screening stage fitted on this campaign's own measured trials, tagged
analytic, refusing to be the last stage, and never cached because it refits as the record
grows); knowledge is a `flux_knowledge.Mentor` over declared sources -- what a person fed in,
and `Mined` (D462) for what was extracted from this project's own data, facts and the
conclusions drawn from them kept distinguishable. Every hook asks the rig first and falls back to what it always did, a
component may decline one decision and keep the others, and an empty rig is the old behaviour.
Who fills a role is settable from a task document (`"roles": {...}`), from the command line
(`flux task run --role orchestrator=rules`) or from an application in Python -- so a run can
have no model in it anywhere, or a model in exactly one role.

**Generation is the same shape one level down** (D456): draft, build, fast-check, and
again with the failure in hand. `Problem.generator` names WHO drafts --
`sources.Model` (the model inner loop with patching, reverting and prototypes),
`sources.Template` (a renderer the framework runs), `sources.Catalog` (designs that
already exist) or `sources.Solver` (computed from the constraints and told the last
counter-example) -- and the loop around it, its phases and its refusal reasons do not
change with the choice. A task document says `"generator": {"command": [...]}` or
`{"catalog": [...]}` and runs the whole loop with no model anywhere in it.

## Repository layout

```
flux/
├── ir/                     # schemas (JSON Schema), canonicalisation, hashing
│   ├── workload/ architecture/ mapping/
├── core/                   # native evaluation — a first real, narrow capability now exists (decisions.md D75/D76)
├── evaluator/             # ABI + adapters — one dir per backend, each independently installable
│   ├── abi/ zigzag/ timeloop/ rtl/ systemc/ booksim/ noxim/ cacti/ gem5/ thermal/ dramsim3/ native/
│   ├── openroad/ # real physical-design PPA — Yosys + OpenROAD placement/routing on vendored
│   │   ASAP7 (decisions.md D225–D230), the campaign escalation stage for area_mm2/power_w
│   ├── stream/   # real multi-core/layer-fused DSE, StreamEvaluator (decisions.md D80–D82) —
│   │   composes a real Flux-Workload-IR→ONNX exporter with a real interconnect.multi_core
│   │   Architecture IR translator that reuses evaluator/zigzag's own per-core translator directly
│   ├── hammer/ sparseloop/ cimloop/   # not built — real sparsity capability lives in
│   │   evaluator/timeloop/ instead (decisions.md D78, no separate evaluator/sparseloop/
│   │   package was needed); hammer superseded by openroad/ (D225, its README stays as the
│   │   documented commercial-flow alternative); cimloop genuinely not started
├── calibration/            # residual models, escalation policy, drift CI, conformance checking
├── validity/                # independent validity checking (constraints + roofline), no shared code with any adapter
├── search/                 # strategy plug-ins
│   ├── exhaustive/ (+annealing) architecture/ agentic/   # cp/ gradient/ bayesian/ evolutionary/ not started
│   ├── campaign/   # long-horizon campaigns over an Objective IR document (decisions.md
│   │   D216–D222) — grid/agentic/generative strategies, composition, DB-is-the-checkpoint resume
├── generation/              # real architecture-candidate generation (decisions.md D91) — an LLM
│                            # proposes a whole new Architecture IR document, real-verified against
│                            # independent validity, RTL conformance, and deterministic replay:
│                            # roadmap.md's own Phase 3.5 exit criterion, real for the first time
├── codegen/                 # real agentic RTL/SystemC module generation-and-verification (decisions.md
│   │                        # D39–D55) — an LLM proposes behavior, a deterministic harness compiles/
│   │                        # traces/checks it against caller-authored test vectors; rtl_harness/ also
│   │                        # has real Yosys synthesis + caching (D47/D52/D89) and real ASAP7 PDK
│   │                        # synthesis (D92) — a real, physical area, not a generic gate count
│   ├── rtl_harness/ systemc_harness/   # each language's driver, build, emission and keywords
│   ├── harness_spec/        # what a harness is TOLD and what it REPORTS, in no language
│   │                        # (decisions.md D453): DesignSpec/CompositionSpec/HarnessRunResult
│   │                        # and the parsers, after the two mirrors drifted apart
├── workload_dynamism/       # real, honest cost estimation for declared-dynamic workload shapes
│                            # (decisions.md D63, KV-cache/dynamic seq-len) and MoE `data_dependent`
│                            # routing (decisions.md D68) — sample-and-aggregate over an existing
│                            # evaluator, no new cost model
├── redaction/               # real redaction layer between evaluator outputs and model context
│                            # (decisions.md D93/D94, closing gap-analysis.md G15) — relative deltas
│                            # and rank orderings instead of absolute PDK-derived numbers, plus real,
│                            # structural confidentiality-policy enforcement, not just an available
│                            # opt-in a caller could bypass
├── knowledge/              # domain knowledge / context layer, RAG-style
│   ├── corpus/ retrieval/ connectors/
│   ├── mining/   # typed facts computed from campaign/calibration stores, boundaries as fields
│   │   (decisions.md D243), rendered into proposer/authoring prompts (D245)
├── protocols/              # machine-checkable protocol semantics — OBI handshake/stability rules
│                            # as Verilator-proven SVA (decisions.md D212–D214)
├── llm/                    # shared LLM-client plumbing (flux-llm, decisions.md D200)
├── frontends/              # onnx/ (started) — mlir/ pytorch/ yaml/ not started
├── stores/                 # result store, corpus loaders
├── interfaces/             # CHIA nodes, MCP tools, CLI
│   ├── chia_nodes/ mcp/ cli/
├── corpus/                 # benchmark workloads + reference archs (holdout partition separate)
├── containers/             # Dockerfiles per evaluator backend — not started
├── docs/
└── tests/
    ├── unit/ integration/ conformance/ golden/
```

**Conformance tests are the load-bearing directory.** Any new evaluator backend must pass a shared
suite proving it interprets the IR the same way as the reference, or that it fails loudly on the
parts it cannot express. Without this the contract decays into a suggestion within two releases.
`generation/`'s own real architecture-candidate generation (decisions.md D91) is held to exactly
this same standard — `flux_conformance_check`'s own real mechanism, not a separate, looser one —
rather than the originally-imagined dedicated `generation/conformance/` subdirectory, which never
existed once the real capability was actually built.

## What this explicitly does not do (v1)

- Does not replace CHIA, TVM, Deeploy, or MLIR.
- Does not attempt full-system simulation.
- Does not model training — the IR reserves room for it (`phases`, tensor lifetimes) but no cost
  model covers optimizer state or gradient traffic.
- Does not ship its own NoC or thermal model as a default — the schema slots exist; NoC has a real
  model now via `evaluator/booksim` (adapter, not built-in); thermal has a real model too now via
  `evaluator/thermal` (3D-ICE, [decisions.md D64](decisions.md)/[D65](decisions.md)) — both
  adapters, not built-in, per this same design principle.
- RTL *generation* is in scope long-term (unlike the original v1 proposal) — real now on two
  distinct axes: module-level implementation generation (`codegen/`, decisions.md D39–D55) and
  whole architecture-candidate generation (`generation/`, decisions.md D91), both gated behind —
  and both real callers of — calibration + independent validity checking, live since Phase 2, see
  [roadmap.md](roadmap.md). Conformance-checking is the acceptance gate for any generated design,
  not a substitute for generating it.
