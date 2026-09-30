# Design decisions

What Flux does, and why, today. Each point is a decision and its key reason or number; the
D-numbers at the end of a point are the decision numbers the code cites. A new decision gets the
next number and goes under [Since the fold](#since-the-fold).

## Contents

- [The problem document](#the-problem-document)
- [The loop: steps, passes, roles](#the-loop-steps-passes-roles)
- [Search: DSE policies and phases](#search-dse-policies-and-phases)
- [Gates, checks and scoring](#gates-checks-and-scoring)
- [Generation: prompts, repair, replies](#generation-prompts-repair-replies)
- [Prototypes, golden models and spelling](#prototypes-golden-models-and-spelling)
- [Models, coding agents, skills and flux ask](#models-coding-agents-skills-and-flux-ask)
- [Stages, evaluators, measurement and calibration](#stages-evaluators-measurement-and-calibration)
- [The record, reports and running campaigns](#the-record-reports-and-running-campaigns)
- [Knowledge, mining and operator feedback](#knowledge-mining-and-operator-feedback)
- [The IR and the evaluator ABI](#the-ir-and-the-evaluator-abi)
- [The applications](#the-applications)
- [The command line, the TUI, docs, packaging and tests](#the-command-line-the-tui-docs-packaging-and-tests)
- [Since the fold](#since-the-fold)

## The problem document

- **A task is a document, not code.** YAML or JSON, run by `PromptProblem`; `flux task check` validates, `flux task run` runs. What a document cannot say goes in a `world:` package (the NLU is `nlu.problem.yaml` + `flux_nlu.world.World`). (D430, D519, D520, D541)
- **Typed, closed schema.** Unknown keys and unknown `space:` placeholders are load errors; `cache:` sets caching. (D541, D581)
- **A document says only what is its own.** `extension` comes from `language`; `gate:` may be the command alone (count defaults to `N failing`, `count_re`/`fail_re` for other checkers); a `flux rtl measure` stage knows its metrics and tools; a known metric has its unit; a goal is judged on the deepest stage. `budget` has eight common knobs, the rest advanced. (D628)
- **Short documents.** A 1-3 line header with the run command; a comment only where a value is not obvious; no keys set to their default. (D631)
- **`flow:` is the only spelling of the drawing.** Boxes, in order: validate, orchestrate, plan, dse, generate, test, critique, analytical, simulation, calibrate, select, feedback, knowledge, extract, records. There are no `roles`/`generator`/`critique`/`decompose` keys; `--role` and `Roles(...)` switch a role per run. `flux task check` prints the resolved flow. (D542, D629)
- **Every stage measures every objective.** Each stage ranks its own rows, so a stage missing an objective has no front; `PromptProblem.validate` (run by `task check`) refuses it. (D625)
- **The world contract.** `CONTRACT` in `flux_loop/document.py` lists the hooks a world may fill, by box, with fourteen `CORE` ones; a world defining `loop_owned()` is refused with the contract printed. (D561)
- **Commands and metrics.** Commands are shell-like strings; a command headed `flux` runs this Flux; stages report `metrics:` `name=value` lines. (D580)
- **Sub-documents.** `subtasks:` children inherit world, hooks, ladder, cache and flow, and keep their own record `<parent>/<child>`. (D555)
- **The space.** `seeds:` are measured first (the first is home); a knob with `when:` moves only while other knobs hold given choices, otherwise it sits at its first choice, so no two points differ only in a dead knob; phase `knobs`/`hold`/`keep` take globs; `space:` components with `optional: true` add `<component>.on`; `{point}` is the whole point as JSON. (D634, D637)
- **A goal relative to the best.** `keep: 0.9, above: 1.0` means within 90% of the best design's gain over 1.0; then the least on the next objective. (D634)
- **Extension beside the document.** Worlds, hooks and `module:Class` policies resolve relative to the document; `docs/extending.md` lists the extension points. (D602)

## The loop: steps, passes, roles

- **One problem-agnostic loop.** `flux_loop.run_loop` owns records, planning, generation, judging, composition, stages and the decision; a `Problem` supplies hooks. No hand-written design families. (D421, D425, D507)
- **Four swappable roles.** `Problem` = `MentorRole` + `OrchestratorRole` + `GeneratorRole` + `EvaluatorRole`, each with an AI and a no-AI half; one `Gradient` serves prototype and generation; a `Candidate` may be a knob point. (D427, D460)
- **Part by part.** Plan one part, generate it, judge it alone, admit and freeze it; compose only proven parts. Parts are named once in the document; the `given` orchestrator only orders them. (D412, D566)
- **One step, one work item.** A part, a sub-task or a search batch, chosen by `next_work`. `budget.parallel_parts` drafts in parallel but admits in order on the loop thread. (D457, D569)
- **Decomposition and plans.** The orchestrator may split a task, brief each part and set its repair budget; these join the record and are reused on resume. `flow: {plan: llm}` gives a method per part from the library digest index; a problem with no parts is one goal the plan may name (`*`). (D431, D432, D577, D623)
- **Sub-loops.** A part may be a bounded-depth `SubLoop` whose decision becomes the parent's part. (D455)
- **Search path.** A search generator yields batches gated and measured together; results go back to the policy. (D446)
- **Advisory critic.** It may delay once or add a caveat; it never vetoes the gate. (D433)
- **Typed memory, resume from the best.** `PartState` per part, a `Ledger` across passes (a rest is an entry). Each part's best refused attempt reloads every pass and generation resumes from it in patch mode; up to six admitted designs are kept per part. Rows carry their prompt digest; `StageNames` (gate, admit, prototype) is the loop's own vocabulary. (D415, D438, D509, D528, D535)
- **Improve, revert, regress.** Any stage can send a design back; validate runs first. Regression is judged against the last measured attempt; reverting is a tolerance earned by progress and spent by regression; an unmeasured score is never best or a revert target. (D463, D470, D491, D504, D535)
- **Routing and rest.** The whole's shortfall scales each part's goal; a pass that admits nothing and sends nothing back is at rest; so is one whose search handed over its last batch. (D518, D535, D621)
- **Runs end only when stopped.** `flux stop`, Ctrl-C or a cap (`--passes`, `budget.passes`). After rest a pass explores by sending every admitted design back; without a generator it waits. With `--passes N`, a run at rest with nothing to draft ends. (D593, D621)
- **Model I/O rules.** In-turn tools on by default; text tool calls must start a line; dicts of ROMs and data `in` are refused in generated code. (D470, D491, D507, D535)

## Search: DSE policies and phases

- **One frontier library.** `flux_frontier` owns Pareto dominance, the interval rule and the decision arithmetic (`corner`, `knee_ranked`, `cheapest_meeting`); dominance, the note sink, record read-back and `run_tool` are each defined once. (D399, D429, D439)
- **ParetoUCT.** Hypervolume-driven tree policy; its rollout estimate only orders moves and is never recorded. (D368)
- **The objective vector.** Ordered, with one `better` rule; tournaments, frontier axes, decisions and the goal check derive from it. (D511)
- **Goals, stages and margins.** A goal is judged on its objective's stage; a shallower stage must clear it plus a margin learned from measured stage-to-stage ratios (`margin:` is the floor). Meeting a goal is a floor, not a stop. (D522, D543, D562)
- **Tool result size.** Cut at `budget.tool_result_chars` (40,000). (D543)
- **Policies from the document.** `flow: dse` names sweep, montecarlo, anneal, gradient, genetic, or `llm` (the model proposes the next batch); it may list phases with floors, margins, waves and patience; worlds may add `seeds` and `moves` hooks. (D553, D554, D583)

## Gates, checks and scoring

- **Deterministic RTL harness, no LLM.** Verilator with a generated testbench drives a DUT against declared vectors from one `DesignSpec`. Clocked designs get harness-owned `clk`/`rst_n`, a reset sequence and posedge-synchronised vectors. (D39, D43, D49, D54)
- **Deterministic composition.** Verified leaves compose through a declarative netlist (`CompositionSpec`) whose top module is generated, then verified whole; `clk`/`rst_n` fan out to every clocked leaf; array ports are refused. (D48, D50, D55, D128)
- **Latency and array ports.** A harness-owned start/done handshake bounded by `MAX_LATENCY_CYCLES` (10,000) measures latency. Ports may be unpacked arrays (`Port.depth`); a spec with no outputs is refused. (D115, D120, D124)
- **Names and widths.** Every generated identifier is checked against SystemVerilog reserved words. `int` ports carry a bit width; a 1-bit port is a `bool` (plain `logic`), because a signed 1-bit integer holds only 0 and -1. Nets joining different widths are refused. (D51, D192, D202, D203, D621)
- **One header scanner.** `sv_parse` scans module headers with balanced parentheses for the synthesis array-port guard. (D179)
- **Lint before simulate.** `slang` checks generated source before Verilator when available. (D411)
- **Test-driven generation.** The inner loop runs the part's fast vectors and keeps editing on failures. `fp16.describe_failures` tabulates failures by magnitude band and sign so the model cannot misread them. (D416, D419)
- **Build failure is not a score.** `flux rtl test` exits 3 when it does not compile (1 when it fails); broken drafts are never scored. (D594)
- **A golden model fills its widths.** The author check refuses an unsigned output whose top bit no vector sets (float outputs in ULPs exempt). (D622)
- **Advisory validation.** `flow: {validate: llm}` logs objections as lessons; it never stops a run. (D556)
- **Adversarial evaluator check.** A live test checks ZigZag against Verilator for a reward-hackable direction. (D101)

## Generation: prompts, repair, replies

- **Generation behind independent gates.** A design that fails its gate is refused, never ranked. The drafter is a role (Model, Template, Catalog or Solver); non-model sources share one draft-build-check loop. (D2, D456)
- **Derived specs.** `flux_evaluator_openroad.derive` turns an architecture/workload pair into a `DesignSpec` with hash-seeded golden vectors. (D185, D625)
- **Deterministic wrappers.** A generated wrapper owns handshake, counter and reset around a combinational step, with `ceil(C/lanes)` cycles and a zero-padded last tile. `generate_gemm_wrapper` reproduces `mac_array.sv`'s schedule and masks a ragged final K-group, keeping cycles closed-form. (D117, D118, D121, D130, D134)
- **One ask-check-repair round.** `flux_llm.ask_until` and `refine`. (D450)
- **Structured replies, lenient parsing.** Schema-constrained output, so gates check content, not shape; parsing falls back from strict JSON to JSON in prose; SEARCH/REPLACE blocks are patch edits. (D413, D603)
- **Repair is a patch, reverted if it does not converge.** Find/replace edits must match exactly once; after `revert_after` (3) failed compile repairs the loop reverts to the last source that built. Each repair turn opens with a PROGRESS line. (D414, D417, D423)
- **Errors in the model's terms.** Compile errors in the model's own line numbers, with the line, a caret and a hint (`explain_diagnostic`). (D473, D552)
- **Prompt layout.** Static prefix first for server caching; a focus window around failing lines; prototype prompts carry paper excerpts and the part's score history. An unmeasured rewrite of a nearly-right design is refused. (D422, D500, D501)
- **Table oracle.** The model names a function table; the harness computes the entries as a ROM. (D420)
- **Compute sandbox.** numpy, math, struct and the pure standard library only. (D422, D545)
- **Context compaction.** Past `budget.compact_share` (0.6) of the window, older rounds are digested; knowledge sources are densified then cut, keeping what is nearest the part. (D549, D550)

## Prototypes, golden models and spelling

- **Prototype before RTL.** The algorithm is proven as a prototype judged by the same gate; only a passing prototype reaches RTL. Any gate naming `--golden` can prove one first; a missing golden model is written by the model. `--no-prototype` skips it. HCL and HLS front-ends are declined. (D424, D472, D502, D604)
- **Two prototype languages.** `budget.prototype`: true/python (an integer Python prototype) or `systemc` (a synthesizable `SC_MODULE`, built with g++ against libsystemc and run on every vector). `golden_proto.compare` gives both the same failure report. (D635)
- **The flow spells verified prototypes.** `flux_loop/py2sv.py` transpiles Python to width-exact SystemVerilog (if-conversion, unrolling, inlining, ROM tables) and checks it on every input; ICSC (`icsc-sv`, `.#icsc`) translates a SystemC prototype, port widths checked against the golden model. The model transcribes only what cannot be spelled (Python over 20 input bits, SystemC without ICSC). (D478, D606, D611, D636)
- **Exhaustive checking.** Every input when inputs total at most `EXHAUSTIVE_BITS` (20). (D606)
- **Static refusals.** Float arithmetic on the data path, file I/O, domain-sized tables, redefining a toolkit block; reloads re-verify prototypes under today's rules. (D468, D480, D482, D605)
- **Toolkit and composition.** Verified integer blocks (fields, normalisation, fixed point, tables, rounding, packing, `recip_fixed`) are prepended to every prototype; verified operators become blocks for other parts. `from_fixed` is exact for any fixed-point magnitude. (D481, D487, D488, D489)
- **Knobs and spaces.** A prototype may declare knobs and a SPACE; the cheapest passing member is bound. (D479)
- **Automatic if-conversion.** Per-element prototypes become array form, with a line map back. (D483)
- **Diagnosis on the model's data.** Attempts are traced; near-misses from repeated FP16 rounding are diagnosed as a precision floor. (D482, D488)
- **Edits versus new prototypes.** A reply with edits is edits; a >= 80%-same "new" prototype runs as an edit; a streak of unmeasurable attempts ends a pass. (D480, D607)
- **Loop-owned machinery.** The loop owns language rules, prompts, history and repair reminders (`flux_loop.pyint`, `check.check_prototype`); a problem supplies harness, judge, cost and Target. (D514, D515, D516)
- **Improve ladder.** `flux_loop.ladder`: register sweeps, depth passes (a logic-depth proxy ranks), contenders and from-scratch redesigns, generic over objectives; a pass's budget grows while it improves. (D496, D499, D506, D517)
- **Pipelining cuts.** On delay-weighted levels; `keep_nets` is opt-in. (D527)
- **Formulas, not lookups.** Module-level tables of at most 64 entries (`budget.prototype_table_max`); a float input keeps its exponent and works on the mantissa. (D616)
- **Failures shown where they are.** Grouped by the first input's six leading bits, examples from the largest failing ranges. (D617)
- **Nothing oversized is built.** `py2sv.cost` estimates hardware (about 3 units per um2 on ASAP7); over `budget.prototype_cost_max` (2,000) a prototype is made cheaper before it is spelled, since synthesis time grows much faster than cost. Spelled designs carry `prototype_sha`. (D613, D615, D619)
- **Spellability checked early.** A prototype that passes every input but cannot be spelled is not verified. (D618)

## Models, coding agents, skills and flux ask

- **One home for model plumbing.** `flux_llm`; `default_local_model()` is the one model-tag source (`FLUX_LLM_MODEL` overrides). Every measurement names its model tag: results across models are not comparable. (D200, D289, D375)
- **One Proposer protocol.** `propose(prompt, schema, tools, budget) -> Reply`, served by `OpenAIChatProposer` or `ScriptedProposer`. Hosted models are opt-in via `FLUX_LLM_REMOTE`, never by a key's presence. (D337, D469, D508)
- **The model is checked first.** `OpenAIChatProposer.preflight()` asks `/v1/models` and says why a run cannot start (no server, key refused, model not listed); `task check` prints "ready" or "NOT READY". (D623)
- **Thinking and streaming.** Per request; replies stream live into the task row; a thinking loop or prose-only think channel is re-asked with thinking off. (D469, D493, D494, D503)
- **Tool-calling rounds.** A round may write `budget.hop_share` (0.5) of the window; a second tool-call parse failure ends the turn as unusable, not failed. (D544, D596)
- **Optional agentic halves.** In-turn tools, an agent orchestrator, an agent-written plan. (D505)
- **External coding agents.** `generate: {agent: claude|codex|opencode|{command}}`, headless, stdin closed; the brief names the gate command; questions follow `questions: decide|model|operator`, capped by `max_questions`. (D575, D585, D595)
- **An agent writes the prototype.** With `prototype: true` it writes the prototype and runs `flux rtl proto`; the loop spells the RTL. A silent turn is nudged twice at most; an outgrown session continues fresh. (D618)
- **A coding agent in any box.** `flow.<box>: {agent: opencode|claude|codex}` for validate, orchestrate, plan, dse, generate, critique, extract and select; test, the stages, calibrate and records are never delegated (D460). The loop writes `BRIEF.md`, checks the agent's `out.json` against the box's schema and rule, sends a refusal back once, then falls back to the rules half; each turn is a `decided:agent_turn` event that `flux report` lists. `select` only breaks ties the objectives leave open. (D630, D640)
- **`flux ask`.** A model or coding agent writes and checks a document from a prompt, then the loop runs it; `golden.py` may declare `TOLERANCE_ULP`; a duplicate `*.problem.yaml` copy is dropped. (D586, D589, D627)
- **Skills.** SKILL.md skills are copied into coding agents' folders or inlined for the loop's model; `skills/flux/SKILL.md` teaches outside agents to use Flux. (D588, D592)

## Stages, evaluators, measurement and calibration

- **A chain of stages.** Each with an optional cutoff; the decision reads the highest stage with results; an escalation target must be a registered evaluator. (D448, D454, D466)
- **Cheap-to-costly ratios and the surrogate.** The loop records costly/cheap ratios as calibration. `flow: {analytical: [surrogate]}` predicts the costly stage to order finalists; predictions (`<stage>~predicted`) never decide. (D464, D560, D629)
- **Real synthesis ranks verified RTL.** Yosys, composites via their leaves plus the top; unpacked array ports refused up front (`UnsupportedForSynthesisError`); failures are typed. (D47, D52, D61, D96, D127)
- **ASAP7 area, confidential-PDK guard.** Vendored ASAP7 liberty gives um2 with breakdowns; the raw synthesis entry calls `require_not_confidential`, and an unregistered PDK is refused. (D92, D93, D94, D96)
- **Synthesis needs OpenROAD.** The screen times the netlist with OpenSTA, so `flux rtl measure` needs `yosys` and `openroad`; stages declare `needs: [yosys, openroad]` and are skipped, with a message, without them. (D626)
- **The OpenROAD flow.** `run_ppa_flow`: Yosys (optionally `yosys-slang`), ASAP7 placement, `repair_timing`, optionally routed with CTS and RCX; always full ABC (in the toolchain fingerprint); area, power, worst slack. (D225, D229, D254, D274, D276, D277, D278, D564)
- **Timing paths are data.** `report_checks` is parsed into every `PpaReport`, kept per part and offered through the `timing` tool. (D274, D277, D440, D526)
- **Datapaths sized to the workload.** Ports at the workload's true precision; exact-width per-lane products. (D228)
- **One RTL check, one measurement.** `flux_codegen_rtl_harness.golden` (`check_rtl`) and `flux_evaluator_openroad.measure_rtl`, behind `flux rtl test --golden` and `flux rtl measure`. (D453, D567, D579, D582)
- **RTL attributes are not a search axis.** `full_case`/`parallel_case` change nothing; `keep` only costs. (D568)
- **Measurement caches.** `flux_store.CachingEvaluator` reuses results by hash lineage (matching `arch_hash` client-side); Yosys results sit in a `ToolResultCache`; `flux_cache` holds the toolchain-invalidated cache, keyed also on every tool a stage `needs:`, so a rebuilt simulator misses. (D19, D89, D172, D344, D436, D567, D634)
- **Parallel measurement, serial bookkeeping.** Batches measure on threads, cache/record/state stay on the loop thread; an admitted part is measured while the next drafts (`budget.ahead`). OpenROAD `-threads` via `FLUX_OPENROAD_THREADS` (else cores / 4, at most 16). (D525, D563, D570)
- **Stage failures are explicit.** `timeout_s` per stage; a failure records its error text. (D537)
- **The evaluator ABI owns shared machinery.** Registry, one `NotExpressibleError`, `SequentialBatch`, `ToolSource`/`build_step`. (D426, D437, D440, D441, D451)
- **Metrics through `Result` accessors.** `value_of`/`estimate_of` only (an AST test forbids `Result.metrics[...]`); `metric_domains` keeps per-metric calibration domains; records claim `Limiter.NONE` unless a cost model named one. (D140, D211, D440, D441)
- **Calibration learns from conformance.** `record_conformance_residuals` feeds predicted/reference pairs; `calibration_attempts` counts references bought, not measurements. (D98, D106, D114, D318)
- **When calibration corrects.** Only with at least three distinct (workload, arch) points; shift by the mean residual, span the spread; the tight interval only where measured exactly (`validated_here`). (D106, D112, D122, D171)
- **Caveated measurements stay out of the pool.** ZigZag's `caveat_for` marks `lanes == C` residuals. (D110, D112, D113)

## The record, reports and running campaigns

- **One SQLite file, intent before evaluation.** `CampaignStore` shares `ResultStore`'s file; a trial's intent commits before evaluation; budget and frontier are derived. Stored results stay readable for every status. (D217, D264, D524)
- **The record is named by the document's id.** `TaskSpec.record` is the id, or `<parent>/<child>` for a sub-document, so an edited document resumes its record; a copy needs only a new `id:`. Records older than schema v2 do not open. (D524, D628, D633)
- **Provenance and trust on reload.** Rows carry provenance; reloads trust rows by transpiler/judge version; prototypes reload separately, re-verified. (D471, D510)
- **Resume reads the record back.** Seeds the pool and first prompt with the best known configurations; named parts can be regenerated. (D367, D476)
- **Record readers in `flux_records`.** Trials, refusals, conclusions and notes with typed rows; one `record_read_back`; `controlled_pairs` feeds pairwise laws and head-to-head duels. (D397, D444, D445, D565)
- **Modules sit by role.** `mentor/feedback` (`flux_feedback`), `evaluator/cache` (`flux_cache`), `core/frontier` (`flux_frontier`). (D401)
- **Leaderboards and replay.** The corpus leaderboard ranks stored results by an entry's objective, holdout-safe; `flux replay` refuses a dangling `mapping_hash`. (D58, D189)
- **`flux report`.** One self-contained HTML page per campaign: frontier with hypervolume, best-so-far, passes, pairwise fronts; single-design campaigns table every point with its knobs. (D512, D523, D609)
- **Where a run writes.** Under the document's `<home>/out/` (git-ignored); `flux gc` removes unnamed trace roots. (D510, D578)
- **Operating a run.** `flux status`, `flux stop` (at a pass boundary), `flux run` (detached) and `flux attach`, found through `<record>.runs.json`; every model and agent turn goes to `turns.jsonl` (`flux log`). (D513, D597, D599)
- **Where the time goes.** `flux_profile` accounts wall-clock at tool, simulator and model-call choke points. (D295)

## Knowledge, mining and operator feedback

- **Knowledge is retrieved context, not training.** (D3)
- **Licence first.** A standard is ingested only after a primary-source licence check (AMBA, JEDEC, PCIe, I2C are closed); the corpus holds RISC-V unprivileged chapters and original design guidance, each with a `PROVENANCE.md`. (D31)
- **Retrieval.** `knowledge_lookup` ranks the whole BM25 corpus before filtering by `standard_id`; one paragraph splitter feeds all connectors; `library_context` renders each chunk once. (D164, D180, D244, D443)
- **The library.** Git-ignored papers and source (fpnew, HardFloat, FloPoCo) under `mentor/knowledge/library`, indexed as "library"; `digest_library` stores one model digest per document by content hash, used by the planner's index and `flow: {knowledge: [digest]}`. (D407, D477, D576)
- **Knowledge sources and their budget.** `Corpus`, `Library`, `RecordReadback`, `Notes`, `Mined`; together bounded by `budget.knowledge_share` of the window. `flow: {extract: mined}` adds the record's mined lessons. (D449, D462, D548, D629)
- **Mined facts are typed.** Each `Fact` is computed from stored rows with fixed wording, pointers, scope and a `not_established` boundary; never free text. `Mined` renders the refusals, grouped by message. (D243, D297, D633)
- **Plain arithmetic from the record.** One-knob-differing pairs give per-knob directions (withheld under two pairs); `head_to_head` gives categorical duels; both are read back into resumed prompts. (D369, D400)
- **Operator notes.** Advisory, labelled HUMAN GUIDANCE, persisted as `human_note`; `drain_guidance` consumes them for every role, `reload_notes` shows earlier ones. (D388, D398, D403)

## The IR and the evaluator ABI

- **General hardware, general IR.** Memory, compute, interconnect and control, not only DNN accelerators. (D1)
- **Thin adapters.** Tools run as subprocesses, backends load lazily; Flux's own code is about 0.1% of wall time, so no native rewrite. The registry holds zigzag, timeloop, rtl, openroad and champsim. (D33, D90, D540, D637)
- **The batch contract.** `evaluate_batch` returns one `Result` per candidate, in order; `ArchRef` `None` means the default or `NotExpressibleError`. (D166, D173)
- **Missing metrics are named refusals.** `Result.metric` returns a `MetricOutcome`; indexing a missing metric raises `MissingMetricError`. (D169, D201)
- **Shared parsing.** `flux_ir.validate` reports up to ten violations with paths; `parse_einsum` once; `MacArrayHarness` for the mac-array check. (D187, D467)
- **Timeloop translation.** One spatial dimension honoured; multi-op workloads as one run per op; 2-D arrays accepted, 3-D refused; sparsity via Timeloop's own densities. (D24, D62, D78, D215)
- **Trustworthy Timeloop energy.** Energy from a dummy Accelergy plug-in is refused. (D138)
- **Hermetic Timeloop is opt-in.** Docker by default; `use_local`/`FLUX_TIMELOOP_LOCAL` for the hermetic runner, never a silent fallback; a golden file pins both runners' equivalence. (D141, D150, D161, D205, D206, D208, D215)
- **Benchmark workloads.** mlp-ffn0 and the ragged mlp-gemm-ragged-v1 (K=100). (D59, D60, D137)

## The applications

The applications are the directories of `flux/applications/`. Worlds (bankmap, interconnect_mapping, macarray, nlu) share the loop's helpers (`flux_loop.params.from_params`, the pool, the harness reply helpers, `missing_tools`) rather than copying them. Every application is re-run after loop changes and the docs quote those runs. (D557, D610)

- **gelu_fp16, the showcase.** FP16 GELU within 1 ULP as a formula; a coding agent writes the prototype from a method note. (D619)
- **npu_gemm, the accelerator example.** `render.py` writes Architecture IR from `pe_x` and `gbuf_kb`, ZigZag measures, no model. Decision: 32 PEs, 16 KB, 341 cycles (goal 500); 64 PEs add area without speed. (D625)
- **The small examples.** adder16, mul8 and primes (non-hardware, the README's first model example); `flux new NAME --kind ...` scaffolds from templates. (D598, D610)

### nlu

- **The rig owns correctness.** Reference, a vector floor and a 1-ULP gate proven over all 65,536 inputs; the model designs the FP16 operators. (D408)
- **All seven operators are flow-made.** Admitted from the model's own prototypes; reloads re-spell them with today's transpiler. (D484, D485, D486, D495)
- **Tools.** A `Mined` source over the run's own record, and instrument tools (`error_map`, `compare`, quantisation) over the FP16 domain. (D529, D530)
- **Headline.** Routed: 692 MHz at 5,592 um2 (788 MHz placed); routing costs about 12% of clock. (D538)
- **No block sharing across parts.** Measured larger and slower. (D572)

### prefetcher

- **The model writes the .ini.** It chooses Bingo's partners and every knob it changes; there is no space, seeds or world. `knobs.md` is the reference (meaning, range, legality, storage model); `bingo.py check` refuses an illegal file before simulation and `bingo.py measure` fills unstated knobs from `bingo_default.ini`. (D349, D637)
- **Two fidelities.** Screen at 10M+15M instructions (a 2M+3M screen's spread exceeded the effects), confirm finalists at 100M+150M. (D351, D353)
- **Objectives.** `geomean_speedup` with `keep: 0.9, above: 1.0`, then `storage_bytes` (Bingo's tables); a storage budget is a gate option (`bingo.py check --max-storage`). (D362, D637)
- **Invention.** `invent.problem.yaml`: the model writes a C++ L2 prefetcher, gated by `flux champsim build/check`, measured beside Bingo's shipped configuration. (D637)
- **ChampSim is Flux's.** `flux champsim run|build|check` (`evaluator/champsim`) measures an ini or header on every trace, caches the no-prefetcher baseline and header builds by content; it has no Bingo code. (D637)

### macarray

- **What it searches.** Multiplier x reducer x pipeline; no mapping axis (ABC maps against the clock). Verilator verifies, synthesis + STA screen, OpenROAD places finalists. (D365, D571)
- **Area mode.** `preserve_fmax` holds the incumbent's clock within 1% and minimises area. (D366)
- **Invention.** After the screen, with each built-in multiplier's critical path and area in the prompt; a refused round's best attempt is repaired next round. (D370, D551)
- **A document.** `macarray.problem.yaml` + `flux_macarray.world.World`. (D533)

### bankmap

- **Solver ladder.** Exhaustive checker, pigeonhole proofs, z3 CEGIS over XOR folds, then a model round. (D356)
- **Interconnects as stage lists.** Crossbar, staged, omega, butterfly, Clos and Benes as `Stages` plus stated routing assumptions; proofs include clique, bounded SAT colouring and stride compatibility. (D358, D363, D364)
- **Joint wiring.** Lane-to-crossbar wiring solved jointly with the XOR fold. (D372)
- **Record.** A resumed run seeds its model round with past refusals and reads operator notes. (D402)

### interconnect_mapping

- **Evaluation.** Bank hashes against 12 tensor storage modes; injectivity decided exactly; train/holdout split; certificates by exhaustion. (D378)
- **Design points.** Map policy x fabric over twelve fabric families; latency per access under step barriers; traffic at peak pressure so every axis discriminates. (D379, D380, D381)
- **Physical stage.** Element-level Yosys + STA; the full crossbar fails timing at 600 MHz. (D383)
- **Self-checks.** The app's own advice is tested as candidates (read/write staggering was refuted); `simulate.py` cross-checks the analytic cycle law. (D382, D394)
- **Loop shape.** Mapping and fabric-fitting little loops under a coordinating big loop. (D386, D392)
- **Conclusion and report.** Corner designs, a knee pick and proved tile families; the report opens with the answer. (D385, D393)

## The command line, the TUI, docs, packaging and tests

- **Tools via adapters and Nix.** No third-party tool is copied in or built by the repo except ICSC; tools come from the `nixchip` input, whose substituter `flake.nix` repeats (an input flake's `nixConfig` is ignored). (D21, D315, D348, D636)
- **Tree layout.** The four module types plus `applications`, `core`, `interfaces`; role packages import no application; single-use evaluators live with their application. (D296, D428)
- **Install and scaffold.** `pip install -e ./flux`; `flux task check` infers what `flux rtl` needs; `flux new --kind` includes tune and rtl-sweep. (D598, D600, D608)
- **`flux selftest`.** Tools, a sweep, an rtl-sweep, the model server, a model-written problem and a coding agent, each PASS/FAIL/SKIP (`--full` adds adder16); exits 1 on a failure. (D623)
- **CLI behaviour.** Real exit codes, one-line errors, missing tools announced; clear messages for misspelled keys, unknown placeholders, broken YAML and unknown policies (naming `llm`). `flux ask --tui` opens a setup form; authored documents go to operator review. (D587, D590, D627)
- **Where things live.** `flux_loop/document.py` (document and contract), `task.py` (`PromptProblem`); callers use `flux_evaluator_abi` and `flux_loop.report` directly. (D558, D612)
- **Comments say what the code does.** The non-obvious why, then a `(Dnnn)` pointer; no stories, dates or quotes. `CONTRIBUTING.md` explains D-numbers. (D97, D186, D612, D620)
- **One home per doc topic.** `flux/README.md` is the developer map, `docs/usage-guide.md` the command reference; docs say what the code does and carry no hand-kept counts (parity tests do). (D195, D196, D246, D620, D632)
- **Docs for newcomers.** `docs/tutorial.md` builds a new problem end to end (isqrt, in `docs/tutorial/isqrt/`); `docs/models.md` covers model settings; the README's Safety section says documents and agents execute code as the user. (D531, D590, D601, D624)
- **Public site.** MkDocs Material under `website/`, built `--strict` by `pages.yml`; the loop crafter emits a Python skeleton. (D387, D395, D396)
- **TUI foundation.** `flux_tui`: stdlib curses on an event bus, a reusable `Pane`; `demo_run` is the one run path. (D390, D404, D532)
- **TUI keys.** A resize never ends a run; the prompt line is feedback; `r` loops passes; PgUp/PgDn scroll a screen; `f` feedback, `t` thinking, tab 9 info. The first `q` stops at the end of the pass, a second abandons now. (D390, D392, D393, D406, D409, D574, D621)
- **Task and timing tabs.** Tab 1 is the current task, tab 2 a foldable phase tree with per-call costs; each tool round is `llm: round`. (D391, D418, D546, D547)
- **Rows carry their content.** Knowledge, gate, propose, critique, transpile and measure rows show what the step saw and decided. (D418, D490, D492)
- **Results tab.** The objective, what is happening now, the whole design, each part's constraint, and the front. (D497, D498, D532, D573)
- **CI shape.** Per push: ruff, the unit core and conformance. Nightly and on dispatch: the heavy tests, the integration sweep, a physical OpenROAD job and `timeloop-hermetic` (both fail on skips). A no-Nix job pip-installs and runs `flux selftest --no-model`. (D155, D207, D230, D371, D590, D600, D626, D638)
- **CI mechanics.** A shared `nix-setup` action caches `/nix` per shell with `run_id` keys; scheduled runs are never cancelled; `test_ci_workflow.py` asserts this. (D209, D242, D246, D247)
- **Test hygiene gates.** Every discovery test asserts a floor. Unit tests check corpus holdout, data-file paths, `parents[N]` paths, `localSrcDirs` parity with the packages, `TYPE_CHECKING` imports, and every relative link and anchor in the Markdown (`test_doc_links.py`). Undefined names are ruff's F rules. (D123, D195, D198, D320, D334, D335, D619, D639)
- **Test environment.** `conftest.py` puts `localSrcDirs` on `sys.path`; `FLUX_TMPDIR` (default: the user cache) becomes `TMPDIR`. (D123, D343, D405)
- **Fast core by default.** Real-tool and whole-study files, and single tests over 25 s (`HEAVY_TESTS` in `conftest.py`), are `heavy`. A golden campaign test pins the loop; `test_loop_live.py` runs each world's document and fails on one that does not load. (D374, D531, D559, D628, D639)

## Since the fold

New decisions go here, one short entry each: a bold lead naming the decision, then its key reason,
number or rule and how it was verified -- no narrative. From time to time they are folded into
the topics above.

- **D640: every delegable box has its agent half.** One contract (`flux_loop.boxes.box_turn`): a
  fresh turn in `agents/<box>/NNN/`, `out.json` checked, one retry with the reason, then the rules
  half. Orchestrate picks must be on the menu; dse points inside the space and new; extract lessons
  must cite existing record rows; plans pass `check_plan`. Verified: 14 tests with a fake agent
  (answer, retry, three fallbacks, each box end to end, the report's table); unit core 1,223 passed.


- **D641: concurrency tests count overlap, not time.** `test_parallel_parts` and `test_pool` failed
  under the 64-worker core (a peak of 3 for 4; a parallel run slower than a serial one): they now
  assert the drafts or builds overlapped (peak >= 2), which holds on a loaded machine.

- **D642: an agent's brief names the problem's files.** Live on adder16 (OpenCode, hosted qwen3.6):
  the critic read another problem's `gen.py` and objected about a popcount (225 s); with
  `THE PROBLEM'S FILES are in <home> ... nothing outside them` it answered in 36 s with no false
  objection. The validate question now states the loop's conventions (`{home}` is filled,
  `deepest` is the last stage, no parts is one design, `finalists: 0` stops at the first stage).
  The dse agent covered 11 of 12 points in 3 rounds and reached the sweep's decision,
  carry_select-4 at 3,287 MHz.

- **D644: the site gives steps, and the crafter is a form.** README and site pages are numbered
  steps with copy-paste commands, a capability list and the loop's 15 boxes with their halves. The
  loop crafter is a problem builder: presets, then what/check/measure/goal, the fixed loop diagram
  (click a box: only its allowed halves), advanced budget/space/parts, a live `problem.yaml` with
  a checklist. `crafter.js` `buildYaml`/`check` are pure; `test_loop_crafter.py` loads every
  preset's YAML with `load_task` (11 cases). `mkdocs build --strict` passes.

- **D643: with the prototype off, the coding agent writes the target.** A golden model gives a
  document a prototype stage; `prototype: false` still routed drafts to the prototype path, where
  the stage is skipped and the loop's MODEL wrote the RTL -- the coding agent was never called.
  The route now requires `state.request.prototype`. Live (isqrt, hosted qwen3.6 loop, Claude Code
  generating, OpenCode validate/critique/orchestrate): before, qwen drafts stuck at 1-203 failing
  vectors and nothing was admitted; after, Claude's draft #2 passed the gate after one repair.

- **D645: ICSC is its own shell.** ICSC builds from source (~10 min) on the first `nix develop`,
  a cost every newcomer paid for one optional translator; it moves to `nix develop .#systemc` (the
  default shell plus `icsc-sv`). In the default shell a SystemC prototype's RTL is written by the
  model (D635). Checked: the default shell has no `icsc-sv`, `.#systemc` has it.
- **D646: the critic and the validator object only to defects.** Live (D643's run): the critic
  objected to a code comment (229 s), the validator to `finalists: 0`, `--clock-ps` and a
  goal-less objective. The questions now state what is not a defect (comments, style, depth
  claims) and the loop's conventions (`steps`, `--clock-ps` vs `fmax_mhz`, goal-less objectives).
  Rerun: the critic made no cosmetic objection (one turn fell back on a malformed out.json, as
  designed); the validator, on hosted qwen3.6 via OpenCode, still objects to `finalists: 0` and
  `steps` although the brief states them -- validate stays advisory. Claude's isqrt: 1,099.8 MHz /
  35.5 um2 (2 passes, #4) and 1,207 MHz / 36.6 um2 (first draft admitted).
- **D647: the prefetcher document on a real model.** Hosted qwen3.6 wrote the knob file itself
  (bingo + sms + ampm at shipped settings), the gate admitted it, ChampSim measured geomean
  1.0914 at 35,096 B on short stages (100k + 1M instructions, 3 traces).
- **D648: the library reaches every document.** Only the NLU world read the papers; a plain
  document reached them only through the `knowledge` tool. Now every document's mentor leads with
  `Library` (a few short BM25 lookups from the statement, contract and parts, interface words
  dropped) and `Papers` (one line per paper, the digest's first line when stored), unless
  `flow: {knowledge: none}` or the library is empty. The window share bounds them (D548).
  `knowledge: {library: dir}` adds a document's own folder; `FLUX_LIBRARY` moves the shared one
  (unit tests point it at an empty folder). Coding-agent briefs (generate, prototype, the boxes)
  carry a LIBRARY section: the index and the absolute paths nearest the question. Each draft's
  row names the files its prompt cited (`provenance.library`); `flux report` and `task check`
  say it.

- **D649: the crafter asks for a kind of problem and a goal sentence.** Kits fix the check, the
  stages and the metric vocabulary: RTL on ASAP7 (golden model; synth/place/route at a target
  MHz, clock-ps derived), program speed, Python function, ChampSim (.ini or C++ header), ZigZag
  accelerator, or "my own tool". Goals are sentences over the kit's metrics ("reach N, then the
  smallest", "fastest, then the smallest within 90%", "the knee"); the user types at most one
  number. `keep` is offered only where bigger is better. 48 crafter tests load every kit x
  sentence through `load_task`.

- **D650: an excerpt is a passage, not a heading.** Library chunks with fewer than eight distinct
  words (headings, captions, table rows) are no longer excerpts: the isqrt run's prompt carried
  "Root Mean Square error" as one. Tested with a heading-only chunk.

- **D651: model settings once per machine.** Every `flux` command reads `~/.config/flux/flux.env`
  (`FLUX_CONFIG` moves it): `FLUX_*`/`OLLAMA_*` lines, the shell winning. `FLUX_REMOTE_API_KEY_FILE`
  names a file holding the key, so the key is in neither the env file nor the shell. The repo
  documents it with placeholders only; a machine's own server lives in its own file.

- **D652: a gate is a sequence of named checks.** `gate: [{name, run, count_re?, fail_re?,
  timeout_s?}, ...]` runs the checks in order; the first reporting failures refuses the design
  ("failed at <name>: <its report>", which the repair prompt carries) and the rest do not run.
  Exit 3 from any check is "did not build". The score is the failures plus 1,000,000 per check
  not reached, so a design stopped at lint ranks worse than one stopped at golden and the
  gradient and revert keep meaning something; progress text says "N (with k later checks not
  reached)". `gate: <command>` is one check `test`, `{build, test}` the checks `build` (any
  non-zero exit: did not build) and `test`: one structure, the old spellings kept as the simple
  case. build() runs the whole sequence once; fast_check reads it. A stage's gate is its
  `cutoff` ("go on only if"): no new syntax; "timing met" is `{metric: fmax_mhz, at: <MHz>}`.

- **D653: `flux rtl lint`.** Verilator `--lint-only -Wall`, counting only the warnings that are
  hardware defects the golden vectors can miss: LATCH, MULTIDRIVEN, UNOPTFLAT (combinational
  loop; a false one across a vector's bits is split bit by bit), COMBDLY (`<=` in combinational
  logic), BLKANDNBLK (a race), IMPLICIT (a typo made into a 1-bit net). Width, unused and naming
  are style. Prints each as `CODE: line N: ...`, then `N failing`; exit 3 when it does not parse.
  Milliseconds, so it goes before the golden test.

- **D654: one tool catalog.** `flux_loop.toolbox.TOOLS` lists every check and stage a document
  can name (id, role, title, what, run template with named params, params with label/default/unit,
  metrics with units from `objective.UNITS`, needs, pass rule, languages, crafter kinds).
  `flux tools` prints it, `flux tools --json` is `website/docs/assets/tools.json`, and a unit test
  keeps the two equal so the crafter never drifts from Flux.

- **D655: the crafter builds checks and measurements from the tool catalog.** Two ordered lists
  picked from `tools.json` (= `flux tools --json`): checks, each with its pass rule, and
  measurements, each with an optional "go on only if" gate written as its `cutoff`. Kits pre-fill
  both; goal sentences come from the metrics every chosen measurement reports. A 3-check,
  3-stage hand-made case loads with its order and cutoffs; `check()` flags a cutoff on a metric
  its stage does not report. The page's update loop called a deleted helper -- found by driving
  it in headless Chromium, fixed.

- **D656: ICSC comes from nixchip.** nixchip `f4fddde` (Sep 28) exports `icsc` (LLVM 18.1.8,
  cached on nixchip0.cachix.org); this repo builds nothing for it. `nix develop .#systemc` adds it
  and sets `ICSC_HOME`; `systemc_proto.translate` compiles ICSC's "unity" file against its
  libSCTool itself (the cmake `svc_target` recipe), so no wrapper script. The pin moves Verilator,
  Yosys and OpenROAD to their 2026-09-15 builds, all cached. Checked: both shells build from the
  cache, the ICSC translation passes `flux rtl test`, the unit core and the heavy suite pass
  (268 passed).

- **D657: a stage may have several gates.** `cutoff:` is one condition or a list of them
  (`[{metric: fmax_mhz, at: 1000}, {metric: area_um2, below: 80}]`), applied in order, all must
  pass; the cut says which condition cut which designs ("fmax_mhz below 1000 (a); area_um2 above
  80 (b)"). The single dict stays the simple case and round-trips as a dict. Each condition's
  metric must be one its stage measures; `flux task check` prints every gate.

- **D658: every goal is a limit; the goal-less objectives decide, or balance.** Before, only
  the first objective's goal counted. Now each objective with a `goal` (or `keep`, resolved over
  the pool) must hold; among designs meeting every limit the goal-less ones decide in written
  order, each next one breaking the ties of those before; `balance: true` ones decide as their
  knee. Nothing meets every limit: fewest missed, then the smallest relative shortfall, said in
  `decided_by`. A goal-less vector without `balance` is now an order, not a knee, so
  interconnect_mapping says `balance: true` on its four. Checked: every shipped document's
  objectives, old rule vs new, over 3000 random pools each: the same picks, except the NLU's
  when two designs tie exactly on area (power now breaks the tie). `describe()`: "fmax_mhz at
  least 1000, area_um2 at most 80, then least power_w".

- **D659: the problem builder starts empty.** No presets or kinds of problem: add checks (type
  Lint / Compile / Golden model / Test script / Custom, then that type's settings, per language),
  add measurements (a tool, its settings, one or more "go on only if" gates), and an Objective row
  per reported metric (at least / at most / maximise / minimise / balance). Every type maps to a
  catalog command; a language without one says so. Tested from scratch through `load_task`.

- **D660: every display names every limit.** `flux task check`, the default standing the
  orchestrator and agents read, and the DSE prompt now give the whole objective in words ("fmax_mhz
  at least 1000, area_um2 at most 60, then ..."); the report's frontier plot draws each axis's
  limit, labelled with its rule. Part budgets (ladder) follow the first limit, else the first
  objective. Tested: the three texts name the second limit; NLU/macarray/golden heavy tests pass.
- **D661: `flux prog time|count|size` measure a program.** Each takes `--build` (one quoted
  command writing `{out}` in a fresh directory, so parallel candidates never share a binary) and
  `--run` (default: the built program), and prints `name=value`: hyperfine's mean, spread and
  fastest (a Python loop without it); cachegrind's instructions, D1/LL misses and branch
  mispredicts (deterministic: the cheap, noise-free proxy for time); `size`'s text/data/bss. A
  failed build exits 3, as `flux rtl test`. hyperfine and valgrind join the default shell (cached,
  small). Catalog: prog-size, prog-time, prog-count; `task check` infers `needs` (valgrind, size).
  Live: a 4M-int sum x8 at -O2: 20.1 ms +- 1.3; 108,181,679 instructions, 788k D1 misses, every run.
- **D662: `flux rtl measure --stage stat` is Yosys alone.** The synth stage's mapping and
  `stat -liberty`, no OpenSTA: `area_um2`, `cell_count` (the same numbers synth gives) in under a
  second, needing only yosys. Catalog `rtl-stat`, the cheapest RTL screen; its default metrics
  are the two it prints.
- **D663: evaluator stages in the catalog.** A catalog entry may carry `stage` (the stage's keys:
  `{"evaluator": "zigzag"}`) and `document` (top-level keys: `{"workload": "{workload}"}`)
  instead of `run`; `flux tools` prints both. `zigzag-eval` (latency_cycles, energy_pj) and
  `timeloop-eval` (+ area_mm2; needs timeloop-mapper, the .#timeloop shell). A string `workload:`
  is now read beside the document (`{home}/w.yaml` or `w.yaml`), not from the cwd.

- **D664: the builder is dense and shows every catalog tool.** Two columns on wide screens (form,
  sticky YAML and checklist); one line per check, measurement and objective row, secondary
  settings behind "more"; the diagram abbreviates long lists ("screen → +4") with the full list in
  a tooltip. Scroll length at 1400 px: 4,666 → 2,368 px. The RTL clock is "the clock the tools aim
  for", defaulting to an fmax "at least" limit. Evaluator tools write their stage object and the
  top-level `workload`; an objective metric some measurement does not report is an error, as the
  loader refuses it.
- **D665: a stage's estimate is a pre-gate before its tool, off by default.** `stages[].estimate:
  {kind: surrogate|command|model, margin: 0.05}` replaces `flow.analytical`/`flow.simulation`,
  which were labels (the stage order was already `stages:`, `simulation` was read by nothing, and
  the flow could hide stages that ran). Before the tool runs on a design the estimator predicts
  the stage's metrics; one that fails the stage's cutoff or an objective's limit at that stage
  by more than `margin` of the threshold is refused for the stage ("estimated cost 40 fails
  cost <= 35 (the cutoff) by more than 5%") and never measured; no estimate means the tool runs.
  `surrogate` fits the record's rows on that stage (nothing under 3 rows), `command` is a script
  printing the same `name=value` metrics, `model` is one model turn per batch. A skipped design's
  row carries its estimate; a measured one carries it in its provenance beside the number; the
  report counts both per stage. The surrogate's old job, ordering the finalists by a prediction
  (D560), is removed: an estimate may only save a tool run, never choose what climbs. Modelled
  stages are the world's `analytic_stages` (bankmap, interconnect_mapping); macarray's screen is
  a synthesis run and is no longer tagged modelled.
- **D666: the audit's loop fixes.** `dse: pareto` (or a pareto phase) with fewer than two
  objectives is refused at load. `feedback: none` is no channel: the loop drops the feed, reloads
  no earlier notes, and `run_passes(notes=False)` never wakes on one. A coding-agent plan runs
  without a model proposer. `budget.finalists` holds with one objective: the best N by it climb.
  `describe_flow` says what each default and agent does: orchestrate "default (the model picks
  the next part, ...; rules pick the kind of work: ...)" or "(one design, no part to pick; ...)",
  "agent X (a coding agent ...)" vs "agent (the model with tools ...)", "extract: agent X
  (lessons from the record's rows, each citing its rows)", the library "on by default";
  `records` is not a flow key (always on).

- **D667: the builder draws what the loop does.** Audit first (57 choices run for real): the
  diagram had two estimate/measure boxes that were labels, a critic drawn in one place that acts
  in three, an orchestrate default it described wrongly, and settable steps that were fixed.
  Now: one Measure box with a per-measurement "estimate first"; red dotted rejection paths
  (repair, sent back, improve, dropped); the critic at the division, each part and the decision;
  a "sub-loops, composed" node when there are parts; fixed steps greyed and locked. Every
  default's popover text is `flux task check`'s own parenthesis, tested against describe_flow.

- **D668: a running agent shows in the TUI.** An agent turn is its own task row ("agent: <tool>")
  updated once a second while it runs: elapsed time, the tools it called, the tail of its words
  -- read from OpenCode's JSON events and Claude Code's `stream-json` (the claude preset moves to
  it; the answer is the stream's `result` event). Live with OpenCode on the hosted model: the row
  showed 1, 2, 3, 4 tool calls at 22 s, 23 s, 33 s, 34 s, not only at the end.

- **D669: one agent session per job.** A generate agent keeps ONE session per part until the part
  is admitted: the first draft reads the whole brief in `agents/generate/<part>/`; a gate repair
  or a critic's send-back resumes that session with a short message (what failed, the file, fix
  it), the file kept at one path so the brief's gate command stays true. Admission drops the
  session; an improve is a new agent (`<part>-2/`) whose session ends with it. The prototype
  agent's session spans the part's prototype stage until the prototype passes. An agent that
  cannot resume (codex, a session gone or out of context) gets the full brief with the prior
  draft, as before. A decision box takes `session: turn` (fresh each turn, the default) or
  `session: pass` (one session per box for the pass in `agents/<box>/pass/`, `out-NNN.json` per
  turn, later turns resumed with the new question); `session` on generate is a load error, its
  span is fixed. Every agent turn is an `agent_turn` row with `session: fresh|resumed` + id (the
  report's Agent turns table has the column; the transcript too); rows drafted on worker threads
  are queued and written by the loop's thread. A send-back from an earlier step now reaches a
  template/agent source too (it read no `state.best` before). Live, OpenCode on the hosted model:
  the gate refused the first draft (a closing line only its failure names); the repair resumed
  `ses_f0ea11c0...` with 427 chars against the brief's 11,854 and the part was admitted; a
  `session: pass` critic answered four turns (the division, lo, hi, the decision) in one session,
  5,940 chars then 1,223, 1,223, 863.
- **D669 (addendum): agents get the key.** OpenCode reads `{env:FLUX_REMOTE_API_KEY}`; when only
  `FLUX_REMOTE_API_KEY_FILE` is set, Flux loads the key into its own environment (memory only), so
  agents it starts are not refused with HTTP 401.

- **D670: a preset's executable and extra arguments.** `bin` / `FLUX_<PRESET>_BIN` renames only
  the executable (an installed name or a path; not a shell alias), `args` / `FLUX_<PRESET>_ARGS`
  adds arguments before the prompt and before the answer on resume, e.g. OpenCode's `--agent
  flux`. The document's wins over the machine's. Live: OpenCode run as `oc-alt --agent build`.

- **D671: size limits on what reaches an agent or a prompt.** Linux refuses one argument over
  128 KiB (E2BIG): a `{prompt}` or `{answer}` over 100 KB is written to a file in the agent's work
  directory and the argument asks it to read that file (short prompts stay inline). A mined
  refusal fact quotes the first line of its message (160 chars) and groups on it -- raw agent
  output and compiler logs had been quoted whole, each tail its own fact; the full messages stay
  in the evidence by their start and end. The mined block carries 12 facts and 3,000 chars.

- **D672: the brief on stdin.** The presets (OpenCode, Claude Code, Codex `exec -`) take the
  prompt, and a resume's message, on stdin; no argument carries it, so no size hits the 128 KiB
  argument limit. A custom `command` naming `{prompt}`, `{prompt_file}` or `{answer}` still gets
  it there (the D671 file for a long `{prompt}`); naming none, it reads stdin. Stdin is written
  by its own thread and closed, beside the streaming readers; with nothing to send it is closed
  at once. Extra `args` go at the end (before codex's `-`). Live: OpenCode read a 132 KB brief
  on stdin and its resumed session recalled the code word in it.

- **D673: the agent writes, the loop runs.** Agents compiled and ran tests on their own drafts
  (D595 had put the gate command in the brief). Now the brief says: do not compile, lint,
  simulate, synthesize, test or run; write the file and end the turn. The loop runs the gate and
  the stages, and a refusal resumes the part's session with the exact output ("THE LOOP RAN
  YOUR DRAFT AND REFUSED IT: ..."). The same holds for the prototype: the loop runs
  `flux rtl proto`. The presets enforce it: Claude Code runs with `--disallowedTools Bash`, and
  OpenCode gets `permission.bash: deny` merged into `OPENCODE_CONFIG_CONTENT` (the machine's
  own keys kept). Codex has only the brief. Live: OpenCode on add8 made one `write` call and
  no shell call, and the loop gated and measured the draft (4,555 MHz, 3.1 µm²). With a golden
  that disagreed with the contract, the loop's refusal came back into the same session as
  failing vectors.

- **D674: a deny list, not no shell.** The shell has legitimate uses: reading PDFs from the
  library (`pdftotext`, which the LIBRARY section names), scratch calculation (fitting
  coefficients, error bounds), searching. Denied: the commands that compile, simulate,
  synthesize or test (`DENIED`: verilator, iverilog, vvp, yosys, openroad, sta, klayout,
  champsim, timeloop, the C compilers, make, cmake, ninja, pytest, `flux rtl`/`task`/`run`), and
  `bash`/`sh`, because `bash -c "yosys ..."` got past the Claude list. Claude Code: `acceptEdits`
  alone had never let `-p` run a shell command (each needs an approval nobody gives), so it now
  gets `--allowedTools Bash` plus `Bash(<cmd>:*)` denies. OpenCode: `"*": "allow"` first, then
  `<cmd>` and `<cmd> *` deny. Live, both presets: `python3` ran; `yosys -V`, `bash -c "yosys -V"`
  and `flux rtl --help` were refused.

- **D675: a running agent shows what it does.** The TUI row had shown a tool count, tool names and
  an occasional line of words. Now it shows:
  - each tool call with its command, file or pattern (`1. bash: python3 -c "print(255+255)"`,
    `2. write: add8.sv`), the last eight;
  - the last tool output (400 chars);
  - the thinking tail;
  - the reply tail, in the fields a model's turn uses.

  OpenCode runs with `--thinking`, which adds its reasoning blocks to the JSON events. Claude
  Code runs with `--include-partial-messages`, so the words stream token by token. Its thinking
  arrives redacted (only an estimated size), so the row says "about N tokens". Live, both presets
  sent 11-12 updates in a short turn with tools, output, thinking or its size, and words.
