# Writing a Flux problem document

A problem document (`problem.yaml`) is everything the design loop needs: what to make, how a
candidate is checked, how it is measured, what "better" means, and how the search goes. The
loop generates candidates (a model writes them, a script renders them from knobs, or a coding
agent writes them), gates each one, measures the survivors stage by stage and decides.

## Keys

Say only what is yours; the rest is inferred.

- `statement` (the ask in prose: the model reads it). The document is `problem.yaml` and has no
  `id`: the problem's id is its folder's name, which also names its record, so an edited
  document resumes it.
- `contract` (optional): rules every candidate must follow, in prose -- ports, naming, what is
  forbidden. The model reads it with the statement.
- `language`: the artifact's language (`systemverilog`, `verilog`, `python`, `c`, `cpp`,
  `cuda`, `text`, ...); the file extension follows from it.
- `flux tools` lists every check and stage Flux has, with its command and pass rule.
- `objectives`: a list of `{metric, direction: minimize|maximize, goal: N}`. Every one with a
  `goal` is a limit that must hold (at least N when maximizing, at most N when minimizing);
  among the designs meeting every limit, the ones without a goal decide in the order written
  ("fmax_mhz at least 1000, area_um2 at most 80, then least power_w"). `balance: true` on
  goal-less ones: the decision is their knee (the balance of fmax_mhz and area_um2). Nothing
  meets every limit: the closest wins, and the report says what it misses. A goal is judged on
  the deepest stage (`stage:` names another); a known metric has its unit (`unit:` for one
  Flux does not know). `{keep: 0.9, above: 1.0}` instead of a goal: a limit at 90% of the best
  measured design's gain over 1.0 (the smallest design that stays near the fastest).
