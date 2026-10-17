# Presets

Flux's own checks and measurements. A document runs one as `fluxpreset NAME ...`; nothing is
copied into the loop's folder. `flux tools` lists the catalog entries below, and the configurator
offers them as a check's **Type** or a measurement's **Tool** under **Flux presets**. In a shell,
`fluxpreset` alone lists the presets and `fluxpreset NAME --help` describes one.

Each entry gives its title, catalog id and role, the command a document writes, and its
parameters with their defaults. A `{name}` in a command that is not one of the
[loop's placeholders](commands.md#command-placeholders) is a parameter.

## golden

The golden test for RTL: the design against a golden model's vectors. It prints the failing vectors and `N failing of M`, and exits 0 (pass), 1 (fail) or 3 (did not compile). `--extra F.sv` adds a module the design instantiates. The golden model's format is [below](#golden-model).

```text
fluxpreset golden DESIGN --golden golden.py [--extra F.sv] [--module M]
```

- **Test against a golden model** (`rtl-golden`, a check): `fluxpreset golden {artifact} --golden {golden}`  
  Verilator runs the module on golden.py's vectors and counts the wrong outputs. Parameters: `golden` (`{home}/golden.py`). Reports `N failing`. Needs `verilator`.

## rtl

Hardware checks and measurements on the ASAP7 process: lint with Verilator, measure with OpenROAD-flow-scripts. `rtl measure` runs the flow to the stage asked and prints one line, `fmax_mhz=... area_um2=... power_w=... cell_count=... path_ps=... worst_slack_ps=...` (`stat` only area and cells; `route` adds `drc_errors` and `wirelength_um`).

```text
fluxpreset rtl lint DESIGN
fluxpreset rtl measure DESIGN --stage stat|synth|place|route --clock-ps N
fluxpreset rtl cycles DESIGN --golden golden.py
fluxpreset rtl equiv DESIGN --reference ref.sv
fluxpreset rtl breakdown DESIGN
fluxpreset rtl fmax DESIGN --stage synth [--steps 6]
fluxpreset rtl power DESIGN --golden golden.py --stage place
fluxpreset rtl energy DESIGN --golden golden.py --stage place
fluxpreset rtl path DESIGN --stage place
```

- **Lint for hardware defects** (`rtl-lint`, a check): `fluxpreset rtl lint {artifact}`  
  Verilator's hardware defects only: latches, multiple drivers, combinational loops, blocking/non-blocking mixes, implicit nets; width and style warnings are not defects. It also refuses what does not parse. Reports `N failing`. Needs `verilator`.
- **Area and cells (ASAP7)** (`rtl-stat`, a stage): `fluxpreset rtl measure {artifact} --stage stat --clock-ps {clock_ps}`  
  OpenROAD-flow-scripts' synthesis: the mapped cells' area; nothing timed, the cheapest screen. Parameters: `clock_ps` (`1000`). Reports `area_um2`, `cell_count`. Needs `yosys`, `openroad`, `make`.
- **Synthesise and time (ASAP7)** (`rtl-synth`, a stage): `fluxpreset rtl measure {artifact} --stage synth --clock-ps {clock_ps}`  
  OpenROAD-flow-scripts to its floorplan: the netlist timed unplaced, seconds. Parameters: `clock_ps` (`1000`). Reports `fmax_mhz`, `area_um2`, `power_w`, `cell_count`, `path_ps`. Needs `yosys`, `openroad`, `make`, `sta`.
- **Place and time (ASAP7)** (`rtl-place`, a stage): `fluxpreset rtl measure {artifact} --stage place --clock-ps {clock_ps}`  
  OpenROAD-flow-scripts to detailed placement, timed with wire estimates: tens of seconds. Parameters: `clock_ps` (`1000`). Reports `fmax_mhz`, `area_um2`, `power_w`, `cell_count`, `path_ps`. Needs `yosys`, `openroad`, `make`, `sta`.
- **Route and time (ASAP7)** (`rtl-route`, a stage): `fluxpreset rtl measure {artifact} --stage route --clock-ps {clock_ps}`  
  OpenROAD-flow-scripts to routing: timing after global routing, minutes. Parameters: `clock_ps` (`1000`). Reports `fmax_mhz`, `area_um2`, `power_w`, `cell_count`, `path_ps`. Needs `yosys`, `openroad`, `make`, `sta`.
- **Clock cycles (Verilator)** (`rtl-cycles`, a stage): `fluxpreset rtl cycles {artifact} --golden {golden}`  
  A clocked design run on golden.py's vectors, each started when the last is done: its latency in cycles (the most, the fewest) and the mean cycles per result. Parameters: `golden` (`{home}/golden.py`). Reports `latency_cycles`, `latency_min_cycles`, `cycles_per_result`. Needs `verilator`.
- **Equal to a reference module** (`rtl-equiv`, a check): `fluxpreset rtl equiv {artifact} --reference {reference}`  
  Formal equivalence with Yosys (a miter of the two, proved by SAT; a clocked pair by induction): passes when they are equal for every input, else prints a counterexample. Parameters: `reference` (`{home}/reference.sv`). Reports `N failing`. Needs `yosys`.
- **Where the area goes (ASAP7)** (`rtl-breakdown`, a stage): `fluxpreset rtl breakdown {artifact} --clock-ps {clock_ps}`  
  OpenROAD-flow-scripts' synthesis, its cells grouped: sequential, adders, XORs, buffers and inverters, the rest of the logic; and the flops. Parameters: `clock_ps` (`1000`). Reports `area_seq_um2`, `area_arith_um2`, `area_xor_um2`, `area_buf_um2`, `area_logic_um2`, `flop_count`, `area_um2`. Needs `yosys`, `openroad`, `make`.
- **Fastest clock that closes (ASAP7)** (`rtl-fmax`, a stage): `fluxpreset rtl fmax {artifact} --stage {stage} --clock-ps {clock_ps} --steps {steps}`  
  A few OpenROAD-flow-scripts runs, each at the period the last one's slack says, until the fastest clock that closes timing at that stage is found: costly, and the honest fmax. Parameters: `stage` (`synth`), `clock_ps` (`1000`), `steps` (`6`). Reports `fmax_closed_mhz`, `closed_period_ps`. Needs `yosys`, `openroad`, `sta`, `make`.
- **Power from the design's own switching (ASAP7)** (`rtl-power`, a stage): `fluxpreset rtl power {artifact} --golden {golden} --stage {stage} --clock-ps {clock_ps}`  
  Verilator runs golden.py's vectors and records the switching; OpenROAD reads it on the synthesised or placed design and reports power: internal, switching, leakage. Parameters: `golden` (`{home}/golden.py`), `stage` (`place`), `clock_ps` (`1000`). Reports `power_w`, `power_internal_w`, `power_switching_w`, `power_leakage_w`. Needs `verilator`, `yosys`, `openroad`, `sta`, `make`.
- **Energy per result (ASAP7)** (`rtl-energy`, a stage): `fluxpreset rtl energy {artifact} --golden {golden} --stage {stage} --clock-ps {clock_ps}`  
  The power the golden vectors' own switching draws, times the time one result takes (its measured cycles at the clock): energy per result and energy-delay product. Parameters: `golden` (`{home}/golden.py`), `stage` (`place`), `clock_ps` (`1000`). Reports `energy_pj_per_result`, `edp_pj_ns`, `power_w`, `cycles_per_result`. Needs `verilator`, `yosys`, `openroad`, `sta`, `make`.
- **The critical path's anatomy (ASAP7)** (`rtl-path`, a stage): `fluxpreset rtl path {artifact} --stage {stage} --clock-ps {clock_ps}`  
  OpenROAD's worst path at the stage: how many cells it crosses, and how much of its delay is in the cells and how much in the wires -- whether to cut logic or wire. Parameters: `stage` (`place`), `clock_ps` (`1000`). Reports `path_ps`, `path_levels`, `path_cell_ps`, `path_wire_ps`, `slack_ps`. Needs `yosys`, `openroad`, `sta`, `make`.

## systemc

A SystemC design as SystemVerilog: ICSC translates it, so every RTL preset can judge it. Alone it checks that the module translates; `--then "golden --golden g.py"` or `--then "rtl measure --stage stat"` runs that preset on the translation.

```text
fluxpreset systemc DESIGN.h --top M --golden golden.py [--then "golden --golden golden.py"]
```

- **SystemC translates to RTL (ICSC)** (`systemc-translate`, a check): `fluxpreset systemc {artifact} --top {top} --golden {golden}`  
  ICSC translates the SystemC module to SystemVerilog: passes when it does, and when the ports are the golden model's. Parameters: `top` (`top`), `golden` (`{home}/golden.py`). Reports `N failing`.
- **SystemC against a golden model** (`systemc-golden`, a check): `fluxpreset systemc {artifact} --top {top} --golden {golden} --then "golden --golden {golden}"`  
  ICSC translates the SystemC module, then Verilator runs the RTL on golden.py's vectors. Parameters: `top` (`top`), `golden` (`{home}/golden.py`). Reports `N failing`. Needs `verilator`.

## cases

The golden test for Python: the design's function called on cases and compared with the expected value or a reference function's (floats within `--tol`). Cases come from a JSON table (`[args]` or `{"args", "kwargs", "expected"}`), the reference module's `CASES`, and `--random N` draws of its `inputs(rng)`.

```text
fluxpreset cases DESIGN.py --call F [--reference ref.py[:F]] [--cases cases.json] [--random N] [--tol R]
```

- **Compare with a reference function** (`python-cases`, a check): `fluxpreset cases {artifact} --call {call} --reference {reference} --random {random}`  
  Calls the design's function on cases (a JSON table, the reference's CASES, random inputs from its inputs(rng)) and compares each result with the reference function's or the expected value. Parameters: `call` (`main`), `reference` (`{home}/reference.py`), `random` (`100`). Reports `N failing`.

## ccases

`cases` for a C or C++ function: the design built as a shared library and called through ctypes with a scalar `--sig`, against a Python reference.

```text
fluxpreset ccases DESIGN.c --call F --sig "long(long,long)" --reference ref.py:F --random N
```

- **Compare a C function with a reference** (`c-cases`, a check): `fluxpreset ccases {artifact} --call {call} --sig {sig} --reference {reference} --random {random}`  
  Builds the design as a shared library and calls one function (a scalar signature) on cases, each result compared with a Python reference function's or the expected value. Parameters: `call` (`main`), `sig` (`int(int)`), `reference` (`{home}/reference.py`), `random` (`100`). Reports `N failing`. Needs `cc`.

## pytest

Your pytest file against the design: the design is copied as `design.py` beside your tests, and the failing tests are counted.

```text
fluxpreset pytest DESIGN.py --tests test_design.py` (the design imported as `design`)
```

- **Run your pytest file** (`python-pytest`, a check): `fluxpreset pytest {artifact} --tests {tests}`  
  Runs your pytest file with the design importable as `design`; counts the failing tests. Parameters: `tests` (`{home}/test_design.py`). Reports `N failing`.

## accuracy

How far an approximation is from its reference, as a measurement, not pass or fail. `max_ulp` counts float64 steps.

```text
fluxpreset accuracy DESIGN.py --call F --reference ref.py:F --random N
```

- **Accuracy against a reference** (`python-accuracy`, a stage): `fluxpreset accuracy {artifact} --call {call} --reference {reference} --random {random}`  
  Calls the design's function and a reference on the same cases and reports how far apart they are: absolute, relative and ULP error. Parameters: `call` (`main`), `reference` (`{home}/reference.py`), `random` (`1000`). Reports `max_abs_error`, `mean_abs_error`, `max_rel_error`, `mean_rel_error`, `max_ulp`.

## timebound

A time budget as a check: a Python function slower than `--max-ms` (the fastest of `--runs`) fails before the costly benchmark runs. A run stuck past the budget is stopped.

```text
fluxpreset timebound DESIGN.py --call F --args "(1000,)" --max-ms 50
```

- **Within a time budget** (`python-timebound`, a check): `fluxpreset timebound {artifact} --call {call} --args {args} --max-ms {max_ms}`  
  Times one call of the design's function on a large input and fails a design slower than the budget, before the costly benchmark. Parameters: `call` (`main`), `args` (`()`), `max_ms` (`1000`). Reports `N failing`.

## bench

Time a Python function: the design loaded, `f(*args)` timed over `--runs` after `--warmup`. `time_ms` is the median. Exit 3 when the design does not load, 1 when it raises.

```text
fluxpreset bench DESIGN.py --call F --args "(1000,)" [--runs 5] [--warmup 1]
```

- **Time a Python function** (`python-bench`, a stage): `fluxpreset bench {artifact} --call {call} --args {args} --runs {runs}`  
  Loads the design, calls one function with your arguments, times it over several runs. Parameters: `call` (`main`), `args` (`()`), `runs` (`5`). Reports `time_ms`.

## prog

Build a program with `--build` (`{out}` is the program it builds), then time, count, size or watch it. It prints `name=value` lines and exits 3 when the build fails. Older documents said `flux prog`; `flux task migrate` rewrites it.

```text
fluxpreset prog time|count|memory|size --build "c++ -O2 -o {out} {artifact}" [--run CMD]
```

- **Program size** (`prog-size`, a stage): `fluxpreset prog size --build "{build}"`  
  Builds the program and reads its sections with `size`: text, data and bss bytes. Parameters: `build` (`c++ -O2 -o {out} {artifact}`). Reports `text_bytes`, `data_bytes`, `bss_bytes`. Needs `size`.
- **Program run time** (`prog-time`, a stage): `fluxpreset prog time --build "{build}" --run "{run}" --runs {runs} --warmup {warmup}`  
  Builds the program and times it over several runs (hyperfine when present). Parameters: `build` (`c++ -O2 -o {out} {artifact}`), `run`, `runs` (`10`), `warmup` (`1`). Reports `time_ms`, `time_ms_stddev`, `time_ms_min`.
- **Instruction count (Valgrind)** (`prog-count`, a stage): `fluxpreset prog count --build "{build}" --run "{run}"`  
  Builds the program and counts instructions and cache misses with Valgrind's cachegrind: deterministic. Parameters: `build` (`c++ -O2 -o {out} {artifact}`), `run`. Reports `instructions`, `d1_misses`, `ll_misses`, `branch_mispredicts`. Needs `valgrind`.
- **Program peak memory** (`prog-memory`, a stage): `fluxpreset prog memory --build "{build}" --run "{run}" --runs {runs}`  
  Builds the program and runs it: its peak resident memory as the kernel counts it, the most over the runs. Parameters: `build` (`c++ -O2 -o {out} {artifact}`), `run`, `runs` (`3`). Reports `peak_rss_kb`.

## sanitize

A C or C++ program built with AddressSanitizer and UndefinedBehaviorSanitizer and run: each distinct memory error, leak or undefined behaviour it hits is a failure.

```text
fluxpreset sanitize --build "cc -O1 -o {out} {artifact}" [--run CMD]
```

- **Memory and undefined-behaviour errors** (`c-sanitize`, a check): `fluxpreset sanitize --build "{build}" --run "{run}"`  
  Builds the program with AddressSanitizer and UndefinedBehaviorSanitizer and runs it: each memory error, leak or undefined behaviour it hits is a failure. Parameters: `build` (`cc -O1 -o {out} {artifact}`), `run`. Reports `N failing`. Needs `cc`.

## complexity

How complex a design's source is, read and not run, in any common language (lizard). With `--max-cyclomatic N` it fails each function over N.

```text
fluxpreset complexity DESIGN [--ext .c] [--max-cyclomatic 15]
```

- **Source complexity** (`complexity`, a stage): `fluxpreset complexity {artifact}`  
  Reads the design's source (any common language) and counts its lines of code, functions, cyclomatic complexity and tokens; nothing is built or run. Reports `loc`, `functions`, `cyclomatic_max`, `cyclomatic_mean`, `tokens`, `max_function_loc`.
- **No function too complex** (`complexity-limit`, a check): `fluxpreset complexity {artifact} --max-cyclomatic {max_cyclomatic}`  
  Fails each function whose cyclomatic complexity is over the limit. Parameters: `max_cyclomatic` (`15`). Reports `N failing`.

## scaling

How a command's time grows with the problem size: timed at doubling sizes (`{n}`; `--input "gen {n}"` feeds its stdin, `--build` makes `{out}` first), start-up time taken off, fitted as time ~ n^k. `scaling_exponent` is about 1 for linear, 2 for quadratic. With `--max-exponent K` it is a check.

```text
fluxpreset scaling [--start 1000|--sizes A,B,C] [--input "gen {n}"] [--build "cc -o {out} {artifact}"] [--max-exponent K] -- CMD {n}
```

- **How time grows with size** (`scaling`, a stage): `fluxpreset scaling --start {start} -- {command}`  
  Times the program at doubling problem sizes and fits time ~ n^k: the exponent says how it scales (1 linear, 2 quadratic), the constant factor aside. Parameters: `start` (`1000`), `command` (`{artifact} {n}`). Reports `scaling_exponent`, `scaling_r2`, `time_at_max_ms`, `max_n`.
- **Scales no worse than n^k** (`scaling-limit`, a check): `fluxpreset scaling --start {start} --max-exponent {max_exponent} -- {command}`  
  Times the program at doubling sizes and fails when time grows faster than n to the given power. Parameters: `start` (`1000`), `command` (`{artifact} {n}`), `max_exponent` (`1.5`). Reports `N failing`.

## repeat

Any measurement run several times with its noise taken out: each metric's median and `NAME_spread_pct`.

```text
fluxpreset repeat --n 5 [--warmup 1] -- CMD
```

- **Median of several runs** (`repeat`, a stage): `fluxpreset repeat --n {n} -- {command}`  
  Runs any measurement several times and prints each metric's median and its spread, so one noisy run does not decide. Parameters: `n` (`5`), `command`. Reports your `name=value` lines.

## derive

Metrics made from other metrics: a command's own numbers plus formulas over them (arithmetic, `min`, `max`, `abs`, `sqrt`, `log`, `exp`).

```text
fluxpreset derive --expr NAME=EXPR [--expr ...] -- CMD
```

- **Metrics from formulas** (`derive`, a stage): `fluxpreset derive --expr {expr} -- {command}`  
  Runs any measurement and adds metrics computed from its numbers, such as area times delay or throughput per watt. Parameters: `expr` (`adp=area_um2*path_ps`), `command`. Reports your `name=value` lines.

## regress

A performance gate: a measurement run again and compared with a recorded baseline file. `--record` writes the file, `--tol` is the allowed percent, `--higher` names metrics where more is better.

```text
fluxpreset regress --baseline base.json [--tol 5] [--record] -- CMD
```

- **No worse than a baseline** (`regress`, a check): `fluxpreset regress --baseline {baseline} --tol {tol} -- {command}`  
  Runs any measurement again and fails when a metric is worse than the recorded baseline by more than the tolerance. Parameters: `baseline` (`{home}/baseline.json`), `tol` (`5`), `command`. Reports `N failing`.

## extract

Any tool's output as `name=value`, so a tool that does not speak Flux needs no script: JSON, CSV or text, picked with `--pick PATH=name` (a dotted path, a column or a regex group).

```text
fluxpreset extract [--format json|csv|regex] --pick PATH=name -- CMD
```

- **Numbers out of any tool's output** (`extract`, a stage): `fluxpreset extract --format {format} --pick {pick} -- {command}`  
  Runs your tool and reads its JSON, CSV or text output, printing the numbers you pick as name=value. Parameters: `format` (`json`), `pick` (`value=metric`), `command`. Reports your `name=value` lines.

## champsim

A cache prefetcher in ChampSim, on your traces. `stats` runs the prefetcher and the run without it on every trace and says why it wins: coverage (the share of misses removed), accuracy, lateness.

```text
fluxpreset champsim build|check|run|stats ARTIFACT --traces DIR [--warmup N --sim N]
```

- **Build the prefetcher into ChampSim** (`champsim-build`, a check): `fluxpreset champsim build {artifact}`  
  Compiles a prefetcher header into ChampSim; reports the first compiler error. Reports `N failing`. Needs `pythia`.
- **Smoke-run the prefetcher** (`champsim-check`, a check): `fluxpreset champsim check {artifact} --traces {traces}`  
  Builds the prefetcher and runs it on one trace; refused when it issues no prefetches. Parameters: `traces` (`{home}/traces`). Reports `N failing`. Needs `pythia`.
- **Simulate on your traces (ChampSim)** (`champsim-run`, a stage): `fluxpreset champsim run {artifact} --traces {traces} --warmup {warmup} --sim {sim}`  
  Runs an .ini or a prefetcher header on every trace; the geometric-mean IPC speed-up over no prefetcher. Parameters: `traces` (`{home}/traces`), `warmup` (`10000000`), `sim` (`15000000`). Reports `geomean_speedup`. Needs `pythia`.
- **Why the prefetcher wins (ChampSim)** (`champsim-stats`, a stage): `fluxpreset champsim stats {artifact} --traces {traces} --warmup {warmup} --sim {sim}`  
  Runs it and the run without it on every trace: L2 misses per kilo-instruction with and without it, the share of misses it removed (coverage), and how many of its prefetches were used, late or useless. Parameters: `traces` (`{home}/traces`), `warmup` (`10000000`), `sim` (`15000000`). Reports `l2_mpki`, `l2_mpki_baseline`, `pf_coverage`, `pf_accuracy`, `pf_late`, `pf_useless`, `pf_issued_pki`, `geomean_speedup`. Needs `pythia`.

## asl

A PTO-style spec's AVS tests, from a spec checkout kept in the loop's folder. `tests` lists a unit's tests (by id or mnemonic); `run` runs them with ASLRef as the spec's release suite does, on `--jobs` threads, each failing test a line then `N failing of M`. `--out FILE` writes each result as JSON. Needs `aslref` on PATH or `--aslref`.

```text
fluxpreset asl tests|run UNIT... --spec {home}/spec [--jobs N] [--out FILE]
```

- **The spec's AVS tests pass** (`asl-avs`, a check): `fluxpreset asl run {unit} --spec {home}/spec`  
  Runs a PTO-style spec unit's AVS tests with ASLRef, through the spec's own runner, from the spec kept in the application's folder. Each failing test a line, then the count. Parameters: `unit` (`TADD`). Reports `N failing`. Needs `aslref`.

## ndf

A PTO-style spec's normative clauses, read with the NDF tool. `list` gives each unit with its clauses by modality and its test count; `clauses UNIT` prints a unit's clauses; `scope UNIT` lists the unit and every unit that depends on it; `check UNIT --results FILE` reads `asl run --out` and counts each MUST clause failed or uncovered.

```text
fluxpreset ndf list|clauses|scope|check UNIT --spec {home}/spec
```

- **Every MUST clause covered and passing** (`ndf-contract`, a check): `fluxpreset ndf check {unit} --spec {home}/spec --results {results}`  
  Reads a PTO-style spec unit's NDF clauses (the NDF tool, from the spec in the application's folder) and the AVS results `fluxpreset asl run --out` wrote: each MUST clause that failed or has no test is a line, then the count. Parameters: `unit` (`TADD`), `results` (`avs.json`). Reports `N failing`. Needs `ndf`.

## pto

A pyCircuit design checked against a PTO-style spec's ASL model, unit by unit. The design is one combinational datapath for the spec's elementwise units, with ports `op` (8 bits, from `fluxpreset pto ops`), `dtype` (5), `a`, `b` (64), `y` (64) and `fault` (1). Each wrong vector is a line, then `N failing of M`; `--build-only` only builds, `--measure` prints `vectors_failing`, `units_passing`, `units`. Needs `pycircuit` and `aslref`.

```text
fluxpreset pto ops [--names]
fluxpreset pto ref UNIT...
fluxpreset pto check DESIGN --units UNIT... [--dependents] [--build-only|--measure]` (`--spec {home}/spec`)
```

- **The design matches the spec's ASL model** (`pto-check`, a check): `fluxpreset pto check {artifact} --units {units} --spec {home}/spec`  
  Builds a pyCircuit design (pycircuit, the C++ model) and drives it with each unit's reference vectors -- the spec's own answers from ASLRef, cached per spec -- one wrong vector a line, then the count. The interface: `fluxpreset pto ops`. Parameters: `units` (`all`). Reports `N failing`. Needs `pycircuit`, `aslref`.

## Without a preset

- **Test script** (`python-test-script`, a check): `{python} {script} {artifact}`  
  A Python script beside the document runs the design on known cases and prints `N failing`. Parameters: `script` (`{home}/check.py`). Reports `N failing`.
- **Any command** (`custom-check`, a check): `{command}`  
  Any command; it prints `N failing` (or say `count_re` / `fail_re`), or its exit code decides. Parameters: `command`. Reports `N failing`.
- **Timing script** (`bench-script`, a stage): `{python} {script} {artifact}`  
  A Python script beside the document runs the design and prints `time_ms=...`. Parameters: `script` (`{home}/bench.py`). Reports `time_ms`.
- **Any command** (`custom-stage`, a stage): `{command}`  
  Any command; it prints `name=value` for each metric it measures, listed as the stage's `metrics`. Parameters: `command`. Reports your `name=value` lines.

## One interface

Every preset keeps the same interface:

| | |
|---|---|
| Arguments | `fluxpreset NAME [SUBCOMMAND] ARTIFACT [--option VALUE ...]`: the design file first, every other input an option with a default |
| stdin | Never read |
| stdout | A check: each failure on a line of its own, then `N failing` (or `N failing of M`) last. A measurement: `name=value` for each metric it reports |
| stderr | Diagnostics for a person; never the count or the numbers |
| Exit code | `0` passed or measured; `1` failed, or the measurement could not be taken; `2` wrong arguments; `3` the design did not build, parse or load |
| Files | Only in a temporary folder of its own; never in the loop's folder |

## Writing a preset

A preset is a module `flux_presets/NAME.py` (or a package) with `main(argv) -> int` (its command
line) and `catalog() -> [entry]`: one catalog entry per check or stage it offers, its `run`
starting `fluxpreset NAME`. `flux tools`, the configurator's **Flux presets** list and the
loader's language and `needs` inference read those entries; nothing else in Flux names a preset.

## Golden model

`fluxpreset golden`, `rtl cycles`, `rtl power`, `rtl energy` and `systemc` read a golden model:
a Python file that defines `PORTS` and `golden(**inputs)`.

```python
PORTS = [
    {"name": "x", "dir": "in", "bits": 16, "unsigned": True},
    {"name": "y", "dir": "out", "bits": 16, "unsigned": True},
]

def golden(x):
    return {"y": x}
```

| Setting | Purpose |
|---|---|
| `COUNT` | Random vectors (default `32`), besides generated corner cases |
| `SEED` | Random seed |
| `VECTORS` | Extra input dictionaries; expected outputs still come from `golden()` |
| `EXHAUSTIVE` | Every input combination (at most 20 input bits) |
| `CLOCK` | The clock port of a clocked design, e.g. `"clk"` |
| `LATENCY` | Cycles from `start` to `done` for a clocked design |
| `TOLERANCE_ULP` | Map of floating-point output port to allowed ULP error; absent means exact |

Match the ports and clocking in the contract and the design. Floating-point bit-pattern ports
are unsigned; reinterpret their bits in `golden()` rather than converting the integer to a
float. A Python prototype is checked against the same file with
`python -m flux_loop.golden_proto PROTOTYPE.py --golden golden.py` (prints `N failing of M`).
