# Design decisions

What Flux does, and why, today: each point is a decision and its key reason or number.
The D-numbers at the end of a point are the citations the code and docs use.
A new decision gets the next number (D960) and is appended under [Since the fold](#since-the-fold) as one terse bullet.

## Contents

- [The problem document](#the-problem-document)
- [The loop: steps, passes, roles](#the-loop-steps-passes-roles)
- [Search and the decision](#search-and-the-decision)
- [Gates, checks and scoring](#gates-checks-and-scoring)
- [Generation: prompts, repair, replies](#generation-prompts-repair-replies)
- [Prototypes, golden models and spelling](#prototypes-golden-models-and-spelling)
- [Models, coding agents, skills and flux ask](#models-coding-agents-skills-and-flux-ask)
- [Stages, measurement and calibration](#stages-measurement-and-calibration)
- [The record, reports and running campaigns](#the-record-reports-and-running-campaigns)
- [Knowledge, mining and operator feedback](#knowledge-mining-and-operator-feedback)
- [The applications](#the-applications)
- [The command line, the TUI, docs, packaging and tests](#the-command-line-the-tui-docs-packaging-and-tests)
- [The sandbox](#the-sandbox)
- [The web interface (flux serve)](#the-web-interface-flux-serve)
- [Removed](#removed)
- [Since the fold](#since-the-fold)

## The problem document

- **A task is a folder with a document, not code.** `problem.yaml` (or `.json`), run by `PromptProblem`; the folder's name is the id (`id:` is refused, so a copied loop gets its own record); `NAME.problem.yaml` variants beside it keep records `<id>.NAME` and are picked at the start. (D430, D520, D786, D787, D788, D875)
- **General hardware.** Memory, compute, interconnect and control, not only accelerators. (D1)
- **Typed, closed schema.** Unknown keys and placeholders are load errors; `flow` is the only layout; an older document is migrated by `flux task migrate` or Admin › Loops, one step per format change, written only when the result loads. (D541, D581, D783, D811, D813)
- **A document says only what is its own.** `language` is optional (inferred from the tools' catalog, else `text`); a known tool knows its metrics and units; `budget` has eight common knobs; a header with the run command, comments only where a value is not obvious, no defaults written. (D628, D631, D832)
- **`flow:` holds every box and who works it.** validate, orchestrate, plan, generate, test, measure, critique, calibrate, select, feedback, knowledge (records always on); a worker is a word (`model`, `off`, `rules`, a policy), an agent's name, or `{by: <who>, ...options}` beside the box's own settings; `task check` prints the resolved flow. (D542, D629, D665, D666, D775, D795, D830)
- **The gate is `flow.test`.** A command, or a map name -> command or `{run, count_re, fail_re, timeout_s}` run in order; the first reporting failures refuses ("failed at <name>"), `build` refuses on any non-zero exit, exit 3 is "did not build"; score is failures plus 1,000,000 per check not reached. (D652, D789)
- **Stages are `flow.measure`.** A map by name, a command alone for a known tool; every stage measures every objective except a limit named for a deeper stage, which waits there. (D625, D878)
- **The search is `orchestrate`.** Without a space it orders the parts; with one it picks points: `{policy, space, seeds}`, phases, `{by: model, batch_size}`, an agent, or `{command}`. (D797)
- **The space.** Seeds first (the first is home); a `when:` knob moves only under given choices; globs in phases; optional components add `<c>.on`; a knob's choices may be files (`from: "out/invented/*.sv"`); a model or agent generator needs a command that reads the knobs, else load error. (D634, D637, D798, D879, D911)
- **A goal relative to the best.** `keep: 0.9, above: 1.0` means within 90% of the best gain over 1.0. (D634)
- **Commands and files.** Shell-like; a command headed `flux` runs this Flux; stages print `name=value`; `{home}` is the document's folder, `{params}` its `params:` as JSON; `module:Class` policies resolve beside the document (`docs/extending.md`). (D580, D602, D663, D799)
- **Parts.** `parts: decompose`, names in order, or a map name -> statement; a division is at most 8 parts, joined by a blank line. (D792)
- **Sub-loops are folders.** `subtasks: [ops/recip, ...]`; a child says only what differs (`flow`, `budget`, `params` merge key by key), shares the parent's `out/` and `workbench/`, and run alone loads through its parent; a parent's `generate: {command}` composes `{parts}`, a model or agent `generate` drafts for its children. (D555, D801, D804, D805)
- **What a document cannot say is a command beside it.** A check, a stage, a generator, or an `orchestrate: {command}` search that reads `{history}`, keeps `{state}` and prints candidates, lessons, a conclusion and `done`; applications keep code as `python -m flux_<app>.steps`; embedding uses `run_loop` with any `Problem`. (D799, D803)

## The loop: steps, passes, roles

- **One problem-agnostic loop.** `run_loop` owns records, planning, generation, judging, composition, stages and the decision; no hand-written design families. (D421, D425, D507)
- **Four swappable roles.** Mentor, Orchestrator, Generator, Evaluator, each with an AI and a no-AI half; one `Gradient` serves prototype and generation; test, stages, calibrate and records are never delegated. (D427, D460)
- **Part by part.** Plan a part, generate it, judge it alone, admit and freeze it; compose only proven parts; `next_work` picks one work item a step; `parallel_parts` drafts together but admits in order. (D412, D457, D566, D569)
- **Plans.** The orchestrator may split and brief; the `plan` box gives each part a method from the library digest index, kept on the record and reused on resume. (D431, D432, D577, D623)
- **Sub-loops and searches.** A part may be a bounded-depth `SubLoop`; a search lives across a run's passes, one pick a pass (`budget.batch: N` for more), hearing a batch's results once all are measured. (D446, D455, D738)
- **Advisory critic.** It may delay once or add a caveat; never vetoes the gate. (D433)
- **Typed memory, resume from the best.** `PartState` per part, a `Ledger` across passes; each part's best refused attempt reloads and resumes in patch mode; up to six admitted designs kept per part. (D415, D438, D509, D528, D535)
- **Improve, revert, regress.** Any stage can send a design back; reverting is a tolerance earned by progress and spent by regression; an unmeasured score is never best. (D463, D470, D491, D504, D535)
- **One pass, one design.** A one-design loop builds a design each pass; with one standing, the orchestrator (or the rules: refine while the decision moves, explore after `budget.explore_after` = 2 passes) chooses refine (the ladder's due step, else a rework) or explore (a new design that beats it); a refused try is retried within the step budget, so such a loop never rests. (D845, D847)
- **Away from the first design.** Fresh drafts read TRIED SO FAR (best then latest, 8, with numbers); a model or agent draft equal to a measured design is refused before it is built. (D839)
- **Rest for parts and searches.** The whole's shortfall scales each part's goal; a pass that admits nothing and sends nothing back is at rest; after rest a pass explores each admitted design, or says why it cannot. (D518, D535, D621, D842)
- **Runs end only when stopped.** `flux stop`, Ctrl-C or a cap; `--passes N` at rest with nothing to draft ends; a start clears the old stop file; a finished search rests (else 454 passes in 30 s). (D593, D621, D695)
- **Passes at once.** `budget.parallel` runs passes in waves sharing one search, then a Conclusion pass decides over everything; on a server `FLUX_PARALLEL_MAX=1` unless an admin allows parallel work. (D740, D741, D747)
- **Model I/O rules.** In-turn tools on; text tool calls start a line; dicts of ROMs and data `in` refused in generated code. (D470, D491, D507, D535)
- **Meaningless settings refused or said.** `pareto` with one objective is a load error; `feedback: off` drops the channel; a coding-agent orchestrator or plan needs no model. (D666, D843)
- **Names hold across starts.** Drafts number on from the record (`x#N`); a point's name carries a non-word value's knob (`wheel=1`); the conclusion carries the design's key. (D743, D840)

## Search and the decision

- **One frontier library.** `flux_frontier` owns dominance, the interval rule and `corner`/`knee_ranked`/`cheapest_meeting`; record read-back and `run_tool` are defined once. (D399, D429, D439)
- **ParetoUCT.** Hypervolume-driven; its rollout estimate only orders moves. (D368)
- **The objective vector.** Ordered with one `better` rule from which tournaments, axes, decisions and goal checks derive. (D511)
- **Goals, stages and margins.** A goal is judged on its stage; a shallower stage must clear it plus a margin learned from stage-to-stage ratios; a tool result is cut at 40,000 characters. (D522, D543, D562)
- **Policies.** sweep, montecarlo, anneal, gradient, genetic, `model`, an agent or a `command`, optionally in phases with floors, margins, waves and patience. (D553, D554, D583)
- **Every goal is a limit.** Among designs meeting every limit the goal-less objectives decide in order (`balance: true` as a knee; more than two balanced ones Pareto over all); 3,000 random pools per document gave the same picks. (D658, D660, D878)
- **Only feasible designs decide.** No design meeting every requirement means no decision, the closest shown; `flux task run` exits 0 with an answer, 3 when none qualifies, 1 when nothing was measured; `flux_loop.eligibility` reads each limit as met, missed, unmeasured or pending, for the decision and the web. (D899, D900)
- **The pool is the record.** Each pass decides over every design measured on the deepest stage reached, this pass's and the record's; refined or explored designs are admitted, so the best stands. (D809, D861)
- **Parts decide on the whole.** With parts, only compositions decide; a part measured alone never does. (D895)
- **The decision says why.** Each conclusion records `decided_by` ("the least area_um2 at fmax_mhz at least 800"). (D815)
- **A resumed search goes on from its record.** Measured points rejoin before the walk, but only rows measured under today's key. (D682, D853)

## Gates, checks and scoring

- **A deterministic RTL test, no LLM.** `rtl.py test` reads vectors from a `$readmemh` table and Python compares outputs (signedness, ULP, latency); clocked designs get `clk`, `rst_n`, `start`/`done` and a bounded latency. (D49, D115, D864, D948)
- **Every input when it fits.** A golden's `EXHAUSTIVE = True` checks every combination up to 20 bits through one looped bench: 65,536 in 15.6 s against 14 minutes unrolled. (D865, D882)
- **Widths.** An `int` port carries its width and a 1-bit port is a `bool`; a golden refuses an unsigned output whose top bit no vector sets. (D202, D203, D622)
- **Lint first.** `slang` and `rtl.py lint` (Verilator `-Wall`, hardware defects only, milliseconds) before simulation. (D411, D653)
- **Test-driven generation.** The inner loop runs fast vectors and keeps editing on failures. (D416)
- **A build failure is not a score.** Exit 3 when it does not compile, 1 when it fails. (D594)
- **Only a success is a measurement.** A command's exit status and an evaluator's validity are checked before its numbers; a refusal never reaches the cache or frontier. (D897)
- **Advisory validation, objections only to defects.** `validate` logs objections as lessons; critic and validator are told what is not a defect. (D556, D642, D646)
- **One tool catalog.** `flux_loop.toolbox.TOOLS` lists every check and stage (params, metrics with units, needs, languages); `flux tools --json` is the crafter's `tools.json`. (D654, D663)

## Generation: prompts, repair, replies

- **Generation behind independent gates.** A refused design is never ranked; the drafter is a role (Model, Template, Catalog, Solver) sharing one draft-build-check loop. (D2, D456)
- **Structured replies, lenient parsing.** Schema-constrained output; fallback from strict JSON to JSON in prose; SEARCH/REPLACE blocks are patches. (D413, D603)
- **Repair is a patch, reverted if it does not converge.** An edit must match once; after 3 failed compile repairs the loop reverts to the last source that built; each turn opens with PROGRESS. (D414, D417, D423)
- **Errors in the model's terms.** Its own line numbers, the line, a caret and a hint. (D473, D552)
- **Prompt layout.** Static prefix first for caching, a focus window, paper excerpts and the part's score history; an unmeasured rewrite of a nearly-right design is refused. (D422, D500, D501)
- **Compute tool.** numpy, math, struct and the pure standard library; its screen is validation, the boundary is the sandbox. (D422, D545, D932)
- **Context compaction.** Past 60% of the window older rounds are digested; knowledge is densified then cut nearest the part. (D549, D550)

## Prototypes, golden models and spelling

- **Prototype before RTL.** Proven by the same gate; any gate naming `--golden` can prove one first; a coding agent's check is `python -m flux_loop.golden_proto`; HCL and HLS declined. (D424, D472, D502, D604, D951)
- **Two languages.** Integer Python or a synthesizable SystemC `SC_MODULE`, both compared by `golden_proto`. (D635)
- **The flow spells verified prototypes.** `py2sv` transpiles Python to width-exact SystemVerilog checked on every input (at most 20 input bits exhaustively); ICSC translates SystemC. (D478, D606, D611, D636)
- **py2sv idioms.** If-conversion, unrolling, inlining, ROM tables; tuples are rows of signals (returned, unpacked, chosen, indexed by a computed position). (D483, D618, D804, D806)
- **py2sv pipelines.** `CLOCK = True, LATENCY = N` cuts the datapath into N+1 stages by a depth proxy: gelu at 2 stages placed 466 MHz against 316. (D864)
- **Static refusals.** Float arithmetic, file I/O, domain-sized tables (over 64 entries), redefining a toolkit block; reloads re-verify under today's rules. (D468, D480, D482, D605, D616)
- **Toolkit and composition.** Verified integer blocks prepended to every prototype; verified operators become blocks; `from_fixed` exact at any magnitude. (D481, D487, D488, D489)
- **Knobs and spaces.** A prototype may declare a SPACE; the cheapest passing member is bound. (D479)
- **Diagnosis.** Attempts traced, FP16 near-misses diagnosed as a precision floor, failures grouped by leading input bits. (D482, D488, D617)
- **Edits versus new prototypes.** A >= 80%-same "new" one is an edit; a streak of unmeasurable attempts ends a pass. (D480, D607)
- **Loop-owned machinery.** Language rules, prompts, history and reminders are the loop's; a problem supplies harness, judge, cost and target. (D514, D515, D516)
- **Improve ladder.** Register sweeps, depth passes, contenders, redesigns; the budget grows while it improves; pipelining cuts on delay-weighted levels. (D496, D499, D506, D517, D527)
- **Nothing oversized is built.** `py2sv.cost` (about 3 units per um2) over 2,000 makes it cheaper first; designs carry `prototype_sha`; a passing but unspellable prototype is not verified. (D613, D615, D618, D619)
- **Agent-sized prototype passes.** A coding agent gets 8 attempts, +2 per new best, at most 20 (one pass ran 59 turns in 1 h 50 m). (D943)

## Models, coding agents, skills and flux ask

- **One model home.** `flux_llm`; every measurement names its model tag. (D200)
- **One Proposer protocol.** `propose(...) -> Reply`; hosted models are Flux's endpoint, model and key (`FLUX_REMOTE_*`), read once per machine from `~/.config/flux/flux.env`. (D469, D508, D651, D817)
- **The model is checked first.** `preflight()` asks `/v1/models`; `task check` says ready or NOT READY. (D623)
- **Thinking, streaming, tool rounds.** Replies stream live; a thinking loop is re-asked with thinking off; a round may write half the window; a second parse failure ends a turn as unusable. (D469, D493, D494, D503, D544, D596)
- **Optional agentic halves.** In-turn tools, an agent orchestrator, an agent-written plan. (D505)
- **A coding agent in any box.** Headless, brief on stdin (a file past 100 KB); the loop checks its `out.json`, refuses once, then falls back; questions follow `questions: decide|model|operator`; `select` only breaks ties. (D575, D585, D630, D640, D642, D670, D671, D672)
- **An agent writes the prototype or the target.** A silent turn is nudged twice at most; an outgrown session continues fresh. (D618, D643)
- **Agents write, the loop runs.** Compilers, simulators, synthesis, `make`, `pytest`, `bash` are denied; `python3` and Icarus stay; `allow:` gives commands back. (D673, D674, D685)
- **`flux probe`.** The problem's own gate and stages inside a turn within a budget (`probe: {gate: 20, stages: 3}`): add8 2.96 um2 with probes, 3.135 without. (D678, D679)
- **One session per job.** A repair resumes the part's session (427 chars against an 11,854-char brief); decision boxes take `session: turn|pass`. (D669)
- **The workbench.** `workbench/` beside the document, kept across runs, linked into every agent's folder, never read by the loop; scratch is the container's `/tmp`. (D677, D764, D790)
- **A running agent shows what it does.** Model, status, rate limit, stderr and its steps (words, thinking, tool calls) live; every turn's steps, tokens and cost in `turns.jsonl`; it may read the loop around it. (D668, D675, D676, D694, D696, D711, D712)
- **An agent registry.** Built-in opencode, claude, codex and admin-added agents (name, kind, program, login, arguments, home files, hosts), usable by name in any box (`FLUX_AGENTS`); each gets only its own variables (`FLUX_<NAME>_ENV`), never another kind's. (D718, D807, D847, D893, D934, D945)
- **One resolver.** `flux_loop.agent_env` maps `FLUX_<NAME>_BASE_URL/_API_KEY/_MODEL/_OAUTH_TOKEN` to each kind's names from any source, ranks scopes, flags conflicts, never shows values; `FLUX_<NAME>_TIMEOUT_S` limits a turn unless the document says. (D893, D922)
- **Ready means tested.** `flux agent test` (program, login or key, a live FLUX-OK); starts, authoring and asks need a passed test whose configuration hash matches; retested daily; an admin's test of the server configuration counts for users who inherit it. (D751, D807, D923, D935)
- **Codex.** `exec --json` (reply, session resume, steps, tokens); `danger-full-access` inside Flux's container, where its bubblewrap cannot run; its program's folder is mounted for its helpers. (D749, D750, D848)
- **Claude Code subscriptions.** `claude setup-token` output is kept as an encrypted user setting, masked in the transcript. (D748)
- **A turn owns its processes.** Its process group ends with it (TERM, KILL after 3 s); a program that cannot start is a failed turn. (D768, D854)
- **`flux ask` and `flux consult`.** A model or agent writes and checks a document, then the loop runs it; `consult` answers about a loop from a read-only snapshot into `answer.md`. (D586, D589, D627, D705)
- **Skills.** SKILL.md copied into agents' folders or inlined; `skills/flux/SKILL.md` teaches outside agents. (D588, D592)

## Stages, measurement and calibration

- **Every measurement is a command.** A stage prints `name=value`; cutoffs (all must pass, saying which cut what) prune escalation, never eligibility; the decision reads the highest stage with results. (D448, D454, D466, D657, D954)
- **Estimates are pre-gates, off by default.** `estimate: {kind, margin}` may only save a tool run; costly/cheap ratios are the loop's calibration. (D464, D560, D665)
- **`rtl.py measure`.** One Yosys and one OpenROAD call on ORFS's gzipped ASAP7 files (1.1 s, against 17 s / 49 s through ORFS's Makefile); stages `stat`, `synth`, `place`; area unrounded; reports fmax, area, power, cells, `worst_slack_ps`, `flow_depth` and the critical path. (D225, D229, D564, D626, D662, D870, D948, D950, D956)
- **One copy per application.** Edit `applications/mul8/rtl.py`, `scripts/sync-app-tools.py` copies it; the NLU's operators use a four-line launcher. (D453, D567, D579, D582, D948, D956)
- **Timing paths are data.** Parsed by `flux_loop.timing` into every report and offered as the `timing` tool. (D440, D526, D950)
- **One evidence identity.** The cache key is the candidate plus the stage's command, metrics, regexes, knobs read, `{part}`, home files, params, the inputs' fingerprint and tool builds; it is the cache's key and each row's `measured_as`; always on, sidecar `<id>.json`. (D19, D344, D436, D567, D634, D790, D853, D898)
- **The ABI holds what a row stores.** `Result` and its parts, `run_tool` and toolchain fingerprints; metrics through `value_of`/`estimate_of`; a missing metric is a named refusal. (D140, D169, D201, D426, D440, D441, D451, D958)
- **Parallel measurement, serial bookkeeping.** Threads measure, the loop thread records; an admitted part is measured while the next drafts; OpenROAD threads = cores / 4, at most 16. (D525, D563)
- **Tools are bounded.** `run_tool` runs a process group within its timeout and kills the group on timeout; `timeout_s` per stage; a failure keeps its error text; a tool's task streams its output. (D537, D709, D854)
- **Thin adapters.** Tools run as subprocesses; Flux's own code is about 0.1% of wall time, so no native rewrite. (D540)
- **`flux prog time|count|size`.** hyperfine, cachegrind (deterministic) or `size` on a program built per candidate. (D661)
- **Nightly bands.** OpenROAD numbers are pinned to bands holding for both tool generations (area 300-440 um2); a number leaving its band is read, not widened. (D860)

## The record, reports and running campaigns

- **One SQLite file, intent before evaluation.** A trial's intent commits first; the trial number is taken in the insert. (D217, D264, D524, D747)
- **The record is named by the id.** `<parent>/<child>` for a sub-loop; a record asked for in a missing folder is made; schema v2 and later. (D524, D628, D633, D931)
- **Trust by version.** Rows carry provenance; an admitted design is kept while its judge (gate, the inputs' fingerprint, tool fingerprints, Flux revision) and transpiler are today's, else re-verified once. (D471, D510, D778, D853, D864)
- **Resume reads the record back.** Best configurations seed pool and prompt; `Candidate.from_record` is the one row reader, `records.fresh` the one staleness test. (D367, D476, D886)
- **Record readers.** `flux_records`: typed rows, one `record_read_back`, `controlled_pairs` for laws and duels; modules sit by role. (D397, D401, D444, D445, D565)
- **`flux report`.** One HTML page: frontier with hypervolume, best-so-far, passes, pairwise fronts. (D512, D523, D609)
- **Operating a run.** Under `<home>/out/`; `flux status`, `stop`, `run`, `attach` via `<record>.runs.json`; turns in `turns.jsonl` (`flux log`); `flux_profile` accounts the time. (D295, D510, D513, D578, D597, D599)

## Knowledge, mining and operator feedback

- **Knowledge is retrieved context, not training.** (D3)
- **Licence first.** A standard only after a primary-source licence check; each source has a `PROVENANCE.md`. (D31)
- **Retrieval.** BM25 over the whole corpus before filtering; one paragraph splitter; each chunk rendered once. (D164, D180, D244, D443)
- **Sources and budget.** Corpus, Library, RecordReadback, Notes, Mined, bounded by `budget.knowledge_share`; lessons are `knowledge: {lessons: mined|claude}`. (D449, D462, D548, D629, D796)
- **A loop's library is `library/`.** Beside the document, plus the shared library (`FLUX_LIBRARY`); `flux ask` copies attachments there; the mentor and agents' briefs lead with it; a draft records what it cited. (D407, D648, D650, D735, D736, D737, D791)
- **Every paper is digested once.** In the Setup, by the model or `knowledge: {digest: claude}`, at most 8 a pass, the loop's own first, three failures stop it; an agent reads `paper.txt`; a fragment under 80 characters fails; only loops that prompt digest, and a stop does not wait. (D477, D576, D753, D771, D773, D774, D782, D785, D791, D793, D830)
- **Digests per home.** Kept in `~/.cache/flux/digests` by content and recipe, so another loop takes them without a call. (D794)
- **Mined facts are typed.** Computed from rows with fixed wording and a `not_established` boundary; one-knob pairs give directions (withheld under two pairs), duels give head-to-heads. (D243, D297, D369, D400, D633)
- **Operator notes.** Advisory HUMAN GUIDANCE drained by every role; the web's `runs/inbox.jsonl` joins the terminal's, an agent's question is answered by the next note, a removal is a `{"forget": id}` line. (D388, D398, D403, D684, D808)

## The applications

Every application is re-run after loop changes, and the docs quote those runs. (D557, D610)

- **gelu_fp16.** FP16 GELU within 1 ULP as a formula, a 2-cycle py2sv pipeline; its deep stage is `place`. (D619, D863, D864)
- **Small examples.** adder16 (one `arch` knob of six, 2900 MHz at 333 ps), mul8 (exhaustive gate, 1600 MHz), primes (fresh-process median bench with cheats refused); adder16 and mul8 read no library. (D598, D610, D865, D866, D867, D874)
- **nlu.** Seven operator sub-loops, each a `golden.py` true on all 65,536 inputs against a 60-digit reference; Claude drafts each from `knowledge/fp16_blocks.py`; all seven admitted by the generic path. (D484, D495, D802, D804, D862)
- **prefetcher.** The model writes Bingo's `.ini`; `bingo.py check` refuses what knobs.md and the contract forbid; screen 2M+10M, confirm 2M+15M (traces are 17.7-35.8M); `geomean_speedup` with `keep: 0.9`, then storage including each partner's modelled tables; `invent.problem.yaml` writes a C++ prefetcher. (D349, D351, D362, D637, D788, D872, D873)
- **macarray.** A document and `flux_macarray.steps`: multiplier x reducer x pipeline (48 points plus invented multipliers), the PE checked on 236 rows and a multiplier on every input; precision from its own `workload.yaml`. (D228, D365, D798, D868, D871, D959)
- **bankmap.** `orchestrate: {command}` runs the chain: modulo, pigeonhole proofs, z3 over XOR folds with joint wiring, then model rounds; interconnects are stage lists; the artifact is a Verilated `bankmap` module. (D356, D363, D364, D372, D402, D799, D876)
- **interconnect_mapping.** Bank hashes against 12 storage modes, injectivity exact, train/holdout; little loops under a coordinating one as command rounds; Yosys + STA at 600 MHz as a limit; one balanced pick. (D378, D380, D382, D383, D385, D386, D393, D800, D878)

## The command line, the TUI, docs, packaging and tests

- **One dev shell, tools from Nix.** nixchip's tools, ICSC, ORFS with KLayout (`orfs`, run serially) and ripgrep; `VERILATOR_BIN` unset after the vars hook. (D636, D645, D656, D661, D933, D944, D947)
- **Install and scaffold.** `pip install -e "./flux[web]"`; `flux new NAME` writes the baseline folder; `flux example KIND NAME` the worked examples. (D598, D600, D608, D825, D930)
- **`flux selftest`.** Tools, a sweep, the model, a model-written problem, an agent; PASS/FAIL/SKIP. (D623)
- **CLI behaviour.** Real exit codes, one-line errors, clear refusals; authored documents go to operator review. (D587, D590, D627)
- **Where things live.** `flux_loop/document/` (a package by concern), `task.py` plus five mixins. (D558, D612, D890, D891)
- **Comments and docs.** The non-obvious why then `(Dnnn)`; one home per topic; no hand-kept counts. (D195, D246, D612, D620, D632)
- **Docs and site.** `docs/tutorial.md`, `docs/models.md`; MkDocs under `website/`, built `--strict`, kept short. (D387, D531, D590, D644, D679, D844)
- **The configurator.** `crafter.js`, six steps (Prompt, Check & Measure, Objective, Graph, Extra, Save) in the app's look, one line of purpose per step; `fromDoc` inverts `buildYaml`, every repository document round-trips with its meaning; an agent with settings is `agent:*`; the Digest box writes `flow.knowledge`. (D686, D719, D728, D781, D784, D826, D828, D880, D910, D911, D941, D946)
- **TUI.** stdlib curses on an event bus; a resize never ends a run; `q` stops after the pass, twice now; task and timing tabs; rows carry their content. (D390, D391, D392, D393, D404, D418, D532, D546, D547, D574, D621)
- **TUI results and forms.** Results lay out by row kind and are rebuilt only on change (149 to 0.1 ms a frame); fields keep typed Unicode and scroll to the cursor; a failed run says why. (D497, D498, D573, D857, D902, D903, D904)
- **CI.** Per push ruff, unit core, a no-Nix pip job whose commands a test parses; nightly heavy tests, integration (a per-file table of skips) and OpenROAD; failures named in annotations. (D155, D207, D230, D242, D246, D247, D371, D590, D600, D626, D638, D831, D858, D881)
- **Test hygiene.** Discovery floors, links and anchors checked, `parents[N]` paths, `TYPE_CHECKING` imports; signals restored after each test. (D123, D195, D334, D335, D619, D639, D777)
- **Test environment.** `FLUX_TMPDIR` becomes `TMPDIR`; the unit core runs under `/dev/shm` (145 s to 48 s). (D123, D405, D715)
- **Tests count, not clock.** Overlap and handshakes, not sleeps. (D692, D710)
- **The browser test and `tests/check.py`.** `tests/e2e/web_ui.py` walks every page in headless Firefox (`FLUX_E2E_STEPS` for a few); `check.py` runs ruff, unit, heavy and e2e side by side in about 150 s. (D710, D821, D822, D894, D949)
- **Fast core by default.** Tests over 25 s are `heavy`; a golden campaign pins the loop; `test_loop_live.py` runs each document. (D374, D531, D559, D628, D639)

## The sandbox

- **Every run is boxed.** `task run|check`, `ask`, `consult`, logins and Tests in rootless Podman (Docker otherwise), on by default; `--read-only`, tmpfs `/tmp`, `--cap-drop ALL`, no-new-privileges, a pids limit. (D680, D682, D704, D714, D716)
- **Only the loop's own folders write.** The loop's folder is read-only; `out/`, `workbench/` and its cache are writable; a sub-loop mounts its top folder; admins add per-loop mounts. (D763, D764, D805, D936)
- **A home per user.** `<data>/users/<name>/home` mounted as HOME, started from `home_seed` (an agent's config, a corporate build's parts) before a run, its cache inside; a shared loop runs in its owner's home. (D681, D744, D746, D760, D765, D769)
- **Secrets stay off the command line.** Variables go in a 0600 `--env-file`; secret-looking names are dropped unless passed; agents' own-folder variables are dropped. (D687, D697, D745, D847)
- **Network open or allowlisted.** Under an allowlist `--network none` and a relay to a host proxy, chained through a corporate proxy with its certificates; a start adds no hosts. (D680, D698, D722, D884)
- **Refusals are audited, the list is not shown.** DNS goes through the relay, each refused name is logged once per run. (D708, D716, D717, D720)
- **Nothing outlives its purpose.** Login and Test containers get Podman's `--timeout`; the reaper removes orphaned containers. (D768)
- **Podman's TMPDIR is local.** conmon's console socket fails on a FUSE TMPDIR. (D848)
- **Tested against a rogue loop.** A live containment test and a policy test that the writable surface is exactly the loop's folders. (D851)

## The web interface (flux serve)

- **The server.** FastAPI, plain JavaScript modules without a build; SQLite for users, sessions, settings and the audit; `X-Flux: 1` on every change; routes in groups behind central guards; every route's guard tested. (D683, D694, D719, D810, D888, D889, D892)
- **A loop runs or not.** One record per loop, a start resumes it, one live run; a start is reserved in one transaction. (D683, D688, D689, D854)
- **The journal grows with structure.** `events.jsonl` holds starts, ends and marks; live fields in `live.json`; first looks are 30 passes and 24 MB at most via `marks.jsonl`; log lines are stamped with date and time. (D732, D759, D761, D762, D816)
- **Pages never wait.** One stream per loop page, only while shown; slow work off the event loop (`/api/me` 300 ms to 3 ms); the latest start one indexed query; cold admin pages without slow probes; transcripts and Timeline read incrementally. (D774, D779, D780, D917, D918, D921)
- **Only the latest request draws.** A tab checks `still()` after each wait; drafts survive refreshes. (D919)
- **Inside the loop.** Reads and writes resolve inside the loop or its cache with O_NOFOLLOW; replacements renamed into place; web document loads confined. (D852, D905)
- **Users, shares, admins.** Watch or edit shares, a shared loop running on its owner's agents; admins edit anyone's loop; internal and external users; invite and reset links; five failures lock a name. (D687, D699, D701, D702, D734, D769, D812, D818, D942)
- **Agents and models.** A tab per tool for the admin and each user: installation, connection, verification, model, variables, prices (USD per million, a user's only with their own endpoint, priced once); inherited values show "From Server". (D684, D696, D697, D705, D721, D756, D814, D823, D835, D841, D855, D896, D924, D925)
- **Settings save as they change.** A check mark, not "saved"; no password manager fills them. (D820, D833, D939)
- **A loop's page.** Overview, Live, Results (and Graphs, and Library), Agents, Files, Settings; Talk from every tab. (D689, D690, D692, D693, D696, D699, D713, D723, D758, D916, D955)
- **Live › Tasks is the loop's tree.** Setup, a branch per pass of crafter boxes, End; short names; a drawing with a step bar over the boxes' runs; ended work folds; a killed run's tasks are interrupted. (D726, D727, D729, D730, D731, D739, D742, D752, D755, D928)
- **Results and graphs.** Designs ranked by the objectives' rule; one point per design; best-so-far and front count only feasible designs of one scope; part colours fixed. (D809, D837, D849, D901, D914, D915)
- **Timeline.** Per kind: calls, average, longest, total and share. (D772)
- **Making a loop.** Empty loop, Configurator, Upload, Agent, Clone a loop; creating never replaces; one draft across the ways. (D700, D703, D704, D824, D825, D906, D912, D913, D913b)
- **Files is a file manager.** Rename, move, delete on the selected item; dotfiles count as ignored; loop parts bold; big inputs walked once. (D829, D834, D836, D907, D908, D937)
- **Errors are said.** Why a start stopped, a document refused before saving. (D757)
- **Ask.** A question runs `flux consult` into `runs/asks/<id>/` with the owner's settings and sandbox. (D705, D713)
- **Page mechanics.** Toasts and dialogs, breadcrumbs, a bell per user, light and dark themes, a highlighter that builds no HTML; a page draws only while it is the latest navigation. (D688, D691, D700, D702, D719)
- **Admin.** Loops (pause, stop all, migrate, notify), Insights and audit (four sub-tabs, one interval), Applications, Resources (token rates), Sandbox (stderr masks), Agents and models, Users, Maintenance (scheduled tasks; scratch only where safe). (D695, D724, D733, D766, D819, D838, D846, D850, D885, D920, D940)
- **Usable anywhere.** No sideways scroll on a phone; 40 px touch targets; sortable headers with a fixed arrow slot; every action by keyboard; short headings. (D754, D856, D859, D883, D926, D927, D929, D938)

## Removed

- **MCP, CHIA, omni.** Dropped from the production loop. (D591)
- **Worlds and hooks.** `world:`, `hooks:`, the hook `CONTRACT`; what they did is a command beside the document. (D519, D561, D803)
- **flux_nlu.** Its world, FP16 toolkit, instrument tools, table oracle and transpiler (4,577 lines). (D420, D530, D802)
- **macarray's world.** Area mode `preserve_fmax` and its in-process study. (D366, D798, D957)
- **Old document keys.** `cache:`, `workbench:`, `joiner`, `max_parts`, `knowledge.library`, `digest: true`, `brief`, `extract`, `dse`, `llm`, `id:`, `workload:`. (D786, D790, D791, D792, D795, D796, D797, D954)
- **The RTL harness `rtl_tools`.** 17 files per application, wrappers included. (D118, D948)
- **`flux_evaluator_openroad`.** OpenROAD only via `rtl.py`. (D950)
- **Redaction, `flux gc`, `flux knowledge`.** The PDK guard refused nothing. (D94, D952)
- **Dead code.** Unused helpers, stores and stream routes. (D887, D953)
- **The Architecture-IR evaluation world.** `flux import/eval/replay`, the registry, `flux_evaluator_rtl`, evaluator stages and `workload:`, `flux_calibration`. (D98, D106, D110, D122, D954)
- **Application dead code.** Mapping-IR translators, `ChampSimEvaluator`, macarray's study. (D957)
- **npu_gemm, ZigZag, Timeloop.** And the ABI's adapter side. (D877, D958)
- **The IR package.** `core/ir`, `docs/ir.md`, golden files. (D959)
- **Web leftovers.** The Example tab, prompt banner, Other providers tab, folder panel, start hosts. (D767, D770, D817, D827, D884)

## Since the fold

New decisions go here, one terse bullet each.
- **The decision log is condensed.** 281 KB of paragraphs became one terse point per decision, grouped by topic, with every D-number the code cites kept and removed features under Removed; new decisions are one bullet here. (D960)
- **No evaluator package.** What was left of the evaluator ABI moved to its users: `Result` and its parts to `flux_store.result` (the record format), `run_tool` to `flux_loop.toolrun`, the toolchain fingerprints to `flux_loop.toolchain`, the measurement cache to `flux_loop.measure_cache`; `evaluator/` and two packages are gone, stored records read the same. (D961)
- **Lint is Verilator itself.** The catalog's lint check runs `verilator --lint-only` with lint and style warnings off and the six hardware-defect classes on (latch, multiple drivers, combinational loop, blocking/non-blocking mix, implicit net); exit 0 passes, so `rtl.py` has no lint command. It also restores the pre-D948 rule that width and style warnings are not defects. (D962)
- **Applications are usage examples.** interconnect_mapping (a research study with its own 2.4k-line library) and gelu_fp16 (one operator of what the NLU shows in full) are removed; primes, adder16, mul8, nlu, prefetcher, macarray and bankmap remain, and rtl.py has one copy fewer. (D963)
- **A Result is metrics and provenance.** `Result` keeps each metric's value and method and the stage that produced it; intervals, validity, domain, bottleneck, escalation and the placeholder `workload_hash`/`arch_hash`/`mapping_hash`/`usd_cost` columns are gone, an older row reads back with those keys ignored, and an older record keeps its columns (a writer fills them empty: dropping them rewrote a 1.5 GB record in 7 minutes). (D964)
- **Fewer docs.** measurement.md, stores.md and calibration.md are records.md; design-agent-loop.md is a section of agent-surface.md; the web interface left usage-guide.md for web.md, organised by page. (D965)
