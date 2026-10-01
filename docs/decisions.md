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
- [The sandbox](#the-sandbox)
- [The web interface (flux serve)](#the-web-interface-flux-serve)
- [Since the fold](#since-the-fold)

## The problem document

- **A task is a document, not code.** YAML or JSON, run by `PromptProblem`; `flux task check` validates, `flux task run` runs. What a document cannot say goes in a `world:` package (the NLU is `nlu.problem.yaml` + `flux_nlu.world.World`). (D430, D519, D520, D541)
- **Typed, closed schema.** Unknown keys and unknown `space:` placeholders are load errors; `cache:` sets caching. (D541, D581)
- **A document says only what is its own.** `extension` comes from `language`; `gate:` may be the command alone (count defaults to `N failing`, `count_re`/`fail_re` for other checkers); a `flux rtl measure` stage knows its metrics and tools; a known metric has its unit; a goal is judged on the deepest stage. `budget` has eight common knobs, the rest advanced. (D628)
- **Short documents.** A 1-3 line header with the run command; a comment only where a value is not obvious; no keys set to their default. (D631)
- **`flow:` is the only spelling of the drawing.** Boxes, in order: validate, orchestrate, plan, dse, generate, test, critique, calibrate, select, feedback, knowledge, extract; records is always on and not a key, and a stage's estimate is the stage's own (D665). There are no `roles`/`generator`/`critique`/`decompose` keys; `--role` and `Roles(...)` switch a role per run. `flux task check` prints the resolved flow, saying what each default and agent does. (D542, D629, D665, D666)
- **Every stage measures every objective.** Each stage ranks its own rows, so a stage missing an objective has no front; `PromptProblem.validate` (run by `task check`) refuses it. (D625)
- **The world contract.** `CONTRACT` in `flux_loop/document.py` lists the hooks a world may fill, by box, with fourteen `CORE` ones; a world defining `loop_owned()` is refused with the contract printed. (D561)
- **Commands and metrics.** Commands are shell-like strings; a command headed `flux` runs this Flux; stages report `metrics:` `name=value` lines. (D580)
- **Sub-documents.** `subtasks:` children inherit world, hooks, ladder, cache and flow, and keep their own record `<parent>/<child>`. (D555)
- **The space.** `seeds:` are measured first (the first is home); a knob with `when:` moves only while other knobs hold given choices, otherwise it sits at its first choice, so no two points differ only in a dead knob; phase `knobs`/`hold`/`keep` take globs; `space:` components with `optional: true` add `<component>.on`; `{point}` is the whole point as JSON. (D634, D637)
- **A goal relative to the best.** `keep: 0.9, above: 1.0` means within 90% of the best design's gain over 1.0; then the least on the next objective. (D634)
- **Extension beside the document.** Worlds, hooks and `module:Class` policies resolve relative to the document; `docs/extending.md` lists the extension points. (D602)
- **Files beside the document.** A string `workload:` is read beside the document (`{home}/w.yaml`), not from the cwd. (D663)

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
- **Settings that mean nothing are refused or said.** `dse: pareto` with fewer than two objectives is a load error; `feedback: none` drops the channel and earlier notes; a coding-agent plan runs without a model; `budget.finalists` holds with one objective. (D666)
- **A start forgets the last stop; a finished search rests.** `ops.register` removes the stop file when a new process or container registers (a start obeyed an hour-old "Stop now"); a search with every point on record turns "nothing left to do" into a rest, so a resumed sweep no longer spins (454 passes in 30 s). (D695)

## Search: DSE policies and phases

- **One frontier library.** `flux_frontier` owns Pareto dominance, the interval rule and the decision arithmetic (`corner`, `knee_ranked`, `cheapest_meeting`); dominance, the note sink, record read-back and `run_tool` are each defined once. (D399, D429, D439)
- **ParetoUCT.** Hypervolume-driven tree policy; its rollout estimate only orders moves and is never recorded. (D368)
- **The objective vector.** Ordered, with one `better` rule; tournaments, frontier axes, decisions and the goal check derive from it. (D511)
- **Goals, stages and margins.** A goal is judged on its objective's stage; a shallower stage must clear it plus a margin learned from measured stage-to-stage ratios (`margin:` is the floor). Meeting a goal is a floor, not a stop. (D522, D543, D562)
- **Tool result size.** Cut at `budget.tool_result_chars` (40,000). (D543)
- **Policies from the document.** `flow: dse` names sweep, montecarlo, anneal, gradient, genetic, or `llm` (the model proposes the next batch); it may list phases with floors, margins, waves and patience; worlds may add `seeds` and `moves` hooks. (D553, D554, D583)
- **Every goal is a limit.** Each objective with a `goal` (or `keep`) must hold; among designs meeting every limit the goal-less ones decide in written order, `balance: true` ones as their knee; when none meets every limit, fewest missed, then the smallest relative shortfall. Over 3,000 random pools per shipped document the picks were unchanged but for an exact area tie in the NLU. `describe()` and every display -- `task check`, the standing, the DSE prompt, the report's axes -- name every limit. (D658, D660)
- **A resumed search goes on from its record.** Points measured on earlier passes rejoin `state.scored` before the walk: a sweep proposes only unmeasured points (none left: at rest), samplers draw new ones, a gradient starts from the incumbent, the decision is over the whole record. (D682)

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
- **A gate is named checks in order.** `gate: [{name, run, count_re?, fail_re?, timeout_s?}]`; the first reporting failures refuses the design ("failed at <name>: ...", which repair carries) and the rest do not run; exit 3 is "did not build"; the score is the failures plus 1,000,000 per check not reached. `gate: <command>` and `{build, test}` are the simple cases. (D652)
- **`flux rtl lint`.** Verilator `--lint-only -Wall`, counting only hardware defects the vectors can miss (LATCH, MULTIDRIVEN, UNOPTFLAT split per bit, COMBDLY, BLKANDNBLK, IMPLICIT); milliseconds, so it goes before the golden test. (D653)
- **One tool catalog.** `flux_loop.toolbox.TOOLS` lists every check and stage a document can name (params, metrics with units, needs, pass rule, languages); an entry may give stage or document keys instead of a command (`zigzag-eval`, `timeloop-eval`). `flux tools --json` is the crafter's `tools.json`, kept equal by a test. (D654, D663)
- **Objections only to defects.** The critic's and validator's questions say what is not a defect (comments, style, depth claims) and the loop's conventions (`{home}` filled, `steps`, `finalists: 0`, goal-less objectives); validate stays advisory. (D642, D646)

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
- **The flow spells verified prototypes.** `flux_loop/py2sv.py` transpiles Python to width-exact SystemVerilog (if-conversion, unrolling, inlining, ROM tables) and checks it on every input; ICSC (`.#systemc`) translates a SystemC prototype, port widths checked against the golden model. The model transcribes only what cannot be spelled (Python over 20 input bits, SystemC without ICSC). (D478, D606, D611, D636)
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
- **Model settings once per machine.** Every `flux` reads `~/.config/flux/flux.env` (`FLUX_*`/`OLLAMA_*` lines, the shell winning; `FLUX_CONFIG` moves it); `FLUX_REMOTE_API_KEY_FILE` keeps the key out of both, loaded into memory for the agents a run starts; the repo holds placeholders only. (D651, D669)
- **External coding agents.** `generate: {agent: claude|codex|opencode|{command}}`, headless. The brief comes on stdin (a custom command naming `{prompt}` gets a file past 100 KB: Linux refuses an argument over 128 KiB) and names the problem's files and nothing outside them; questions follow `questions: decide|model|operator`, capped by `max_questions`. `bin`/`FLUX_<PRESET>_BIN` renames the program, `args`/`FLUX_<PRESET>_ARGS` adds arguments. (D575, D585, D642, D670, D671, D672)
- **An agent writes the prototype, or the target.** With `prototype: true` it writes the prototype, the loop proves it with `flux rtl proto` and spells the RTL; with the prototype off it writes the target itself (it was never called). A silent turn is nudged twice at most; an outgrown session continues fresh. (D618, D643)
- **A coding agent in any box.** `flow.<box>: {agent: opencode|claude|codex}` for validate, orchestrate, plan, dse, generate, critique, extract and select; test, the stages, calibrate and records are never delegated (D460). The loop writes `BRIEF.md`, checks the agent's `out.json` against the box's schema and rule, sends a refusal back once, then falls back to the rules half; each turn is a `decided:agent_turn` event that `flux report` lists. `select` only breaks ties the objectives leave open. (D630, D640)
- **`flux ask`.** A model or coding agent writes and checks a document from a prompt, then the loop runs it; `golden.py` may declare `TOLERANCE_ULP`; a duplicate `*.problem.yaml` copy is dropped. (D586, D589, D627)
- **Skills.** SKILL.md skills are copied into coding agents' folders or inlined for the loop's model; `skills/flux/SKILL.md` teaches outside agents to use Flux. (D588, D592)
- **Agents write, the loop runs.** The brief says not to compile, simulate, synthesize, test or run; the loop gates and measures, and a refusal resumes the part's session with the exact output. A deny list, not no shell: compilers, simulators, synthesis, `make`, `pytest`, `flux rtl|task|run`, `bash`/`sh` are refused (Claude Code `--allowedTools Bash` with `Bash(<cmd>:*)` denies, OpenCode permission rules); `python3`, `pdftotext` and Icarus (`iverilog`, `vvp`) stay. `allow:` gives commands back to one agent. (D673, D674, D685)
- **`flux probe`, the loop's tools inside a turn.** `flux probe gate FILE` and `flux probe measure FILE --stage S...` run the problem's own build, judge and stages (stages side by side, each saying its limits: exit 0 met, 1 missed, 2 refused) within a per-turn budget (`probe: {gate: 20, stages: 3}`); probes are logged on the `agent_turn` row, never trials. add8 with probes: 2.96 µm², 24 cells; without, 3.135. (D678, D679)
- **One agent session per job.** A generate agent keeps one session per part until it is admitted: a repair or send-back resumes it with a short message (427 chars against the brief's 11,854); an improve is a new agent; a decision box takes `session: turn|pass`; rows say fresh or resumed. (D669)
- **The agents' workbench.** `workbench/` beside the document (`tools/`, `notes/`), kept across runs and linked into every agent's folder (Claude Code also gets `--add-dir`); the brief lists it and asks for notes; the loop never reads it. On isq16 a turn built its own ASAP7 timing model and the next drafted from it (998.9 MHz, 20.5 µm²). (D677)
- **A running agent shows what it does.** Its task row updates each second from OpenCode's JSON events (`--thinking`) or Claude Code's `stream-json` (`--include-partial-messages`): model and version, status, rate limit, stderr, output lines, and its steps in order -- words, thinking (redacted: its token count), each tool call with its input fields and output. A turn keeps every step in `turns.jsonl`, with its tokens and cost. (D668, D675, D676, D694, D696, D712)
- **Agents read the loop around them.** OpenCode's `external_directory` is allowed (`opencode run` cannot ask) and Claude Code gets `--add-dir <home>` when the loop is outside its working folder. (D711)
- **Each agent gets its own variables.** None of the other agents' prefixes (`ANTHROPIC_`/`CLAUDE_CODE_`/`FLUX_CLAUDE_`, `OPENAI_`/`CODEX_`/`FLUX_CODEX_`, `OPENCODE_`/`FLUX_OPENCODE_`): OpenCode's providers read `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` on their own. `FLUX_REMOTE_*` stays; the `--version` call gets the same. (D718)
- **`flux consult`.** A coding agent or Flux's model answers a question about a loop from its folder (read-only) and a snapshot of its record (SQLite's backup, opened `mode=ro&immutable=1`), writing `answer.md`; it changes nothing, sandboxed like `ask`; `agent.out` keeps a failure's words. (D705)

## Stages, evaluators, measurement and calibration

- **A chain of stages.** Each with optional cutoffs -- one condition or a list, all must pass, the cut saying which condition cut which designs; the decision reads the highest stage with results; an escalation target must be a registered evaluator. (D448, D454, D466, D657)
- **A stage's estimate is a pre-gate, off by default.** `stages[].estimate: {kind: surrogate|command|model, margin}` predicts the stage's metrics; a design that fails a cutoff or limit there by more than `margin` is never measured, and no estimate means the tool runs. An estimate may only save a tool run, never choose what climbs. The loop records costly/cheap ratios as calibration. (D464, D560, D665)
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
- **`flux prog time|count|size`.** A program built fresh per candidate (`--build` writing `{out}`), measured by hyperfine, by cachegrind (instructions and misses: deterministic, the noise-free proxy for time) or by `size`; a failed build exits 3. (D661)
- **`flux rtl measure --stage stat`.** Yosys's mapping and `stat -liberty` alone: area and cells in under a second, the cheapest RTL screen (`rtl-stat`). (D662)
- **A tool's task says what ran.** `run_tool`'s `tool:<program>` phase carries the command, folder and candidate, the ends of stdout and stderr live each second and at the end, and the exit; a timeout keeps what was printed; bytes that are not UTF-8 are replaced. (D709)

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
- **The library reaches every document.** The mentor leads with `Library` (BM25 lookups from the statement, contract and parts) and `Papers` unless `knowledge: none` or it is empty; agents' briefs carry a LIBRARY section; a draft records the files its prompt cited (`provenance.library`). `FLUX_LIBRARY` moves it. An excerpt has at least eight distinct words. (D648, D650)
- **Operator notes from the web.** A run started by `flux serve` also drains `runs/inbox.jsonl` (`InboxChannel`, joined with the terminal's); an agent's `operator` question is a journal mark the page shows with its time left, answered by the next note. (D684)

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
- **On a real model.** Hosted qwen3.6 wrote the knob file (bingo + sms + ampm), the gate admitted it, geomean 1.0914 at 35,096 B on short stages. (D647)
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

- **Tools via adapters and Nix.** No third-party tool is copied in or built by the repo; tools come from the `nixchip` input, whose substituter `flake.nix` repeats (an input flake's `nixConfig` is ignored). ICSC (LLVM 18) is nixchip's too, in `nix develop .#systemc`, so the default shell builds nothing; hyperfine and valgrind are in the default shell. (D21, D315, D348, D636, D645, D656, D661)
- **Tree layout.** The four module types plus `applications`, `core`, `interfaces`; role packages import no application; single-use evaluators live with their application. (D296, D428)
- **Install and scaffold.** `pip install -e ./flux`; `flux task check` infers what `flux rtl` needs; `flux new --kind` includes tune and rtl-sweep. (D598, D600, D608)
- **`flux selftest`.** Tools, a sweep, an rtl-sweep, the model server, a model-written problem and a coding agent, each PASS/FAIL/SKIP (`--full` adds adder16); exits 1 on a failure. (D623)
- **CLI behaviour.** Real exit codes, one-line errors, missing tools announced; clear messages for misspelled keys, unknown placeholders, broken YAML and unknown policies (naming `llm`). `flux ask --tui` opens a setup form; authored documents go to operator review. (D587, D590, D627)
- **Where things live.** `flux_loop/document.py` (document and contract), `task.py` (`PromptProblem`); callers use `flux_evaluator_abi` and `flux_loop.report` directly. (D558, D612)
- **Comments say what the code does.** The non-obvious why, then a `(Dnnn)` pointer; no stories, dates or quotes. `CONTRIBUTING.md` explains D-numbers. (D97, D186, D612, D620)
- **One home per doc topic.** `flux/README.md` is the developer map, `docs/usage-guide.md` the command reference; docs say what the code does and carry no hand-kept counts (parity tests do). (D195, D196, D246, D620, D632)
- **Docs for newcomers.** `docs/tutorial.md` builds a new problem end to end (isqrt, in `docs/tutorial/isqrt/`); `docs/models.md` covers model settings; the README's Safety section says documents and agents execute code as the user. (D531, D590, D601, D624)
- **Public site.** MkDocs Material under `website/`, built `--strict` by `pages.yml`: numbered steps with copy-paste commands; `guide/loop-shape.md` mounts the crafter's drawing read-only, and a test keeps its box table complete. (D387, D395, D396, D644, D679)
- **The loop crafter, a problem builder.** `crafter.js` starts empty: ordered checks and measurements picked from `tools.json`, each measurement with its "go on only if" gates, an Objective row per metric; the fixed loop diagram (one Measure box, red rejection paths, the critic at the division, each part and the decision, fixed steps locked) whose popovers are `describe_flow`'s words. `buildYaml`/`check` are pure and `fromDoc(raw, normal)` is their inverse: what the form cannot say is kept, named, and appended by the server. Every repository document round-trips; host options: `calmChecks`, `nextSteps`, `foldSteps`, the name's label. (D644, D649, D655, D659, D664, D667, D679, D686, D719)
- **TUI foundation.** `flux_tui`: stdlib curses on an event bus, a reusable `Pane`; `demo_run` is the one run path. (D390, D404, D532)
- **TUI keys.** A resize never ends a run; the prompt line is feedback; `r` loops passes; PgUp/PgDn scroll a screen; `f` feedback, `t` thinking, tab 9 info. The first `q` stops at the end of the pass, a second abandons now. (D390, D392, D393, D406, D409, D574, D621)
- **Task and timing tabs.** Tab 1 is the current task, tab 2 a foldable phase tree with per-call costs; each tool round is `llm: round`. (D391, D418, D546, D547)
- **Rows carry their content.** Knowledge, gate, propose, critique, transpile and measure rows show what the step saw and decided. (D418, D490, D492)
- **Results tab.** The objective, what is happening now, the whole design, each part's constraint, and the front. (D497, D498, D532, D573)
- **CI shape.** Per push: ruff, the unit core and conformance. Nightly and on dispatch: the heavy tests, the integration sweep, a physical OpenROAD job and `timeloop-hermetic` (both fail on skips). A no-Nix job pip-installs and runs `flux selftest --no-model`. (D155, D207, D230, D371, D590, D600, D626, D638)
- **CI mechanics.** A shared `nix-setup` action caches `/nix` per shell with `run_id` keys; scheduled runs are never cancelled; `test_ci_workflow.py` asserts this. (D209, D242, D246, D247)
- **Test hygiene gates.** Every discovery test asserts a floor. Unit tests check corpus holdout, data-file paths, `parents[N]` paths, `localSrcDirs` parity with the packages, `TYPE_CHECKING` imports, and every relative link and anchor in the Markdown (`test_doc_links.py`). Undefined names are ruff's F rules. (D123, D195, D198, D320, D334, D335, D619, D639)
- **Test environment.** `conftest.py` puts `localSrcDirs` on `sys.path`; `FLUX_TMPDIR` (default: the user cache) becomes `TMPDIR`. The unit core keeps pytest's folders and its runs' TMPDIR under `/dev/shm` when 4 GB are free (`FLUX_TEST_SHM=0` opts out): SQLite's fsync on sshfs made it 145 s, now 48 s. (D123, D343, D405, D715)
- **Tests count, not clock.** Concurrency tests assert overlap (a peak of 2 or more), handshakes replace sleeps, and a wait ends as soon as it can but allows a loaded machine. (D641, D692, D710)
- **The browser test.** `tests/e2e/web_ui.py` starts its own `flux serve` with three users and walks every page in headless Firefox over Marionette, failing on a script error or a red notice; `page()` waits until the old content is gone, `button()` never takes a tab. (D710)
- **Fast core by default.** Real-tool and whole-study files, and single tests over 25 s (`HEAVY_TESTS` in `conftest.py`), are `heavy`. A golden campaign test pins the loop; `test_loop_live.py` runs each world's document and fails on one that does not load. (D374, D531, D559, D628, D639)

## The sandbox

- **Every run is boxed.** `flux task run|check`, `ask` and `consult` re-launch themselves in a container, on by default (`--no-sandbox`, `FLUX_SANDBOX=0`). Read-only: `/usr`, `/nix/store`, chosen `/etc` files, the source, the cwd, PATH folders and their links' targets, the running Python's prefix and `sys.path`; writable: the record's folder, `out/`, `workbench/` and the application's cache; a path asked both ways is writable. `--read-only`, a tmpfs `/tmp` (Yosys's abc hung on scratch mounted from the host), `--cap-drop ALL`, `no-new-privileges`, a pids limit; the container runs `sys.executable -m flux_cli`. (D680, D682, D704, D714, D716)
- **Rootless Podman first.** No daemon, no image (`--rootfs` of merged-/usr links), state under `/var/tmp/flux-sandbox-<uid>` (a home on sshfs cannot hold it); Docker otherwise (`FLUX_SANDBOX_ENGINE`). The run records its engine command, so `flux status` and `stop --now` reach it. (D682)
- **The application's own cache and home.** Only `~/.cache/flux/apps/<user>.<app>/` (`tmp/`, `home/`, `cache/`) is writable, shared by that loop's runs and asks; HOME is its `home/`, with the agents' configurations read-only and their credentials copied in. Not mounted: `~/.ssh`, the engine's socket, `~/.config/flux`. Secret-looking variables are dropped unless the server names them in `FLUX_SANDBOX_PASS`; `OPENCODE_SKIP_SAFE_CHECK=1`. (D680, D681, D687, D697, D714)
- **Home files mounted or copied.** `FLUX_SANDBOX_HOME_RO` and `_COPY` extend the fixed lists, inside HOME only. A copy is made entry by entry to a name beside the old and renamed over it (a running program keeps its inode, a read-only file is replaced), links as links, the source's mode kept, the copy's own files kept; a copied path is never hidden by a read-only mount at, inside or above it. (D698, D706, D707, D725)
- **The network: open, or an allowlist.** Under an allowlist the container has `--network none` and a relay at 127.0.0.1:18080 to a proxy on the host's Unix socket. A name passes by a name rule, or by an address in an IP or CIDR rule, and is connected by that address; an empty list allows nothing. Behind a corporate proxy the allowlist proxy chains through the host's `HTTPS_PROXY` (credentials, `NO_PROXY`), the host's certificates are trusted inside (`/etc/pki`, the CA files the host names, `NODE_EXTRA_CA_CERTS` for Node and Bun), and loopback stays inside. (D680, D698, D722)
- **Refusals are audited; the list is not shown.** The container resolves names through the relay (`resolv.conf` at 127.0.0.1:53, `ip_unprivileged_port_start=53`), so a client that ignores the proxy is still seen. Each refused host or name goes once per run to the server's refusals file and the admin's audit. Users read at most "network: an allowlist of N entries": no hosts, no `--no-sandbox` hint, no list in the container. A bare-IP connection fails unseen. (D708, D716, D717, D720)

## The web interface (flux serve)

- **The server.** FastAPI and uvicorn, plain JavaScript pages without a build (`h()` builds nodes, never HTML); SQLite for users (scrypt), hashed session tokens, settings, samples and the audit; `X-Flux: 1` on every change; pages, scripts and styles sent `no-cache`; a stop waits at most 3 s for open streams. (D683, D694, D719)
- **A loop runs or it does not.** An application is a loop with one record, and a start resumes it; there are no run numbers. `runs/` holds one `loop.log` (each start marked, with who started it), `answer.json` and `inbox.jsonl`; one live run per loop. The run's journal (`events.jsonl`: every phase event, text cut to 4,000) lets the page follow it, sandboxed or not; streams resume from `<inode>-<byte>`. (D683, D688, D689, D694)
- **Users, shares, admins.** Loops are per user. A share is watch (see it) or edit (change and run it, on the owner's record, settings and keys); every route asks `access()` and goes through a reader or an editor. Admins read every loop and may stop any run. Names match whatever their case and spaces; five failures lock a name from one address, fifty everywhere. (D687, D699, D701, D702)
- **Model settings and variables.** A tab per tool -- Flux (its model and the agent by default), OpenCode, Claude Code, Codex, Other (Ollama, OpenRouter) -- the server's, then a user's; naming one's own endpoint drops every server value of that group; keys are Fernet-encrypted and never sent back. OpenCode gets a `flux` provider, Claude Code and Codex a `--model`; the agents' programs (`FLUX_*_BIN`) are the admin's. Environment variables come from the server, the user and the loop, in that order, plumbing names refused. (D684, D696, D697, D705, D721)
- **A loop's page.** Six tabs: Overview (figures, the decision and the best three, best-so-far charts, notes, the last pass), Live (Tasks as a tree or a graph, Log, Timeline), Results (measured designs accepted or failed by every limit with the misses said; sortable, paged, Pareto and improvement charts thinned to 3,000 rows keeping every best, two designs compared), Agents (each turn's conversation, tokens and cost; usage per loop, agent and user), Files (Loop files, Workbench), Settings (Problem; Variables and sharing; Advanced for admins; Delete at the end). Ask is a panel over any tab. Addresses name the view; the old ones lead to their new places. (D689, D690, D692, D693, D694, D696, D699, D713, D723)
- **Live and the log.** The task tree follows the running task, an agent first; an agent's or a tool's task shows facts and streams; standings are tables; the log is numbered and coloured, filters, and draws only the lines in view (200,000 kept); a docked note line answers the agent's question. The Timeline bars leaf phases by kind, busy time against summed. (D688, D694, D697, D699, D702, D709, D712, D723)
- **Making and changing a loop.** New loop: Configurator, Upload (any size: batches of 300 files and 40 MB, 32 MB parts, cancellable), Agent (`flux ask --no-run`, sandboxed, on a copy in `.author-work/` written back), Example (`flux new`'s kinds with their files). Settings › Problem: the configurator with a diff before saving, Direct edit with the loader's refusal shown, an agent's revision. Files follow `.gitignore` as git does; `.git` is never shown. Admins import `applications/` as hard-linked loops. A start re-runs the check only when the inputs changed (`inputs_digest`). (D693, D700, D703, D704, D710, D719, D723)
- **Ask.** A question runs `flux consult` into `runs/asks/<id>/`, one at a time, with the owner's settings and sandbox; answers are rendered from Markdown node by node. (D705, D713)
- **Admin.** Loops (pause starts, stop every loop), Applications, Resources (the machine, containers by their `flux.app` label, disks, caches to clean, history sampled each minute for a week), Sandbox, Models and variables, Users (running limits, usage), Audit (kinds in groups, users, refused hosts). (D695, D699, D700, D708, D724)
- **Page mechanics.** Toasts and dialogs (a toast moves into an open dialog; uncaught errors become toasts), placeholders, breadcrumbs, a bell per user with notices, a light and dark theme, a highlighter that builds no HTML; a page draws only while it is the latest navigation. (D688, D691, D700, D702, D719)

## Since the fold

New decisions go here, one short entry each: a bold lead naming the decision, then its key reason,
number or rule and how it was verified -- no narrative. From time to time they are folded into
the topics above.

- **Open.** A task started on a worker thread (a stage's parallel measurements) has no parent in the journal, so it shows at the top of the tree (D709). The Ask agent found that by add8's objective (fmax at least 2000, then least area) add8#1 (area 3.0) should beat the decided add8#2 (area 4.0) at confirm (D705): not looked into.
