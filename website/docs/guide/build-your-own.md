# Build your own loop

A loop is a `Problem` plugged into the shared loop (`core/loop`, package `flux_loop`;
decision D421). You implement what makes your problem different -- what a candidate is,
how a model is asked for one, how it is built, the fast test the generator iterates
against, the gate that admits, the costlier stages that measure, how parts compose, how
to decide -- and `run_loop` does everything every loop needs: the campaign record and
its read-back, operator feedback, resuming from proven and best-so-far parts, planning
with cooldowns, the generation<->test inner loop with structured decoding, find/replace
patching, revert-on-breakage and your tools, the judge, composition, the cached chain,
the frontier, the decision and the conclusion written back.

`Problem` is the four roles of the drawing combined (D427): `MentorRole`,
`OrchestratorRole`, `GeneratorRole` and `EvaluatorRole`, each a class of hooks with
defaults. Subclass `Problem` for the usual case; assemble one from roles when a role is
shared -- an evaluator role reused across problems, a generator role specific to one.
The package itself is split the same way (`flux_loop.types`, `problem`, `model`,
`patch`, `compute`, `gradient`, `prototype`, `generation`, `records`, `loop`, `graph`),
and `flux_loop.graph.NODES` is the one table the timing tree, task list and log color by.

**Or write no code at all.** A task can enter as a document (D430): `flux task run
task.json` builds a `PromptProblem` from it -- the statement and contract become the
prompts, the gate's `build`/`test` commands the checks, the `stages` the measurements
(commands with `metrics_re`, or evaluators by registry name), the `objectives` the
frontier and the decision; `"parts": "decompose"` lets the orchestrator's model divide
the task itself, and the record remembers the division for a resume (D431); `"brief":
"propose"` has it brief each part and set the part's repair budget (D432).
`"critique": "propose"` makes the model the adversary over the division, each admitted
candidate and the decision, with the measured gate keeping the last word (D433).
`flux task check` lists what a document declares and which
tools are missing; `--replies` runs one with scripted replies and no model. The example
is [`core/loop/examples/digits.task.json`](https://github.com/choelzl/flux/tree/main/flux/core/loop/examples/digits.task.json).
Subclass `Problem` when the task needs what a document cannot say: a table oracle, an
exhaustive reference, a prototype stage.

The five nodes of the loop drawing, and the hook each one is:

| node | role | hooks on `Problem` (all have defaults) |
|---|---|---|
| input / knowledge / records | mentor | `objective`, `tools_missing`, `prepare` (the model's test-author role goes here), `knowledge` (the mentor's declared sources -- a sheet, the library, the record's read-back, notes -- from which the loop assembles both the mentor tab and the static prompt prefix, D449), `open_records` |
| (1) Orchestration | orchestrator (+model) | `search` (a generator of candidate BATCHES, D446), `space` (the knobs a `sweep` / `montecarlo` / `anneal` orchestrator searches, D465), `next_work` (which kind of work the next step is for, D457/D463), or the parts path: `subgoals`, `subproblems` (parts that are each their own loop, D455), `decompose` (may return part names, `SubLoop`s, or both), `plan_next` / `plan_prompt` (default: the model chooses from a schema whose `next` is an enum of the menu; override to enumerate, anneal, climb a fixed chain) |
| (2) Generation | generator (+model) | `generator` (WHO drafts, D456: `sources.Model`, `Template`, `Catalog`, `Solver`), `generate` (default: the sub-loop around that source -- the model's own for `Model`) or `design_prompt` + `parse_design`; `prompt_prefix` (the static part, sent first for cache reuse), `patch_prompt`, `rewrite_prompt`, `locate` (fault lines for a focused repair window), `apply_tools`, `describe_failure`; `prototype_spec` + `prototype_check` (prove the algorithm as executable Python at 0 over before any target code -- the loop transcribes only a passing prototype); the loop adds a sandboxed compute tool to every generation turn |
| (3) test / validate | evaluator, fast | `build` (raise `BuildError` to refuse), `fast_check` -> (failures, text) |
| (4) Evaluation | evaluator, the chain | `judge` -> `Verdict`, `compose`, `stages` (any evaluators in any order), `cutoff` (what is worth the NEXT stage -- `flux_loop.cutoff.above` / `below` / `within_best`, D454), `route` (what goes back to the GENERATOR to be improved, D463), `calibrated` (what a costly stage said about a cheap one, D464), `measure` or `measure_batch` (one call per batch, the ABI's one-result-per-candidate rule), `analytic_stages` / `analytic_metrics` / `evaluator_name` (the record's method tag and provenance), `cache_suffix` (None: the problem caches for itself) |
| (5) records -> output | orchestrator | `frontier` / `frontier_axes`, `finalists`, `review`, `decide`, `conclusion`, `good_enough` (an optional early stop, D463) |

Divide / conquer / combine is native: `subgoals()` names the parts, each part runs the
inner loop, admitted parts are frozen, `compose` joins them for the outer chain.

**Or work in BATCHES** (D446), which is what a study that enumerates, solves, climbs,
crosses two fields or lets a model direct a menu actually is: `search(state)` yields BATCHES
of candidates, the loop gates each one and measures the admitted on the first stage together,
and the batch's results come back as the value of the `yield` -- so the policy keeps its state
in local variables instead of in a framework. The chain above it does not change.

**Or switch a whole role** (D460). `Problem.roles()` returns a `Roles` bundle -- one slot per
role, `None` meaning "my own hooks" -- and `flux_loop.rig(orchestrator="rules")` builds one from
names. Orchestration's components are `Rules` (code decides the next part), `Given` (the user's
division, in order) and `ModelOrchestrator` (a model decides); generation's are the four sources
below; evaluation's AI half is `Surrogate` (D461), a stage fitted on this campaign's measured
trials that screens candidates and never answers; knowledge's is `Mined` (D462), the facts this
project's own data supports plus the conclusions drawn from them. Set it in your constructor,
accept it as an argument, or let a document or a `--role` flag set it for you. If your problem
overrides `stages`, `measure`, `cutoff` or `prepare` and you want the evaluation component to
work, return `self.chained([...])` and defer to `self.role_measure(...)` /
`self.role_cutoff(...)` -- two lines each, and the hooks' own docstrings say so.

**Or hand the drafting to something that is not a model** (D456). `generator()` returns a
`flux_loop.sources` source -- `Template` (a renderer), `Catalog` (designs that already
exist) or `Solver` (computed from the constraints) -- and the loop runs the same
draft/build/fast-check iteration around it, feeding each failure back through `Attempt`.
Nothing else changes: the gate, the stages, the cutoffs and the decision are the loop's
either way.

**Or make a part a loop of its own** (D455). Return `SubLoop(name, problem, request,
statement)` from `subproblems` (declared) or from `decompose` (decided this run) and the
loop runs that child end to end -- its own gate, its own stages, its own record -- then
takes its decision as the parent's admitted part. Use it when the pieces are not one
artifact: a memory subsystem and the fabric that reaches it are two design spaces, not
two halves of one file. `LoopRequest.max_depth` (3) bounds the nesting.

The smallest complete client is the toy in
[`tests/unit/test_loop.py`](https://github.com/choelzl/flux/tree/main/flux/tests/unit/test_loop.py)
-- match a hidden byte pattern, one half at a time, no model, no tools -- and it
exercises every mechanism above. The first real client is the NLU: a problem document,
[`applications/nlu/nlu.problem.yaml`](https://github.com/choelzl/flux/tree/main/flux/applications/nlu/nlu.problem.yaml),
that names its world,
[`applications/nlu/lib/src/flux_nlu/world.py`](https://github.com/choelzl/flux/tree/main/flux/applications/nlu/lib/src/flux_nlu/world.py)
(FP16 operators proven by exhaustion, a table oracle, yosys/OpenROAD stages). Then:

1. **Say the problem as a document** (`flux task check` reads it): the parts and their
   order, the objectives, the ladder, the stages, the knowledge, the budget (the loop's own
   knobs: steps, repair attempts, patching, cooldowns, finalists) and the `params:` your
   world is built from. What prose and commands cannot say -- a toolkit, a transpiler, an
   exhaustive judge, how parts compose, how a design is measured -- is a `world:`, a
   package whose methods are `Problem` hooks; the document problem binds the ones it has.
   A world is the only Python a new problem needs; a new ask in a world that exists is a
   document.
2. **Write the candidate and its gates first**: `Candidate.artifact` is the text a tool
   runs -- or empty for a problem whose candidates are points, which then live in
   `Candidate.knobs` and cache and record by `Candidate.key()` all the same; `build`
   refuses what cannot be built; `judge` is exhaustive or proof-grade where it can be.
   The gates are where most of the loop's speed lives.
3. **Give the generator a fast test.** `fast_check` runs in milliseconds and the inner
   loop edits toward it; a problem without one iterates only on building, which the
   NLU campaign measured to be the wrong target.
4. **Pick the chain**: `stages()` in rising cost, `measure` per stage; the last stage is
   the one the report quotes, and the loop caches by tool fingerprint and artifact.
5. **Hand the model its tools.** `apply_tools` is where a reply's requests are honoured
   -- a table computed exactly, a solver run -- so the model decides and the rig does
   arithmetic (D420).
6. **Register it**: add `applications/<name>/lib/src` to the flake's `localSrcDirs`; wrap
   the flow as a CHIA node in `interfaces/chia_nodes/` and an MCP tool in
   `interfaces/mcp/` -- it then appears in the [tool catalog](../catalog/index.md).
7. **Test the behaviour, not the tools**: drive `run_loop` with a scripted proposer and
   monkeypatched `build`/`judge`, and pin the *order* of what it asks for and refuses.
8. **Record the decisions**: every non-trivial choice gets a numbered entry in the
   decision record -- the "why" the next person reads.

EVERY application in the repository runs on this contract as a document (D519, D533, D540)
-- macarray, bankmap, interconnect_mapping and prefetcher in batches (D446), the NLU part by
part, and omni as rounds of tool steps (D458); the
[loop-shape page](loop-shape.md#the-five-nodes-as-an-interface) shows which hook each of
their pieces is. omni looked like the exception (no candidate, no objective, only model
rounds over a tool catalog) and was not: a round is a batch, `validate_step` is a gate,
running a tool is a measurement, and what stays omni's own is that it concludes in words
instead of deciding a design.

## Where the shared pieces live

Everything an application needs already exists as a package; building a loop is mostly wiring.
Paths below are under
[`flux/`](https://github.com/choelzl/flux/tree/main/flux) in the repository:

| you need | use | from |
|---|---|---|
| a measurement cache that survives resumes and dies with a tool bump | `MeasurementCache` (namespaced by tool fingerprints) | `evaluator/cache` |
| a queryable record of every trial, readable back as seeds | `flux_records.Records` over `CampaignStore`: trials, refusals, conclusions, notes | `mentor/records` + `core/stores` |
| laws and head-to-head verdicts extracted from the record | `flux_extract.pairwise_laws` / `head_to_head` | `mentor/extract` |
| the decision arithmetic: corners, the knee, target-and-floor | `flux_frontier.decide` | `core/frontier` |
| the closing report grammar (ESTABLISHED / NOT ESTABLISHED / REFUSED) | `flux_report` | `core/report` |
| frontier, confirmation spread, budget rules for two objectives | `flux_frontier` | `core/frontier` |
| a tree policy allocating waves across the frontier | `flux_frontier.pareto_uct` | `core/frontier` |
| the loop itself: planner, inner loop, judge, compose, chain, decide, record | `flux_loop.run_loop` + `Problem` | `core/loop` |
| a model-directed menu of actions, with coverage and a stopping rule | `flux_loop.actions`: `Action`, `Outcome`, `DirectedSearch` | `core/loop` |
| one model call, schema-constrained or with tools, thinking per request | `flux_llm.OpenAIChatProposer` -> `Reply` (D508) | `core/llm` |
| a bounded ask-check-repair round: ask, check for real, repair with the failure attached | `flux_llm.ask_until` / `refine` | `core/llm` |
| knowledge as declared sources, assembled under a budget | `flux_knowledge.Mentor` + `Corpus`/`Library`/`RecordReadback`/`Notes` | `mentor/knowledge` |
| operator guidance typed while the loop runs, drained into proposer prompts | `drain_guidance` + `reload_notes` (resumed runs re-show earlier notes) | `mentor/feedback` |
| real silicon numbers on ASAP7 | `run_synthesis_flow` (seconds) / `run_ppa_flow` (placement) | `evaluator/openroad` |
| Verilator verification of generated RTL against golden vectors | `compile_and_run` + `DesignSpec` | `generator/harness_rtl` |
| what a harness is told and what it reports, in any language | `DesignSpec`, `CompositionSpec`, `HarnessRunResult` | `generator/harness_spec` |
| the evaluator contract and the backend registry (zigzag, timeloop, rtl, openroad, ChampSim) | the `Evaluator` ABI, `make_evaluator(name)` | `evaluator/abi` |
| tool fingerprints for cache keys and provenance | `toolchain_fingerprint` | `evaluator/abi` |

## Where things live

The tree follows four module types, so where a thing lives tells you what kind of thing it is:

| directory | what lives there |
|---|---|
| `generator/` | making designs and code from instructions: architectures, RTL, SystemC, harnesses |
| `evaluator/` | scoring designs with real tools: the ABI, the tool adapters, validity, calibration |
| `mentor/` | knowledge that guides the rest: document corpus, mined facts, protocol specs, benchmarks |
| `applications/` | one folder per design problem: its document, its world package, its README |
| `core/` | the one loop (`core/loop`), the frontier arithmetic, the IR, the stores, the LLM layer, profiling, the TUI |
| `interfaces/` | how it is driven: the CLI, the MCP server, the CHIA nodes |

`applications/` is the one that matters for growth: a design problem is a document naming its
world, and the next problem gets the same shape (`<name>.problem.yaml`, `lib/`) rather than
new top-level directories. Adding a design problem should not change the top level at all.
