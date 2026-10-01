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

- **D676: a silent agent says why.** Claude Code ran 300 s with an empty row. Its stream carries
  more than messages: `system` events (init with the model and the Claude Code version, `status:
  requesting`, API retries) and `rate_limit_event`s (`allowed_warning`, or `rejected` with the
  reset time; a rejected limit waits with no message). None were shown, and neither was stderr.
  The row now shows `agent` (model, version), `status` (requesting / responding / a retry;
  OpenCode's model step or error), `rate limit` (unless plain allowed), `stderr` (live), and
  `output` ("none yet after 300s", or "N lines, the last Ks ago"). Live, Claude Code: none for
  5 s, then the model and version, `requesting`, `allowed_warning (seven_day, resets Wed
  15:00)`, `responding`, and the tool call with its output.

- **D677: the agents' workbench, inner-loop knowledge.** `workbench/` beside the document
  (`workbench: path` or `false`), made on the first agent turn with `tools/` and `notes/`, kept
  across runs. It is where, not what: not compared, not in the digest. Every agent (generate,
  prototype, each box) finds it as `workbench/` in its work directory, through a link. OpenCode
  follows the link; Claude Code checks real paths, so it also gets `--add-dir <workbench>`. The
  brief lists the folder, one line per file (its first line, 3,000 chars at most), and asks for
  a note whenever the agent worked something out. The loop never reads it. The `extract` box's
  lessons come from measured results between passes; the workbench holds what agents build
  while they work. Live, Claude Code on isq16 (16-bit integer sqrt): turn 1 built its own ASAP7
  liberty timing model and technology mapper and ran past the 1,800 s limit with no draft; its
  tools stayed. Turn 2's brief listed them. It calibrated them against a real measurement,
  tabulated 11 architectures, left `notes/isq16-status.md` ("TIME LIMIT (read first) ... write
  a checked draft in the first minutes"), and drafted from its own generator: admitted at
  998.9 MHz, 20.5 µm². A Claude turn stopped before its `result` event now keeps its session
  id, which every event carries.
- **D679: the site guides a new user, and the loop page shows the crafter's drawing.** No
  Applications pages (the applications keep their READMEs in the repository); six flat tabs:
  Home, Tutorial, Build your own, Loop crafter, Run a problem, The loop. `guide/run.md` holds
  the run options, stop/resume, the results and the model choice. `guide/loop-shape.md` mounts
  `crafter.js` read-only (`#flux-loop-drawing`: the loop at its defaults, nothing to click), and
  its box table uses the drawing's titles and `flow:` keys;
  `test_the_loop_page_lists_the_crafters_boxes` fails when a box, a key or a choice of the
  crafter is missing from the table. Crafter Advanced: the form's own fields and rows behind one
  toggle, not a Material `details` with a fixed 11rem + 7rem grid (Material's rem is 20-30 px, so
  the unit column collapsed to one letter per line). Widths in em. A new measurement starts with
  the first tool that times (`rtl-synth`), not `rtl-stat`, which reports no fmax.

- **D678: `flux probe`, the loop's tools inside an agent's turn.** Agents kept trying to run
  synthesis: the loop's feedback comes only after the turn ends. The raw tools stay denied.
  - **Probe.** `flux probe gate FILE` and `flux probe measure FILE --stage S` rebuild the
    `PromptProblem` from a context file the loop writes for each turn (`FLUX_PROBE`) and run its
    own build, judge and measure: the same commands, flags and metric parsing. A failed stage
    shows the tool's output tail. The prototype turn's gate is `flux rtl proto`.
  - **Budget.** Per turn: `probe: {gate: 20, stages: 3, <stage>: N}` or `false`.
  - **Record.** Each probe is a line in the turn's log. The loop says them ("probed screen x3,
    gate x2, confirm x1") and puts them on the `agent_turn` row, never as a trial.
  - **Allow.** `allow: [cmds]` gives denied commands back to one agent; `allow: all` lifts the
    deny list, and the brief then does not call anything denied.

  Live, Claude Code on add8: 6 probes in one turn, then admitted at 2.96 µm², 24 cells,
  4,306 MHz. Without probes the same problem had given 3.135 µm² and 32 cells.

  Also from D677, live: the second isq16 run drafted in its first turn, as its note said
  (999.4 MHz).

- **D679: a probe measures stages independently.** Correctness belongs to the gate, and
  measurement to the stages. `flux probe measure FILE --stage S [--stage T ...]` runs only the
  stages named: each on its own budget, each in its own directory, side by side (threads), no
  gate unless `--gate`. Each stage reports its metrics and whether they meet its limits (its
  cutoffs, then each objective's limit at that stage), e.g. `limits at confirm: fmax_mhz >= 2000
  -- met`. The exit code is 0 when all met, 1 for a miss or a failed measurement, 2 when
  refused. Live, add8: synthesis and placement in one probe, 4,306 and 4,190 MHz, both met.

- **D680: the run's sandbox.** `flux task run` and `flux ask` re-launch themselves with
  `docker run`, on by default (`--no-sandbox`, `FLUX_SANDBOX=0`). A container may run any number
  of processes; its first one is PID 1. That is `tini` from nix: Docker's `--init` lives under
  `/sbin`, which is the host's `/usr` here.
  - **Read-only:** `/usr` (merged-usr: `/bin`, `/lib` follow), `/nix/store`, a list of `/etc`
    files (Docker owns `resolv.conf` and `hosts`), the flux source, the cwd, PATH directories
    and their links' targets, the problem folder.
  - **Writable:** the record's folder, `<problem>/out` and `/workbench`, `~/.cache/flux`,
    `--out`/`--json` targets, `flux ask --dir`.
  - **Container settings:** `--read-only`, tmpfs `/tmp`, `--cap-drop ALL`,
    `no-new-privileges`, `--pids-limit`, your uid/gid.
  - **HOME:** a sandbox home kept across runs (agent sessions), with `~/.config/opencode` and
    `~/.opencode` read-only and Claude/OpenCode credentials copied in. Not mounted: `~/.ssh`,
    the Docker socket, `~/.config/flux` (the host reads flux.env and passes settings and key by
    environment). Environment dropped: SSH/GPG agents, display, D-Bus, `*TOKEN*`, `*SECRET*`,
    `AWS_*`, `GITHUB_*`, ...
  - **Network:** the host's (`--network host`: a local Ollama stays reachable). With
    `FLUX_SANDBOX_ALLOW`, `--network none` and an allowlisting CONNECT/HTTP proxy on the host,
    on a Unix socket in `$XDG_RUNTIME_DIR` (a home on sshfs cannot hold a socket). Inside, flux
    relays 127.0.0.1:18080 to it, and HTTP(S)_PROXY points every client there (urllib, Node,
    Bun).
  - **Stopping:** the run state names its container, so `flux status` asks `docker inspect`
    and `flux stop --now` sends `docker kill --signal INT`.

  Live:
  - A hostile gate read nothing from `~/.ssh`, `~/.config/flux` or other repositories, could
    not write the repository, `/usr` or its own document, and found no Docker socket.
  - OpenCode and Claude Code agents ran (Claude Code with probes), and the loop admitted add8
    at 3.135 and 2.96 µm² respectively.
  - Allowlist `localai.cyprien.ch`: OpenCode worked, and `example.com` was refused.
  - `flux stop --now` from the host ended a sandboxed pass with the record intact.

- **D681: the sandbox's cache is the application's.** D680 mounted all of `~/.cache/flux`
  writable, so a run could reach every campaign's traces, agent sessions and caches. Now only
  `~/.cache/flux/apps/<id>/` is writable, shared by that application's runs:
  - `tmp/` is TMPDIR and FLUX_TRACE_ROOT (scratch, traces, agent directories);
  - `home/` is HOME (agent sessions, copied logins);
  - `cache/` is XDG_CACHE_HOME.

  Mount points under HOME are made in advance, as the user, not by Docker as root. Live: a
  gate found no shared trace folder, saw only its own application under `apps/`, and wrote its
  traces to its own `tmp/`.

- **D682: a resumed search goes on from its record.** Only parts were reloaded, so a DSE
  campaign run again started with nothing measured. It walked from the start, got every
  number from the cache, found nothing new and reported "at rest" (adder16 resumed: `sweep:
  12 point(s) of 12`). Now every point measured on earlier passes rejoins `state.scored`
  before the walk (one row per point and stage), and the policy reads `seen` when the walk
  starts:
  - a sweep proposes only unmeasured points; with none left it is at rest;
  - a sampler draws new points (MonteCarlo: 6 new each pass, none repeated);
  - a gradient starts from the record's incumbent;
  - a model search sees the whole history;
  - the decision is over the whole record.

  A point measured again replaces its recalled row.

- **D682: rootless Podman, preferred over Docker.** Docker's daemon is root: the `docker`
  group is root on the machine. Rootless Podman has no daemon, and a container is one of your
  own processes. `FLUX_SANDBOX_ENGINE=podman|docker`; the default is Podman when installed.
  - **Setup here:** no subuid ranges, so Podman uses a single mapping (root inside is uid 10017
  outside, so files come out yours). It needs no image: `--rootfs` is a bare local directory of
  merged-/usr links and mount points. `--init` gives Podman's own init.
  - **State on local disk:** `--root`/`--runroot` go under `/var/tmp/flux-sandbox-<uid>`
    (`FLUX_SANDBOX_STORAGE`), because the home is sshfs.
  - **Reaching the run:** it records its engine command (`container_cli`), so `flux status` and
    `stop --now` reach it with either engine.
  - **Merged /usr:** a host's `/bin` and `/lib` are links, so they are not mounted (`/usr`
    covers them).
  - **Scratch in the container's `/tmp`.** With TMPDIR on any directory mounted from the host,
    even local ext4, Yosys's `abc -liberty` hung in both engines: yosys-abc waited in `select`,
    Yosys in `pipe_read`. The same script took 0.5 s on the container's tmpfs and on the host.
    Scratch is lost after the run; traces stay in `apps/<id>/tmp`.

  Live, Podman:
  - the digits run; the hostile gate blocked as under Docker (it is root inside, which is
    you outside);
  - the allowlist refused `example.com`;
  - `flux stop --now` from the host;
  - OpenCode on add8, drafted and synthesised inside: 4,554.77 MHz, 3.135 µm².

- **D683: `flux serve`, the web interface.** FastAPI and uvicorn (added to the nix shell); plain
  JavaScript pages, no build.
  - **The journal.** `flux_profile` takes listeners beside the TUI's (`add_listener`, a tee).
    At registration a run attaches a journal that appends every phase event (start, update at
    most 1/s, end, mark, publish; text cut to its last 4,000 chars) to `<run dir>/events.jsonl`.
    Another process follows a run the way the TUI does, sandboxed or not.
  - **Server.** Its SQLite holds users (scrypt), sessions (random tokens stored hashed),
    runs and an audit trail. `X-Flux: 1` is required on every change (CSRF).
  - **Applications.** They live under `users/<name>/apps/<app>/`. Paths are resolved inside the
    application. Zips may not hold links or `..`. At most 500 files and 50 MB.
  - **Runs.** Each is a detached `flux task run --db --json`, forced into the sandbox (no
    `--no-sandbox` on a shared server), and followed by what it writes: the record's run pointer,
    `run.json`, `events.jsonl`, `turns.jsonl`, the log. Server-sent events stream the journal and
    the log.
  - **Sandbox changes.** `FLUX_SANDBOX_APP=<user>-<app>` names the sandbox cache, so two users'
    applications of one id stay apart. `flux task check` is now sandboxed too, because building
    the problem imports its `world:` hooks and its golden model.

  Live, a server on 127.0.0.1 with the Podman sandbox:
  - login, upload of add8, check, and a run started over HTTP;
  - the event stream carried the tree, including the agent's tool calls and thinking, and the
    log stream the refusal and the resumed session;
  - the run was admitted and decided (6,033 MHz, 4.08 µm²);
  - the results, answer, turns and report endpoints answered;
  - pages checked by driving headless Firefox over Marionette.

- **D684: operator notes, files, users' models and the admin's view.**
  - **Inbox.** A run started by `flux serve` gets `FLUX_FEEDBACK_INBOX=<app>/runs/<n>.inbox.jsonl`,
    inside the sandbox's writable `runs/`. `InboxChannel` drains lines appended since the run
    began and joins the terminal channel (`Joined`). A note from the page reaches the next
    prompt and is recorded like a typed one. An agent's `questions: operator` question is now a
    journal mark, `question`, which the page turns into a banner with the time left; the answer
    is the next note. Only the run's owner steers.
  - **Files.** `POST /apps/{name}/files` adds files, or a zip, to an existing application under
    an optional folder, with the same checks as an upload. A new document means a new id.
  - **Users' models.** Settings are FLUX_REMOTE_BASE_URL/MODEL, FLUX_LLM_MODEL,
    OLLAMA_BASE_URL, FLUX_LLM_TIMEOUT_S, and the keys FLUX_REMOTE_API_KEY, OPENROUTER_API_KEY,
    ANTHROPIC_API_KEY and OPENAI_API_KEY. Keys are Fernet-encrypted under
    `<data>/secret.key` (0600) and never returned. A run's environment is the server's, with the
    user's settings over it. `FLUX_CONFIG=/dev/null`, so a run never re-reads the server's
    flux.env. With their own endpoint, the server's FLUX_REMOTE_API_KEY(_FILE) and
    OPENROUTER_API_KEY are dropped, so the server's key never goes to a user's URL.
  - **Admins.** Admins read any user's applications (`?owner=`, read only) and see every
    application (`/api/admin/apps`) with what is running.

  Live, over HTTP with the Podman sandbox:
  - my endpoint and key set through the API (the key never printed);
  - a file added to add8;
  - a run started and a note sent: inside the sandbox, "feedback noted from cedric: …";
  - the account and admin pages checked in headless Firefox.

  Ideas for later: `flux/interfaces/web/IDEAS.md`.

- **D685: agents may run Icarus.** `iverilog` and `vvp`, now in the shell from nixchip, left the
  deny list: an agent can simulate its own draft. Verilator, Yosys, OpenROAD, OpenSTA, and
  `flux rtl` and `flux task` stay the loop's to run.

- **D686: the configurator, the crafter reading a document back.** `fromDoc(raw, normal)` in
  crafter.js is `buildYaml`'s inverse.
  - **Inputs.** `raw` is the document as written (`yaml.safe_load`); `normal` is the loader's
    form (`TaskSpec.to_dict`: gate and stages as lists, argv, `flux` spelled
    `{python} -W ignore -m flux_cli.main`). Neither runs the document's code: `world:` and
    `hooks:` are imported only when a problem is built.
  - **Matching.** A check's or stage's argv is matched against the catalog's command templates
    (`{param}` = one word), else it becomes a custom row with the command itself.
  - **Kept as written.** What the form cannot say is kept, and each kept key is named. What the
    document wrote decides, not what the loader adds (inferred `metrics_re`, an objective's
    default stage, tie or margin).
  - **Saving.** The form no longer writes the kept keys (`state.kept`). The server appends them
    from the old document; a clash is refused.
  - **Carried in the state:** stage `timeout_s`, units the crafter does not know (`XOR`), and a
    balanced objective's direction.
  - **Web.** `window.FluxCrafter.mount(host, false, {state, save, notes})`. The app serves
    `website/docs/assets` (`FLUX_CRAFTER_ASSETS`) and maps the docs theme's variables onto its
    own. Pages: "New loop" (`#/configure`) and "Configure" (`#/app/<name>/configure`).

  Tests:
  - every document of the repository (the applications, the `flux new` templates, digits)
    read back, written again and reloaded gives the same gate, stages, objectives, flow,
    budget, space, parts and workload;
  - the templates keep nothing aside.

  Kept by the applications: bankmap, interconnect_mapping and macarray keep their world's
  stages; nlu keeps knowledge, stages, objectives, budget, world and ladder; gelu keeps flow;
  npu_gemm and prefetcher keep objectives; invent keeps gate; adder16, mul8 and primes keep
  nothing. Live, in headless Firefox: add8 opened, saved through the button, and reloaded to
  the same loop.

- **D687: loops are per user.** A user sees and uses only their own applications and runs. An
  admin reads anyone's and may stop any run; only the owner writes, runs or sends notes.
  - **Tested.** A second user tried every application and run route of another's: the
    document, a file, the listings, `?owner=`, the run and its turns, results, report and
    notes, stop, starting a run, checking, saving, editing, deleting. Each answered not found
    or refused, and the owner's file was untouched.
  - **Sandbox key.** It was `<user>-<app>`, and both names may hold `-`, so `a-b`/`c` and
    `a`/`b-c` shared one sandbox cache (traces, agent sessions). It is now `<user>.<app>`: `.`
    is in neither name.
  - **No templates in the web app.** "Write the YAML yourself" starts empty; the `flux new`
    templates stay the command line's.

- **D688: the web interface refined.**
  - **No `alert` / `confirm` / `prompt`:** notices (toasts) and modal dialogs. A dialog closes
    with its page.
  - **The log.** It is numbered and highlighted (problems red, warnings amber, decisions
    green). It can follow (scrolling up pauses it), wrap, filter by text or `/regex/`, show
    problems only, and download whole (`/api/runs/{id}/log/raw`). It keeps 50,000 lines and
    shows 4,000.
  - **The live tree.** It follows the deepest running task, an agent first. Finished branches
    fold with their count, except those holding a failure. It can be searched. The detail
    panel shows the task's path. Standings are a card of their own: counts, the frontier with
    the objective's axes, the parts.
  - **Notifications.** `/api/runs` every 10 s: a run that ended, failed or was stopped, and an
    agent's open question, which the run's state now carries (the journal's last `question`
    mark, until a note follows or its time is up). They appear as a toast, in the bell (kept
    in localStorage), and on the desktop when allowed and the tab is hidden. List pages
    redraw their tables when a run changes state.
  - **The agents' workbench** on the application's page (`/api/apps/{name}/workbench`: each
    file, its first line, newest first).
  - **General.** Page heads with actions, cards, relative times with durations, empty states,
    buttons that show they are busy and say their failure, focus rings, narrow screens.
  - **Found live.**
    - Two runs of one application could run at once on one record: now one live run per
      application.
    - Every sandboxed flux has the same pid, so a new run continued the last one's pass
      count: the registration now also compares the container.
    - A run's pass count is shown only when the campaign's registration is that run's.

- **D689: a loop runs or it does not; no run numbers.** An application is a loop with one
  record. Starting it again resumes from that record, as `flux task run` on the same `--db`
  always did.
  - **API.** `/api/apps/{name}/start|stop|state|events|log|log/raw|notes|turns|results|report`,
    and `/api/loops` for the notifications. The `/api/runs/{id}` routes are gone.
  - **The loop's files** (`runs/`): one `loop.log`, each start marked in it ("── started <time>
    by <user> · <passes, screen only, network> ──"); one `answer.json`; one `inbox.jsonl`.
  - **Its view.** The live tree is the latest start's: its journal's `hello` clears the tree.
    Agent turns and results cover the whole loop.
  - **Starts** are kept by the server only for the process, the log and the audit trail;
    nothing shows them as numbers.
  - **State:** running since, or idle / stopped / failed, with the last activity. While it
    runs: its pass, a stop asked, the sandbox, an open question. The list is ordered running
    first, then by last activity.
  - **Pages.** "Loops" lists each with Start/Stop. A loop's page has Start (resume), a dialog
    for passes / until stopped / screen only / network, and tabs: Live, Log, Agent turns,
    Results, Files, Workbench.
  - **Notifications** say "<loop> finished its passes / stopped / failed / its agent asks".

  Live, in headless Firefox: add8 started from the page ("Start (resume)", the dialog), running
  with the new start's tree, one log with its start line; the list showing it running with Stop.

- **D690: a loop's results are its measured designs, accepted or failed by its limits.**
  `flux_web/results.py` reads the record's `ok` trials of a measuring stage, across every
  campaign: not `gate`, `admit` or `prototype`, and not a draft in repair. Per design it keeps
  each stage's latest numbers; `shown` is the deepest stage in the document's order.
  - **Accepted** when every applicable limit holds:
    - the stages' cutoffs on that stage's numbers: `at`, `below`, and `within` judged against
      the best design measured at that stage;
    - each objective's limit at its stage, only for a design that reached it (an objective
      without a stage judges the deepest measured).
  - **Failed** otherwise, with each missed limit said ("fmax_mhz 800 is below the limit 1000
    (confirm)", "… (the screen cutoff)").
  - **The page.** The decision first, then the newest. Filter chips. The limited columns carry
    their limit and ✓/✗. A row opens the misses, every stage's numbers and the design's source
    (`/api/apps/{name}/design`).

  Tested on a written record: accepted, failed by an objective, failed by a cutoff; a gate
  refusal is not listed; a `within` cutoff. Live: add8's two designs, accepted, 5513.6 and
  4242.3 MHz against ≥ 2000.

- **D691: highlighted code and a theme switch.**
  - **The highlighter** (`static/highlight.js`, no library, no build) has a small sticky-regex
    tokenizer per language: YAML (keys, `{placeholders}`), Python, SystemVerilog/Verilog (sized
    literals, `$tasks`, `` `macros ``), VHDL, C/C++, JSON, Markdown, shell, Tcl. The language
    comes from the extension, or from the text for a design's source. It builds text nodes and
    spans, never HTML. Past 400 kB a file stays plain.
  - **The editor** is still a textarea, transparent over a highlighted layer that follows its
    scroll; Tab indents. Prompts and replies are prose with their fenced code blocks
    highlighted.
  - **The theme** cycles system → light → dark in the top bar and is kept in localStorage. It
    is applied before the page draws, from an inline head script. The CSS has light tokens by
    default and dark under `[data-theme="dark"]`, or the system's dark when nothing is chosen.
    Token colours exist for both, and the scrollbars follow the theme.

  Checked in headless Firefox, light and dark: the document editor, a design's SystemVerilog,
  an agent turn, the configurator.

- **D692: the Overview tab, sortable results, a log to navigate.**
  - **Overview** is the loop page's first tab and its link. It shows four figures (state,
    designs measured with accepted and failed, passes on record, the objective), the decision
    card, and a best-so-far chart per objective (up to two). It also shows the agent's open
    question, the latest notes and the newest workbench entries.
  - **The charts** are inline SVG, no library. The x axis is the order of measurement, because
    a loop measures in bursts and time piles the dots up; the two ends carry their dates.
    Measurements are dots (red when they miss the limit), the best so far a step line, the
    limit dashed, the passes faint lines between the measurements they separate. The results
    endpoint now also returns every objective (`objective_list`) and the stage order.
  - **Results** sort by any column: text, verdict, stage in the document's order, numbers,
    time. Missing values go last in either direction; the default is the decision, then the
    newest.
  - **The log.** A menu of the loop's starts (from their "── started … ──" lines) shows one, or
    all. "◀ / ▶ problem" scrolls to the previous or next problem line and flashes it. Start
    markers are drawn as separators.
  - **`test_probe`** proves the side-by-side stages by a handshake: each stage says it started
    and waits (up to 30 s) to see the other; one after the other, the first would report it
    was alone. No timing assumption, and 2.5 s instead of 4.5 s.

  Checked in headless Firefox: add8's Overview (the decision, 12 passes, the two charts),
  Results sorted by fmax, the log showing its second start.

- **D693: the overview follows a running loop, a start checks first, results charts, the
  configurator's diff, the loops list, drag and drop.**
  - **The check is kept against the inputs.** `inputs_digest` is a sha256 of the application's
    files (not `out/`, `runs/`, `workbench/` or its metadata). A check stores its verdict and
    output with the digest; a start stores the digest and its options. `GET preflight` says
    whether the inputs changed since the last start, and whether the check ran on them as they
    are. The start dialog runs the check only when it did not; a failure turns the button into
    "Start anyway". It never blocks: the operator decides.
  - **The start dialog offers the last start's options** (passes, until stopped, screen only,
    allowlist).
  - **The Overview redraws every 10 s while the loop runs** and the tab is shown and visible.
    It skips a beat while a redraw is still loading, and drops one whose tab changed meanwhile.
  - **Pareto front** (inline SVG): x and y metrics, a stage (or each design's deepest). The
    direction of each metric is the objective's, else read from its name (area, power, time,
    count, … lower). The non-dominated designs are joined as a staircase. Accepted and failed
    designs are coloured, the decision is a diamond, the limits are dashed, and a click opens the
    design in the table.
  - **Improvement over time:** `bestChart` per chosen metric, at the objective's stage or one
    chosen.
  - **The configurator previews its save.** `POST document/preview` returns the document as it
    is and as the save would write it (kept keys merged). The page shows an LCS line diff
    (common ends cut first), three lines of context, the rest folded, and saves only on
    confirmation. When nothing changes, it says so and writes nothing.
  - **Loops list:** search (name, document, owner), state chips with counts, order (activity,
    name, accepted designs, has a decision). `/api/apps` adds `summary`: designs, accepted, and
    the decision's number on the first objective with whether it meets the limit. A background
    refresh does not redraw while focus is in the list.
  - **Drag and drop:** `webkitGetAsEntry` walks dropped folders (in batches) and keeps relative
    paths. One dropped folder puts its contents at the top and names the loop. On the Files tab
    a drop uploads at once, into the "into folder" value.

  Tests: preflight across a check and an added file; preview writes nothing; the list's
  summary matches the results. Checked in headless Firefox: the list with its bar and columns,
  the Pareto front and the charts, the start dialog's check, the diff dialog (Cancel wrote
  nothing), a drop on the Files tab. The Overview's redraw while running was not watched live.

- **D694: compare two designs, where the time goes, what the turns cost, streams that
  reconnect, results that scale.**
  - **D693's gap closed.** The Overview redrew while a sweep ran: a marker put on its figures
    was gone 22 s later. A folder drop, fed through the real handler with entries read in
    batches, uploaded `rtl/a.sv`, `rtl/b.sv`, `notes.md` and the document with their paths, and
    named the loop after the folder.
  - **Compare:** tick two designs in Results. The dialog shows every stage's numbers for A and B,
    B − A in value and %, green or red by the metric's direction, and a line diff of the two
    sources.
  - **Timeline** (`GET timeline`): one start (a `hello` in the journal) as bars.
    - Only phases without a child are bars (the work itself). Each goes to the kind of its
      nearest ancestor, itself included: agent, model, gate, `stage <name>`, generation,
      re-verify, knowledge, else the loop.
    - Busy time is the union of a kind's bars, so side-by-side work counts once. Summed is their
      total, and summed over busy is how many ran at once. Passes begin at a top-level
      `propose: decompose`.
    - A phase not ended in the latest start runs to now, if the journal was written within the
      hour. Update and publish lines are skipped, and the parse is cached by the file's size and
      mtime.
  - **Token counts:**
    - A model turn now records `tokens_in` and `tokens_out` summed over every exchange of its tool
      hops; the notes described only the last exchange.
    - `agent.usage()` reads an agent's own report: Claude Code's `result` (usage with cache
      reads, `total_cost_usd`) and OpenCode's `step_finish`, summed per step (input plus cache,
      output plus reasoning, cost). Each agent turn in `turns.jsonl` carries these counts.
    - A zero cost is not a price. Checked on a real OpenCode turn against LocalAI: 10667 in, 22
      out.
  - **Usage** (`GET usage`, `/api/usage`, `/api/admin/usage`): turns, seconds, failed turns, tokens
    in, out and cached, and cost, per loop, per agent or model, and per user. It is cached by the
    file's size and mtime. Turns before D694 count in turns and time, and are said to be
    uncounted.
  - **Streams:**
    - The event id is `<inode>-<byte>`. A new file (a new start's journal) or a shorter one is
      read from 0; the id the browser resends is otherwise honoured, so no line comes twice.
    - `retry: 3000` is sent. The page's `followStream` reopens a stream the browser gave up on
      with its last id, backing off up to 30 s, and shows live or reconnecting.
    - A failed fetch shows a banner until the next answer.
  - **A stopped `flux serve` waited forever for open streams**: uvicorn's graceful shutdown has
    no limit, and a stream never ends. `timeout_graceful_shutdown=3` fixes it: with a stream
    open, the process was still there after 10 s before, and was gone after 4 s after.
  - **Results that scale:**
    - Designs are returned up to 20000, with `total`, and the table draws 200 rows at a time. A
      Pareto click pages in its row.
    - Chart rows are thinned to 3000 (`results.thin`): every row that set a new best on an
      objective at its stage, plus an even share of the rest, in order. `rows_total` says how
      many there were. Before, the last 500 were cut, which broke the best-so-far line.
  - **The admin's loop list** has the designs and best columns too.

  Tests (`test_web_time.py`):
    - parallel tools are busy once and summed twice;
    - an agent under a generation counts as the agent's;
    - a phase that has not ended runs to now;
    - starts are split;
    - usage totals, including turns before D694;
    - Claude and OpenCode reports parsed;
    - thinning keeps each new best;
    - a new server process finds a running loop and stops it.

  Live, in headless Firefox:
    - the timeline of add8's third start (1m51s: re-verify 39%, agent 36%);
    - the compare dialog;
    - the turns' cost;
    - the admin's usage.

  Server stopped under an open Log:

  | When | What |
  |---|---|
  | +3 s | banner and "reconnecting…" |
  | every 3 s | retries |
  | 2 s after the new server | live again |

  A line written after the restart showed once.

- **D695: the admin's view of the machine and the controls, and two loop bugs it showed.**
  - **Admin tabs:**
    - **Loops:** every loop, pause new starts, stop every loop after its pass or now.
    - **Resources:** the machine, the sandbox's containers, every loop's disk, the caches no loop
      owns.
    - **Users:** a running limit per user, and usage.
    - **Audit.**
  - **Containers** come from `podman ps -a --filter label=flux.sandbox=1` and `podman stats
    --no-stream`, with the sandbox's own `--root/--runroot`.
    - A run from `flux serve` now carries `--label flux.app=<user>.<app>`, so a container is
      matched to its loop even after its run died.
    - A container with no running loop is "left behind" and can be killed. The loop's own
      container is refused: stop the loop instead.
    - Only `flux-<hex>` names are accepted.
  - **Disk:**
    - A loop's inputs, `out/`, `runs/`, `workbench/`, and its cache
      `~/.cache/flux/apps/<user>.<app>/`. Sizes are walked and kept for a minute.
    - Caches are classed as a loop's, a deleted loop's (its user still exists), or not the web's
      (a `flux task run` of this machine's user).
    - Clean-ups:
      - `tools`: empties the XDG cache.
      - `scratch`: removes a trace folder's dated pass folders; `events.jsonl`, `turns.jsonl` and
        `run.json` stay, so the Timeline and turns survive.
      - `all`: removes the whole cache.
    - Every clean-up is refused while the loop runs.
  - **Server settings:** a `server` table in `flux-web.db`.
    - `paused`: a start fails with the reason (409), and the start dialog says so before asking.
    - `max_running:<user>` overrides `--max-running`; 0 to 64, and empty means the default.
    - Every control is audited.
  - **Bug: a start obeyed the stop of the start before it.**
    - "Stop now" interrupts the pass, so the stop file (`<run dir>/stop`) was never cleared at
      the pass boundary. The next start stopped at its first boundary, citing a stop from an hour
      earlier.
    - `ops.register` now removes it when a new process (or container) registers. The same
      process registering again keeps it.
  - **Bug: a resumed sweep with every point on record spun.**
    - Its pass ended on "nothing left to do", not a rest, so the next pass began at once: 454
      passes in 30 s at 50% CPU, and a log thousands of lines long.
    - The first bug had hidden it: every restart stopped after one pass.
    - A finished search now turns "nothing left to do" into "at rest: the search measured every
      point". The loop then waits for a note, as a fresh sweep does. The test fails without the
      fix.

  Tests:
  - `test_web_admin.py`:
    - admin only;
    - loop disk and cache kinds;
    - scratch cleaned with the journal kept, tools and all;
    - bad keys and container names refused;
    - pause (409 and preflight), limit (0 refuses, 99 refused, default back), stop every loop,
      and no clean-up of a running loop's cache.
  - The sandbox label, the stale stop, and the resumed sweep resting.

  Live, in headless Firefox:
  - a left-behind `flux.app` container killed from Resources;
  - the sweep's container at 4 to 5% CPU and 52.8 MB;
  - disk totals, the Loops controls and the Users limits;
  - the sweep, restarted, rests after one pass and keeps running.

- **D696: the review of the web pages, round one.**
  - **Loops:** the Document column is gone. Upload moved to an **Upload a loop** dialog beside New
    loop (and on the New loop page). The page is the list alone.
  - **Overview:** redraws once a minute, not every 10 s.
    - The decision card lists the best three designs: the decision first, then accepted before
      failed, the deepest stage reached, then each objective without a limit in turn. The
      limits are already in the verdict.
    - A table classed `top` took the page header's flex style; it is now `best-n`.
  - **Live:**
    - The tree is as tall as the window allows.
    - Following, the detail shows the running task, and at rest the task that ended last,
      instead of "select a task".
    - The detail adds the log's latest 14 lines.
  - **Timeline:** a bar is at least 3 px wide.
  - **Agent turns:**
    - An agent turn now records `about`: the model and tool version. Claude Code says both when
      it starts. OpenCode's events name neither, so it records the model its configuration names
      and `opencode --version`, asked once per process.
    - It also records `tool_calls` and `prompt_chars`.
    - The page shows these, with tokens, cached tokens, cost, session, exit, finish, schema and
      folder, as facts on the turn.
  - **Results:** the charts sit in a section that folds; the viewer's choice is kept in their
    browser.
  - **Configurator files:**
    - `GET inputs` lists the loop's own files (not `out/`, `runs/`, `workbench/`).
    - `DELETE file` removes one; never the document, nor what runs write.
    - Configure and New loop show them to edit, write, drop or delete; a new loop's are sent once
      it is created.
    - A file the document names as `{home}/…` and the loop lacks is listed as missing, one click
      from writing it.
  - **Models by group, for the server and each user:** Flux's own model, OpenCode, Claude Code,
    Codex.
    - The admin's values live in the `server` table (keys encrypted). `run_env` layers the
      machine's environment, then the server's, then the user's, per group.
    - Naming one's own endpoint in a group drops all the server's values of that group, so no
      server key reaches another endpoint.
    - A key set on the web wins over the machine's `FLUX_REMOTE_API_KEY_FILE`.
    - **OpenCode** gets a `flux` provider in `OPENCODE_CONFIG_CONTENT` (merged under the loop's
      own permission rules) and `model: flux/<model>`. Its key is `{env:FLUX_OPENCODE_API_KEY}`,
      never inline. It uses its own settings, else Flux's model's.
    - **Claude Code and Codex** get `--model` through `FLUX_<AGENT>_ARGS`, and their
      endpoint and key in their own variables.
    - With nothing set on the web, a run uses the machine's own configuration, as before.
    - Checked on a real OpenCode turn through the generated provider against LocalAI: it answered
      "hi", 10666 tokens in, 22 out, and recorded `flux/qwen3.6-35b-a3b-apex, opencode 1.18.32`.
  - **The mark:** the chip logo (`.github/logo.svg`) is drawn inline, its graphite following the
    theme. It is the favicon, and appears in the top bar and on the login page. The bell is a line
    icon in the text colour instead of an emoji.

  Tests (`test_web_models.py`):
    - admin-only settings, keys never sent back, URL checks;
    - the run environment per group;
    - OpenCode on Flux's model, then on its own;
    - `--model` for Claude Code and Codex;
    - with nothing set on the web, the machine's own configuration;
    - the loop's files listed, written, deleted, with the document and run output refused.

  Checked in headless Firefox, light and dark:
    - the login page;
    - the loop list and the upload dialog;
    - the Overview's best 2;
    - Live at rest showing the last task and the log;
    - the facts of an agent turn;
    - folded charts;
    - the files panel, with golden.py taken away and flagged missing;
    - Account and Admin › Models.

- **D697: the Live log and the note line, environment variables, a loop's advanced settings.**
  - **Live:**
    - The log is its own card under the task. It is coloured as the Log tab (problems, warnings,
      starts), wraps, keeps the last 80 lines, follows the end, and has "problems only" and a
      link to the Log tab.
    - It is fed by the Log tab's own stream (`onLines`), not a second connection.
    - The scroll to the end waits for layout (`requestAnimationFrame`); set at once, it landed
      mid-way.
  - **Notes:** the note card in the side column is gone. A line is docked at the bottom of the
    Live tab, like a chat's.
    - Enter sends, Shift+Enter breaks the line, and it grows to six lines.
    - The notes sent so far fold above it.
    - When the agent asks, the line shows the question and its time left, and Send becomes
      Answer. The banner stays on the other tabs.
  - **Environment variables:**
    - Scopes, applied in order: the server's (admins), a user's, a loop's (its owner). They are
      stored in the `server` table, secrets encrypted with the key of the model settings, and a
      secret is only ever said to be "set".
    - Names: `[A-Za-z_][A-Za-z0-9_]*`, at most 64. Refused: `FLUX_SANDBOX*` (a user must not take
      a loop out of the sandbox), the loop's plumbing (`FLUX_CONFIG`, `FLUX_FEEDBACK_INBOX`,
      `FLUX_TRACE_ROOT`, `FLUX_<AGENT>_ARGS/BIN`, `OPENCODE_CONFIG_CONTENT`), `PATH`, `HOME`,
      `LD_*`, `PYTHON*`, `XDG_*`, `NIX_*`, and the model settings (set under Models).
    - The sandbox drops secret-looking names that do not start with `FLUX_` (`*TOKEN*`, …), so a
      variable set on the web would have vanished. `run_env` now lists the names it set in
      `FLUX_SANDBOX_PASS`, and the sandbox passes those whatever their names.
    - A deleted loop's variables and settings go with it.
  - **Advanced settings** (`PUT /api/apps/{name}/advanced`, admins only, any owner's loop): run in
    the sandbox or on the host, memory, CPUs, processes, scratch `/tmp` size.
    - `sandbox_env` drops whatever the environment said about the sandbox, then sets it from the
      server and the loop.
    - A start on the host says so in the log line that marks it.
    - Admins can set these when creating a loop. Everyone sees them on Settings; going on the
      host asks for confirmation.
  - **Live check:**
    - A copy of the sweep whose bench prints `seed` and `token_len`, started from the web in
      Podman, measured `seed=4242` (the loop's `SEED`) and `token_len=16` (the user's secret
      `HF_TOKEN`, a name the sandbox drops unless passed).
    - With the admin setting off, the next start ran on the host, and its log said so.

  Tests (`test_web_env.py`):
    - the scopes' order;
    - secrets never sent back;
    - nine refused names;
    - `FLUX_SANDBOX_PASS`;
    - a deleted loop's variables gone;
    - advanced settings admin only, with size checks;
    - `sandbox_env` overriding the environment's own.

  Also tested: the sandbox passing a listed secret-looking name and still dropping an unlisted one.

- **D698: the admin's sandbox: the network allowlist, PATH, home files.**
  - **Network** (Admin › Sandbox, `server` table key `sandbox`): open, or an allowlist.
    - Under an allowlist, a start's own entries count only when the admin lets users add.
    - A loop's advanced settings (admins) add hosts for that loop.
    - "The model endpoints" adds the hosts of the base URLs set under Models.
    - On the host (an admin's per-loop choice) a run has the machine's network.
    - The start dialog says the server's list, and disables its own field when users may not add.
  - **The proxy checks IPs for names** (`permitted`). A name no name rule covers is resolved; it
    passes when one of its addresses is in an IP or CIDR rule, and the proxy connects to that
    address, so the name cannot change between the check and the connection. Before, an IP rule
    held only for a client that connected by bare IP.
  - **An empty allowlist opened the network:** with `FLUX_SANDBOX_NET=allowlist` and no
    `FLUX_SANDBOX_ALLOW`, `launch` started no proxy and gave the container the host's network.
    It now starts a proxy that allows nothing, and says "none".
  - **PATH:** every directory on the run's PATH was already mounted read-only. The admin adds
    directories, and optionally the server user's login PATH.
    - The login PATH is read from their shell in the user database, run `-ilc`, starting from
      `/etc/environment`, and read between markers.
    - `$SHELL` inside a nix shell is nix's bash, whose built-in PATH is `/no-such-path`; Ubuntu's
      login files set none.
  - **Home files:** `FLUX_SANDBOX_HOME_RO` and `FLUX_SANDBOX_HOME_COPY` (comma-separated, relative
    to HOME) extend the fixed lists, for example a modified agent's own config and credentials.
    - A path leaving HOME (absolute, `..`) is ignored.
    - A file is mounted onto a file.
    - A copied folder is copied whole before each run.
    - Users cannot set these: `FLUX_SANDBOX*` is reserved (D697).
  - **Live, in Podman, through the web,** a probe loop's bench measured:

    | Probe | Result |
    |---|---|
    | the login PATH | on PATH |
    | a read-only folder from HOME | read |
    | a copied credentials file | read |
    | the LocalAI host, allowlisted by name | reached |
    | example.com | refused, logged |

    The same with only the LocalAI host's IP `/32` allowed: reached by name; example.com
    refused.

  Tests (`test_sandbox_config.py`):
    - names passing IP rules by resolution, and a bare IP refused;
    - extra home paths mounted, copied, and kept inside HOME;
    - an empty allowlist giving no network and a proxy that allows nothing;
    - the admin's settings in a run's environment, including users not adding;
    - admin only, with bad entries refused.

- **D699: the Live standings as tables, a fuller Overview, a log of any length, the machine over
  time; a name logs in as a phone types it.**
  - **Standings:**
    - Plain values (problem, searching, gated, refused) are chips.
    - A list of records is a table: a record's own numbers (`numbers: {time_ms: …}`) become
      columns, and an artifact's text is left out.
    - A record of plain values is chips; anything else is nested key and value.
    - Before, everything past the counts printed as raw JSON.
  - **Overview:** the left column stacks the decision, the latest notes and the workbench; the
    right column has the charts and the last pass (when, its measurements, its conclusion, a link
    to the Timeline).
  - **The Log tab draws only the lines in view.**
    - Each row is one height, so the scroll position says which lines show. A spacer gives the
      box the whole log's height, and 60 rows are drawn above and below the view.
    - Start markers draw their rule as an inset shadow, so they keep the row height.
    - It keeps 200,000 lines (was 50,000; it drew 4,000).
    - The problem jump searches the filtered lines, not the drawn ones.
    - With wrap on, rows differ in height, and the last 3,000 are drawn as before.
    - The Live tab's log keeps its own 80 lines.
    - Checked in Firefox on a 120,002-line log: 151 rows drawn, the middle reached and drawn in
      1 ms, "problem ▶" landing on line 60284.
  - **The machine over time** (`history.py`):
    - `flux serve` samples once a minute into `samples` in the server's database: load, CPUs,
      memory, each disk's use, loops running, and, when any run, the containers' count, CPU and
      memory.
    - Samples are kept a week, pruned from now; pruning from the sample's own time kept stale
      ones.
    - `GET /api/admin/history?hours=` (admins) returns at most 360 points: each bucket's mean,
      except load and CPU, which keep the bucket's highest so a burst survives.
    - Resources charts them over 1 h, 6 h, 24 h or 7 d, each chart on its own scale.
    - Tests and other processes do not sample.
  - **Login:** a 401 "wrong name or password".
    - Names now match whatever their case and the spaces around them; passwords stay exact. A
      phone capitalises a name's first letter and autocomplete adds a space after it, so an exact
      match refused "Cedric" for "cedric".
    - Lookups use `COLLATE NOCASE`, and failures are counted per lowercased name.
    - A name differing only in case cannot be added; `set_user` finds the name as stored.
    - The name field neither capitalises nor corrects.
  - **Data folders:** the other way to get this 401 is a `flux user add` and a `flux serve` on
    different data folders (another user, `sudo`, a service's HOME).
    - `flux user` now says which folder it wrote, and that `flux serve` must use the same.
    - `flux serve` says its folder and its accounts.
    - On this machine the default folder did not exist; the only server running was the test
      one, which had no refused login. So the failing server was elsewhere.

  Tests:
  - The history: a week kept, thinning with a burst kept, admin only, a sample of the machine.
  - Login: four ways of typing a name, a password with a trailing space refused, a case-variant
    name refused at creation.

- **D700: uploads of any size, failures said, the applications folder for admins.**
  - **The failure was never seen:** "at most 500 files and 50 MB" came back as a toast, but a
    toast lives in the page while a modal dialog sits in the browser's top layer above it.
    - A toast now moves into the open dialog.
    - Errors nobody caught (`unhandledrejection`, `error`) become toasts.
    - An error page that is not the server's JSON (a proxy's 413, a crash) is said with its status.
  - **Limits:**
    - One request: 900 files and 256 MB. Starlette refuses more than 1000 files itself, with its
      own message, before ours.
    - A loop's own files: 100,000 files and 8 GB; a zip may fill a loop.
    - Our refusal says to send in batches.
  - **The page sends any size** (`sendFiles`):
    - Batches of 300 files and 40 MB, the document first when the loop is made.
    - A folder's common top directory is dropped, so all batches agree.
    - A file over 40 MB goes in 32 MB parts (`PUT /api/apps/{name}/part?path&offset&final`): each
      appended to a hidden `.part-upload` file at its offset, and the last part moves it into
      place. A part out of order is refused.
    - A progress bar shows while it goes.
    - Uploading, adding on the Files tab and the configurator's files all use it.
    - Checked in Firefox: 600 small files and a 100 MB one, as 3 batches and 4 parts, landed whole.
  - **Applications** (admins):
    - `GET /api/admin/applications` lists `applications/` (or `FLUX_APPLICATIONS`): each folder
      with a document at its top, what it asks, its size.
    - `POST …/use` imports one into the admin's loops (`import_dir`). Files are hard linked
      (copied when the disk differs), without `out/`, `runs/`, `workbench/`, `.git` or
      `__pycache__`; the meta names the `source`.
    - A run never writes its inputs (read-only in the sandbox). An edit or an added file unlinks
      before writing, so the repository's copy never changes.
    - `?refresh=true` takes the files again and keeps the loop's record, log and workbench; not
      while it runs.
    - The tab offers Use, or Open and Refresh.
    - sshfs does not show hard links by inode number, so the response counts what was linked and
      what copied.
  - **The empty loop lists** say "No loop yet." alone: New loop and Upload a loop are at the top
    right.

  Tests (`test_web_uploads.py`): parts in order, out of order refused, in place at the last, the
  run's folders refused; a batch over the limit saying how to go past it; an application listed,
  used, an edit not reaching the folder, refreshed with the record kept, a path escape refused; a
  loop's own limit.

- **D701: a loop shared to watch or to edit.**
  - **Permissions:** an owner shares a loop with another user.
    - **watch:** its runs and outputs: state, log, Live, Timeline, results, turns, files,
      settings.
    - **edit:** also its files, document and variables; check, start, stop, and notes to it.
    - Deleting and sharing stay the owner's.
    - An admin without a share watches, and may stop it, as before.
    - Shares live in the `server` table (`share:<owner>:<app>`) and go with a deleted loop.
  - **One check for every route:** `access(user, owner, name)` returns "owner", "edit", "watch"
    or "admin".
    - Reads go through `reader`, changes through `editor`, which refuses watch and admin.
    - The routes that change a loop used to act on the caller's own workspace whatever the owner;
      each now takes `owner` and passes it through `editor`. These are files, parts, document,
      preview, check, preflight, start, notes and variables.
    - Stop refuses watch.
    - A route left on the caller's own workspace would have let a watcher's call land on their own
      loop of the same name. `test_web_share` calls each with `owner` as the watcher and as the
      editor.
  - **An editor's start is the owner's loop:** the owner's record, model settings, keys,
    variables, sandbox key and limits. `RunManager.start(..., by=)` writes who started it in the
    start's log line, and the audit says both.
  - **The page:**
    - While on another's loop, every call about it carries `owner` (`owned()` in `api()`).
    - Actions follow the permission: edit offers Start, Check, Configure, the files' editor, the
      note line and variables. Watch offers only what it may see, with a "watching" label.
    - Deleting is the owner's only.
    - The Loops page lists "Shared with me".
    - The Settings tab has a Sharing card: the owner adds a user, changes watch and edit, stops
      sharing; others see the list.
  - **Fixed on the way:** the Overview's last pass printed `[object Object]`, because a pass's
    conclusion is a record. It is now shown a field per line.
  - **Live, two users in Firefox:** cedric shared sw (watch) and probe2 (edit) with dee.
    - dee's list showed both.
    - On sw: no Stop, no note line.
    - On probe2: Start, Check, Configure; no Delete; cedric's variables named as his.

- **D702: the loose ends of sharing and uploads, the lockout, breadcrumbs, placeholders, the agent
  at work.**
  - **The bell:**
    - It watches shared loops too: `/api/loops` adds them with their `owner`, and a notice names
      them as "cedric's sw".
    - It is each user's own (`flux-notes:<user>`). It was kept per browser, so the next user
      logged in on it saw the last one's notices.
  - **Notices** (`notices:<user>` in the `server` table, taken once by `/api/notices`):
    - a loop shared with you, or a share changed;
    - a share stopped;
    - for the owner, a guest who left.
  - **Leave:** `DELETE /api/apps/{name}/shares/me?owner=` takes a shared loop off a guest's list;
    a Leave button on watched and edited loops.
  - **Lockout:**
    - Five failures in ten minutes lock a name from that address only; fifty from all addresses
      lock it everywhere. Before, anyone could lock a user out with five wrong passwords.
    - The address is the client's. Behind the TLS proxy (`--secure-cookie`) it is the first
      `X-Forwarded-For`.
    - `failures` gains an `ip` column, added to an existing database.
  - **Uploads:**
    - Progress shows in a dialog that Escape does not close, with Cancel. Cancel stops between
      batches and parts and discards a file cut midway (`DELETE /api/apps/{name}/part`), with the
      folders it leaves empty.
    - The Files tab and the configurator use the same dialog; before, progress came as toasts.
    - Found while checking: the batching added in D700 took the top folder off paths whose drop
      had already lost it, so `more/huge.bin` landed as `huge.bin`. A chosen folder's name is now
      taken off once, by the upload dialog only.
  - **Breadcrumbs:** Loops › owner › loop › tab, Loops › loop › Configure, Admin › tab.
  - **Placeholders:** grey lines of the shape to come replace "Loading…"; they move only without
    reduced motion.
  - **The agent at work (Live):**
    - A task `agent: …` shows facts: model and version, status, output lines, rate limit, exit,
      time.
    - Then streams: its thinking, its numbered commands, the last command's output, its words,
      stderr.
    - A stream follows its end unless read upward: the place is kept across the redraw each second.
    - The parameters and raw fields fold below.
    - OpenCode does not say its model, so the live facts carry the model its configuration names
      and its version from the start; Claude Code's own report replaces them.
    - Checked on a real OpenCode turn: its thinking about `assign s = a + b` and its one command,
      writing `draft-add8.sv`.
  - **Claude Code and Codex:** neither is installed on this machine. A test checks the launched
    argv: `--model` from the web's settings, before Codex's stdin prompt.

- **D703: Files and the configurator follow `.gitignore`; `.git` is never shown.**
  - **`gitignore.py`** reads a loop's `.gitignore` files as git does (no library on hand):
    - the patterns of each apply below its folder, a deeper file after a shallower;
    - the last matching pattern decides; `!` re-includes;
    - a trailing `/` is for folders only, and a `/` elsewhere anchors the pattern to its folder;
    - `*` and `?` stay within a name, `**` crosses folders, `[…]` is a class;
    - nothing under an ignored folder comes back.
    - On a 22-file tree with two `.gitignore` files it answers as `git check-ignore` on every file.
  - **Files tab:** the ignored are left out; "show ignored files", kept per browser, lists them
    greyed and marked. `.git` is not listed even then, its files are not read, and its folder is
    not opened.
  - **The configurator's files** leave out the ignored.
  - **Not changed:** what the loop runs. A run, its digest, uploads and the applications' import
    are unchanged; prefetcher's ignored `traces/*` are its inputs.

- **D704: New loop and Configure, three ways each; an agent writes or revises a problem from the
  web.**
  - **New loop:** Configurator | Upload | Agent. **Configure:** Configurator | Direct edit | Agent.
    - Each tab has its address (`#/configure/agent`, `#/app/x/configure/edit`).
    - The Loops page has one New loop button; the upload dialog became the Upload tab.
  - **Agent** (`authoring.py`): `flux ask <ask> --dir <work> --no-run --author <who> --file …`,
    started with the loop owner's environment, sandbox, limits and network, as a run is.
    - The author is OpenCode, Claude Code, Codex, or Flux's own model. `/api/agents` says which
      can work here; one not installed is listed and disabled, with why.
    - **A new loop** (`POST /api/apps/new-by-agent`, with attachments): an empty loop, then the
      job.
    - **A revision** (`POST /api/apps/{name}/author`): the ask carries the instruction and the
      current document. The author is told to edit it in place; a `problem.yaml` it writes instead
      is moved onto the loop's document name.
    - The job has the loop's `runs/author.log` and `author.json`, and outlives the server.
      `GET …/author` gives its state, log tail, and before and after; `…/author/stop` stops it.
    - One job at a time. Not while the loop runs, and no start while it writes.
    - Its finish is locked and done once: the waiting thread and a status read both saw it end,
      and both moved the document.
  - **The agent works on a copy:** the loop's own files are copied into `.author-work/`, the only
    writable folder in the sandbox, and copied back after (a linked file replaced, never written
    through).
    - Found live: the job ran with its cwd at the loop's folder, which the sandbox mounts
      read-only, and `--dir` was the same path. The read-only mount won, so the agent could not
      write. In `mounts_for`, a path asked both ways is now writable.
    - With the whole loop writable, the agent could also have reached the record and the log.
      Hence the copy.
  - **A `--no-sandbox` server** never told its runs so: a run's own default is the sandbox, so
    they went into the container anyway (the tests set `FLUX_SANDBOX=0` themselves). `sandbox_env`
    now sets `FLUX_SANDBOX=0` for such a server.
  - **Live, OpenCode on LocalAI through the web, in Podman:**
    - It wrote `add4agent` from one sentence and an attached spec in about 45 s: `problem.yaml`,
      `golden.py`, `gen.py`, `inputs/spec4.md`.
    - The loop's own check passes: a sweep over the architectures `gen.py` spells, Yosys and
      OpenROAD stages, objectives at least 2000 MHz then least area.
    - It then revised it to 2500 MHz in 40 s: exactly the statement, both clocks (500 → 400 ps)
      and the goal. A test file in `out/` was untouched, and the work copy was gone.

  Tests (`test_web_author.py`, with a stand-in for `flux ask`):
  - a new loop written with its attachment read and gone;
  - a name taken or an unknown author refused, with no loop left;
  - a revision keeping the document's name and showing before and after;
  - start and a second job refused while it writes;
  - a `--no-sandbox` server telling its runs;
  - a path asked both ways mounted writable.

- **D705: ask an agent about a loop; the agent by default; the agents' programs the admin's.**
  - **`flux consult "<question>" --loop <folder> --out <folder> --author <who>`**
    (`flux_loop.consult`):
    - The answerer reads the loop: its document and files, its log, its last answer.
    - It gets a snapshot of the record: SQLite's backup into `out/record.db`, opened
      `mode=ro&immutable=1` so a read-only mount does it no harm, and queryable as it likes.
    - It gets a summary: the objective, passes, measurements, the last conclusion, the best per
      objective.
    - An agent writes `answer.md` in its working folder; Flux's own model gets the document and
      the log's end inline and answers in text.
    - It is sandboxed (`consult` is boxed like `ask`): the loop's folder read-only, only `--out`
      writable, the run's network.
    - The agent's printed output is kept (`agent.out`). A failure says its error in the answer.
  - **Web** (`asks.py`, the loop's Ask tab):
    - `POST /api/apps/{name}/asks` (edit) runs it into `runs/asks/<id>/` with the owner's settings
      and sandbox; `GET` lists questions and answers (watchers too); stop, forget.
    - One at a time per loop. The record's snapshot is deleted once answered.
    - Answers are rendered from Markdown node by node: headings, lists, tables, code, quotes,
      inline marks; never as HTML.
  - **The agent by default:** a settings group (`FLUX_DEFAULT_AGENT`: opencode, claude, codex,
    model), the admin's for the server and a user's own over it. `/api/agents` marks it, and every
    agent picker starts on it. The per-browser memory of the last pick is gone.
  - **The agents' programs:** `FLUX_OPENCODE_BIN`, `FLUX_CLAUDE_BIN`, `FLUX_CODEX_BIN` under
    Models.
    - Admin only: a user sees them and cannot set their own.
    - A path or a name on PATH; anything else is refused.
    - An absolute path's folder goes first on the run's PATH, which the sandbox mounts read-only,
      so a modified OpenCode outside PATH is found in the container.
  - **Live, OpenCode on LocalAI:**
    - From the command line on add8, in Podman: an answer with the two designs' numbers and the
      decision's history.
    - From the web's Ask tab: the first try ended with exit 1 and no output kept, hence
      `agent.out` and the error in the answer. The second answered in 43 s.
    - That answer raised a finding to look into in the loop itself: by add8's objective (fmax at
      least 2000, then least area) add8#1 (area 3.0, 4242 MHz) beats the decided add8#2 (area
      4.0, 5514 MHz) at confirm.
  - **Tests:**
    - `test_web_asks.py`:
      - the core with a scripted model: a whole snapshot of a WAL record, the brief for a model
        and for an agent, the answer written;
      - the web (with a stand-in held by a handshake, not a clock): answered and kept, a second
        refused while one runs, the snapshot gone, a watcher reading and not asking, bad ids
        refused, forgotten;
      - the agent by default (admin, user, refused value);
      - the programs (admin only, refused value, folder on PATH).
    - `test_web_models` lists the new group.