- `flow`: each box of the loop -- who works it, and its own settings. Every key is optional:
  - `test`: how a candidate is refused -- a command (one string, or a list of tokens) that
    prints `N failing` or exits non-zero. Several checks, cheapest first, as a map by name like
    `measure`: `test: {lint: "flux rtl lint {artifact}", golden: {run: "flux rtl test {artifact} --golden {home}/golden.py", timeout_s: 120}}`.
    They run in the order written; the first that reports failures refuses the design ("failed at
    lint: ...") and the rest do not run. Exit 3 from any check means the design did not build. A
    check is its command, or `{run, count_re, fail_re, timeout_s}`: `count_re` (one integer
    group) or `fail_re` (one match per failure) for a checker that prints something else. A check
    named `build` refuses on any non-zero exit (did not build). Never an agent: it establishes the facts.
  - `measure`: the costed measurements, cheapest first, a map from each stage's name to its
    command (`screen: flux rtl measure {artifact} --stage synth --clock-ps 1000`) or its
    settings. A `flux rtl measure` stage needs nothing more: its metrics and tools are known, and
    it is skipped where a tool is missing. A command of your own prints `name=value` tokens and
    says `metrics:` (the names to read) and `needs:` (tools on PATH it requires), so it is
    `bench: {command: "...", metrics: [time_ms]}`. Every stage must measure every objective.
    `timeout_s` optional.
    A stage's gate is its `cutoff`: go on to the next stage only if `{metric, at: N}` (at least
    N), `{metric, below: N}` (at most N) or `{metric, within: 0.9}` (within 10% of this run's
    best). "Timing met" at 1 GHz is `cutoff: {metric: fmax_mhz, at: 1000}`. Several gates are a
    list, all must pass, in order: `cutoff: [{metric: fmax_mhz, at: 1000}, {metric: area_um2, below: 80}]`.
    A stage may be estimated before its tool runs (off by default):
    `estimate: {kind: surrogate|command|model, margin: 0.05}`. A design whose estimate fails the
    stage's cutoff or an objective's limit by more than `margin` (a fraction of the threshold) is
    skipped for that stage, and the report counts it; otherwise the tool runs. `surrogate` fits
    the record's rows on that stage (nothing until 3 are measured); `command: "..."` (kind command
    only) runs a script with the stage's placeholders that prints the same `name=value` metrics;
    `model` asks the model with the design and the stage's measured rows (no model, no estimate).
  - `orchestrate` with a space (D797) is the search over the design space -- `sweep|montecarlo|anneal|gradient|genetic|pareto|model` (the model proposes points)
    or a list of phases, or, with its space, `{policy: sweep, space: {...}, seeds: [...]}`.
    `space`: knob -> its choices, in a meaningful order. A knob that only matters for some
    choices of another: `{values: [...], when: {stack: [b, c]}}`; elsewhere it stays at its first
    choice and is not measured twice. A knob whose choices also are files beside the document:
    `{values: [a, b], from: "out/invented/*.sv"}` -- each file's name is one more choice, read at
    each load (D798). A component groups knobs: `bingo: {region_size: [...], ...}`
    is `bingo.region_size`; with `optional: true` the search also switches it on or off (`sms.on`),
    so it picks the combination. A generator reads the whole point from `{point}`, a JSON file
    with components nested. `seeds`: points measured before the walk, e.g. the shipped defaults,
    components nested; a knob left out is at its first choice. The first seed is home: a knob
    that does nothing (its component off) sits at its home value, and starts from it when
    switched on. A phase's `knobs`, `hold` and `keep` take globs (`bingo.*`, `"*.on"`).
    `pareto` needs two objectives.
  - `orchestrate: {command: "{python} {home}/search.py {history} {state} {params}"}` (D799): a
    search a command decides, round by round -- it reads `{history}` (what was measured and
    refused), keeps `{state}`, and prints one JSON object: `candidates` (name, artifact, knobs,
    why), `lessons`, `not_established`, a `conclusion` (kept when nothing passes), `done`.
    `{params}` is the document's `params:` as a JSON file, for any command.
  - `generate`: `model` (the default), `{command: "..."}` (a script renders each candidate; with a
    space, once per point, knobs as `{knob}`), `{by: opencode|claude|codex}` (with `bin`,
    `args` beside: another executable, extra arguments such as `[--agent, flux]`; per machine
    `FLUX_OPENCODE_BIN` / `FLUX_OPENCODE_ARGS`).
  - `knowledge`: what the model reads with every prompt -- `files: [...]` (specs, reference code,
    papers as PDF, notes, beside the document), `text: "..."` (inline notes), `by: opencode` (the
    library's papers digested by that coding agent instead of the model), or `knowledge: off` (no
    library at all). The operator's library (`mentor/knowledge/library/`) and the loop's own
    `library/` folder reach every document, each paper digested once: excerpts
    for the statement, contract and parts, one line per paper, and the coding agents' briefs.
  - `select: {finalists: N}`: how many designs reach the costliest stage.
  - `orchestrate: rules|given|model|tools` (`tools`: the model with tools), `plan: model`,
    `critique: model`, `validate: model`, `knowledge: {lessons: mined}` (lessons mined from the record, or `{lessons: claude}`),
    `feedback: off` (no operator notes), `calibrate: off`.
  - Every box says who works it the same way (D795): a word (`rules`, `model`, `off`, ...), an
    agent's name (`critique: claude`), or `{by: <who>, ...}` with the box's settings and the
    agent's options beside (`plan: {by: claude, session: pass}`, `select: {by: claude,
    finalists: 2}`, `knowledge: {by: opencode, files: [...]}`); an agent of your own is
    `by: {command: [...], output: text}`. A coding agent answers any box but test and measure,
    checked by the loop, falling back to the rules half (docs/design-agent-loop.md);
    `session: pass` keeps one agent session per box for the pass
    (resumed turn after turn), `session: turn` (the default) is a fresh agent every turn. A
    generate agent's span is fixed, not set: one session per part until the part is admitted
    (repairs and critique send-backs resume it with a short message; an improve starts fresh;
    `session` on generate is refused).
  `flow` is the only place a box is said: there is no top-level `gate:`, `stages:`, `space:`,
  `seeds:`, `knowledge:`, `roles:`, `generator:`, `critique:` or `decompose:` key.
  `parts: decompose` asks the orchestrator to divide the statement.
- `budget`, the knobs people change:
  - `steps` (work items per pass), `passes` (a cap; default: until stopped),
    `repair_attempts` (repairs per draft), `workers` (measurements at once; 1 for anything
    timed).
  - `prototype` (default on with a golden model): the model proves the algorithm first as
    Python `design(**inputs)` against every input, as a formula; the loop then spells it as
    RTL (or, for inputs over 20 bits, the model writes the RTL from it). Leave it on for numeric
    problems (floating point, transcendental functions, fixed point); `false` for plain logic
    (adders, muxes, counters).
    `prototype: systemc`: the prototype is a synthesizable `SC_MODULE` instead, checked on
    every golden vector against libsystemc; the model writes the RTL from it.
  - `prototype_table_max` (64): the largest module-level table, for coefficients only.
    `prototype_cost_max` (2,000, about 650 um2 on ASAP7; -1 for none): a prototype costing
    more is made cheaper first and never synthesised while over it.
- Advanced `budget` knobs, rarely needed: agent, ahead, budget_s, compact,
  compact_share, compute_timeout_s, cooldown_after, critique_rounds, explore, explore_every,
  hop_share, knowledge_share, max_depth, max_tolerance, parallel_parts, patch_context_lines,
  patching, plan_file, prototype_attempts, prototype_attempts_max, prototype_patience,
  prototype_shrink_attempts, prototype_unmeasured_stop, regenerate, regress_after,
  revert_after, screen_only, structured, tool_hops, tool_result_chars, tools.
- Advanced keys: `parts` (pieces of one artifact: their names in order, `[decoder, datapath]`,
  or a map from each name to what it is, `{decoder: "...", datapath: "..."}`, or `decompose`),
  `subtasks` (child problems, each its own loop: folders beside the document, `[ops/recip, ops/exp]`,
  each folder's `problem.yaml` saying only what differs -- its `flow`, `budget` and `params` merge key
  by key over the parent's, so a child that says `test:` keeps the parent's stages; the parent's
  `generate: {command: "... {parts} {artifact}"}` composes their answers into the whole, D801), `params` (the document's settings, any command reads them as `{params}`), `ladder`, `skills`, `workload`.

Placeholders in any command: `{artifact}` (the candidate's file), `{home}` (the document's
directory), `{workdir}`, `{name}`, `{python}`, and `{knob}` for each knob of `flow.orchestrate.space`. A command
starting with `flux` runs this Flux.

## The RTL tools

- `flux rtl lint {artifact}` -- Verilator lint for hardware defects (latches, multiple drivers,
  combinational loops, `<=` in combinational logic, mixed `=`/`<=`, implicit nets); prints each and
  `N failing`; exits 3 when it does not parse. Put it before the golden test.
- `flux rtl test {artifact} --golden {home}/golden.py` -- Verilator against a golden model;
  prints `N failing of M`; exits 3 when the module does not compile (the loop then treats it as
  a build failure, not a score). `--extra file.sv` for a leaf the module instantiates.
- `flux rtl measure {artifact} --stage synth|place|route --clock-ps P` -- Yosys and OpenROAD
  on ASAP7 (`synth` times the netlist with OpenROAD's OpenSTA; `place`, `route` lay it out); prints `fmax_mhz= area_um2= power_w=
  cell_count= path_ps=`. A module with a `clk` port is timed as clocked (and `rst_n` as its
  reset); `--repair-design` buffers long wires after placement.
- `flux rtl measure {artifact} --stage stat` -- Yosys alone: `area_um2= cell_count=`, nothing
  timed; the cheapest screen before `synth`.
- `golden.py` declares `PORTS = [{"name", "dir": "in"|"out", "bits", "unsigned": True?}]` and
  `def golden(**inputs) -> {output: value}`; optionally `COUNT` (random vectors, default 32),
  `SEED`, `VECTORS` (explicit rows), `CLOCK = "clk"` and `LATENCY` (cycles, checked) for a
  clocked design. Corners of every input, pairwise, are always tested.
- Every expected value comes out of `golden()` -- never a literal you worked out by hand.
  `VECTORS` lists inputs only (`[{"x": 0x3C00}, ...]`). Use exact references: `numpy.float16`
  for half precision -- the bits of an input are READ as a float with
  `float(np.uint16(x).view(np.float16))` (not `np.float16(x)`, which converts the number) and a
  result is written back with `int(np.float16(v).view(np.uint16))`; compute the function in full
  precision (`math`, float64) and round ONCE at the end. Ports that carry float bit patterns are
  `unsigned: True`.
- A float output may be off by a unit in the last place: `TOLERANCE_ULP = {"y": 1}` lets port
  `y` (16, 32 or 64 bits, IEEE) differ from the golden by that many representable values -- a
  hardware approximation is rarely bit-exact; without it the gate demands exact equality.
- About a thousand vectors (`COUNT = 1000`, the corners are added) keep each check under a
  minute; the test bench compiles every vector, so tens of thousands take minutes per repair.
- Combinational or clocked, say it once and the same way: a design with a clock needs
  `CLOCK = "clk"` and `LATENCY = n` in golden.py; without them the test bench drives only the
  golden's ports, and a module with a `clk` port cannot be tested.
- The document is checked by running your golden model once: it must import, make its
  vectors and return every output port for each.

## Other problems

The gate and the stages are any commands: a Python script you write beside the document
(`{python} {home}/check.py {artifact}` printing `N failing`), a test suite
(`{python} -m pytest -q {home}/tests --rootdir {home}` with a `fail_re`), a simulator, a
benchmark printing `name=value`. Write every script the document names.

A program (C, C++, Python) is measured by `flux prog`; `--build` is one quoted command writing
`{out}`, `--run` the command measured (empty: the built program; Python: `--run "{python} {artifact}"`):

- `flux prog time --build "c++ -O2 -o {out} {artifact}" --runs 10 --warmup 1` -- hyperfine:
  `time_ms=` (mean) `time_ms_stddev= time_ms_min=`.
- `flux prog count --build "c++ -O2 -o {out} {artifact}"` -- Valgrind cachegrind, the same every run:
  `instructions= d1_misses= ll_misses= branch_mispredicts=`.
- `flux prog size --build "c++ -O2 -o {out} {artifact}"` -- `text_bytes= data_bytes= bss_bytes=`.
- An architecture (`language: yaml`, Architecture IR) is costed by an evaluator stage:
  `{name: model, evaluator: zigzag, metrics: [latency_cycles, energy_pj]}` with a top-level
  `workload: "{home}/workload.yaml"` (Workload IR); `evaluator: timeloop` adds `area_mm2`.

## Rules

- Write `problem.yaml` and every file it names (golden model, scripts, tests) in the working
  directory. Do not write the design itself unless the ask gives it: the loop generates it.
- Keep what the ask says -- widths, names, targets -- exact. Where it is silent, choose
  sensibly and say so in `statement`.
- Every metric an objective names must be printed by a stage (or the gate).
