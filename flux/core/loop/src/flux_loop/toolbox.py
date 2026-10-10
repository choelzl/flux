"""The tool catalog (D654): the checks a gate may run and the stages a document may measure with,
in one place. `flux tools` prints it, `flux tools --json` writes it for the loop crafter
(website/docs/assets/tools.json, kept equal by a unit test).

Each entry: `id`, `role` ("check" or "stage"), `title` and `what` in plain words, `run` (the
command template: the loop's placeholders `{artifact}`, `{home}`, `{python}`, and the entry's
own `params` by name, which the author fills), `params` (name -> label, default, unit),
`metrics` (a stage's, name -> unit), `needs` (tools on PATH), `pass` (a check's pass rule),
`languages` (the artifacts it fits) and `kinds` (the crafter's kinds of problem it belongs to).
Every stage is a command (D954: no evaluator stages).
"""

from __future__ import annotations

import re
from typing import Any

from .objective import UNITS

__all__ = ["TOOLS", "catalog", "fill", "tool"]

_N_FAILING = "passes when it prints `0 failing`; exit 3 = did not build"
_HDL = ["systemverilog", "verilog"]


def _p(label: str, default: Any, unit: str = "") -> dict[str, Any]:
    return {"label": label, "default": default, "unit": unit}


def _m(*names: str) -> dict[str, str]:
    return {n: UNITS.get(n, "ps" if n.endswith("_ps") else "") for n in names}


#: `flux prog` (D661): a build writing {out} (kept apart from other candidates), then the run
_PROG = 'flux prog {sub} --build "{{build}}" --run "{{run}}"{more}'
_PROG_PARAMS = {"build": _p("Build command ({out}: the program)", "c++ -O2 -o {out} {artifact}"),
                "run": _p("Run command (empty: the built program)", "")}
_PROGS = ["c", "cpp", "python"]


def _rtl_stage(depth: str, title: str, what: str) -> dict[str, Any]:
    return {"id": f"rtl-{depth}", "role": "stage", "title": title, "what": what,
            "run": f"{{python}} {{home}}/rtl.py measure {{artifact}} --stage {depth} --clock-ps {{clock_ps}}",
            "params": {"clock_ps": _p("Clock period", 1000, "ps")},
            "metrics": _m("fmax_mhz", "area_um2", "power_w", "cell_count", "path_ps"),
            "needs": ["yosys", "openroad"], "languages": _HDL, "kinds": ["rtl"]}


TOOLS: list[dict[str, Any]] = [
    # ---- checks: a gate runs them in order, cheapest first (D652)
    {"id": "rtl-lint", "role": "check", "title": "Lint for hardware defects",
     "what": "Verilator itself, its hardware defects only (D962): latches, multiple drivers, combinational loops, "
             "blocking/non-blocking mixes, implicit nets; width and style warnings are not defects.",
     "run": "verilator --lint-only -Wno-lint -Wno-style -Wwarn-LATCH -Wwarn-MULTIDRIVEN -Wwarn-UNOPTFLAT -Wwarn-COMBDLY -Wwarn-BLKANDNBLK -Wwarn-IMPLICIT {artifact}", "params": {}, "needs": ["verilator"],
     "pass": "passes when it exits 0 (no defect, and it parses)", "languages": _HDL, "kinds": ["rtl"]},
    {"id": "rtl-golden", "role": "check", "title": "Test against a golden model",
     "what": "Verilator runs the module on golden.py's vectors and counts the wrong outputs.",
     "run": "{python} {home}/rtl.py test {artifact} --golden {golden}", "params": {"golden": _p("Golden model", "{home}/golden.py")},
     "needs": ["verilator"], "pass": _N_FAILING, "languages": _HDL, "kinds": ["rtl"]},
    {"id": "champsim-build", "role": "check", "title": "Build the prefetcher into ChampSim",
     "what": "Copy champsim.py and tools/ from applications/prefetcher: compiles a prefetcher header; reports the first compiler error.",
     "run": "{python} {home}/champsim.py build {artifact}", "params": {}, "needs": ["pythia"],
     "pass": "passes when it builds; exit 3 = did not build", "languages": ["cpp"], "kinds": ["champsim"]},
    {"id": "champsim-check", "role": "check", "title": "Smoke-run the prefetcher",
     "what": "Builds the prefetcher and runs it on one trace; refused when it issues no prefetches.",
     "run": "{python} {home}/champsim.py check {artifact} --traces {traces}", "params": {"traces": _p("Trace folder", "{home}/traces")},
     "needs": ["pythia"], "pass": _N_FAILING, "languages": ["cpp"], "kinds": ["champsim"]},
    {"id": "python-test-script", "role": "check", "title": "A test script of yours",
     "what": "A Python script beside the document runs the design on known cases and prints `N failing`.",
     "run": "{python} {script} {artifact}", "params": {"script": _p("Script", "{home}/check.py")},
     "needs": [], "pass": _N_FAILING, "languages": ["python", "text", "ini", "yaml", "c", "cpp"],
     "kinds": ["program", "python", "champsim", "own"]},
    {"id": "custom-check", "role": "check", "title": "Any command",
     "what": "Your own command; it prints `N failing` (or say `count_re` / `fail_re`), or its exit code decides.",
     "run": "{command}", "params": {"command": _p("Command", "")},
     "needs": [], "pass": "passes when it prints `0 failing`, or else exits 0; exit 3 = did not build",
     "languages": [], "kinds": ["own"]},
    # ---- stages: measurements, cheapest first; a `cutoff` is a stage's gate
    {"id": "rtl-stat", "role": "stage", "title": "Area and cells, Yosys alone (ASAP7)",
     "what": "Yosys maps to ASAP7 cells and sums their liberty area; nothing timed: a second or two, the cheapest screen.",
     "run": "{python} {home}/rtl.py measure {artifact} --stage stat --clock-ps {clock_ps}",
     "params": {"clock_ps": _p("Clock period (the mapper's target)", 1000, "ps")},
     "metrics": _m("area_um2", "cell_count"), "needs": ["yosys"], "languages": _HDL, "kinds": ["rtl"]},
    _rtl_stage("synth", "Synthesise and time (ASAP7)", "Yosys synthesis, timed by OpenROAD's OpenSTA: seconds per design."),
    _rtl_stage("place", "Place and time (ASAP7)", "OpenROAD placement, timed with wire estimates: tens of seconds."),
    _rtl_stage("route", "Route and time (ASAP7)", "OpenROAD placement and routing, the signoff numbers: minutes."),
    {"id": "champsim-run", "role": "stage", "title": "Simulate on your traces (ChampSim)",
     "what": "Runs an .ini or a prefetcher header on every trace; the geometric-mean IPC speed-up over no prefetcher.",
     "run": "{python} {home}/champsim.py run {artifact} --traces {traces} --warmup {warmup} --sim {sim}",
     "params": {"traces": _p("Trace folder", "{home}/traces"), "warmup": _p("Warm-up", 10_000_000, "instructions"),
                "sim": _p("Simulated", 15_000_000, "instructions")},
     "metrics": _m("geomean_speedup"), "needs": ["pythia"], "languages": ["ini", "cpp"], "kinds": ["champsim"]},
    {"id": "bench-script", "role": "stage", "title": "A timing script of yours",
     "what": "A Python script beside the document runs the design and prints `time_ms=...`.",
     "run": "{python} {script} {artifact}", "params": {"script": _p("Script", "{home}/bench.py")},
     "metrics": _m("time_ms"), "needs": [], "languages": ["python", "text", "c", "cpp"], "kinds": ["program", "python"]},
    {"id": "prog-size", "role": "stage", "title": "Size of the built program",
     "what": "Builds the program and reads its sections with `size`: code (text), initialised data, zeroed data (bss).",
     "run": 'flux prog size --build "{build}"', "params": {"build": _PROG_PARAMS["build"]},
     "metrics": _m("text_bytes", "data_bytes", "bss_bytes"), "needs": ["size"],
     "languages": ["c", "cpp"], "kinds": ["program"]},
    {"id": "prog-time", "role": "stage", "title": "Time the program (hyperfine)",
     "what": "Builds the program and times its runs after a warm-up (hyperfine, else a Python loop): the mean, "
             "the spread and the fastest. {out} is the built program; for Python leave the build empty and run `{python} {artifact}`.",
     "run": _PROG.format(sub="time", more=" --runs {runs} --warmup {warmup}"),
     "params": _PROG_PARAMS | {"runs": _p("Runs", 10, "runs"), "warmup": _p("Warm-up runs", 1, "runs")},
     "metrics": _m("time_ms", "time_ms_stddev", "time_ms_min"), "needs": [],
     "languages": _PROGS, "kinds": ["program", "python"]},
    {"id": "prog-count", "role": "stage", "title": "Count instructions and cache misses (Valgrind)",
     "what": "Builds the program and runs it under cachegrind: instructions, cache misses and branch mispredicts, "
             "the same every run. {out} is the built program; for Python leave the build empty and run `{python} {artifact}`.",
     "run": _PROG.format(sub="count", more=""), "params": _PROG_PARAMS,
     "metrics": _m("instructions", "d1_misses", "ll_misses", "branch_mispredicts"), "needs": ["valgrind"],
     "languages": _PROGS, "kinds": ["program", "python"]},
    {"id": "custom-stage", "role": "stage", "title": "Any command",
     "what": "Your own command; it prints `name=value` for each metric it measures, listed as the stage's `metrics`.",
     "run": "{command}", "params": {"command": _p("Command", "")},
     "metrics": {}, "needs": [], "languages": [], "kinds": ["own"]},
]


def catalog() -> list[dict[str, Any]]:
    """The catalog as `flux tools --json` prints it."""
    return [dict(t) for t in TOOLS]


def tool(tool_id: str) -> dict[str, Any]:
    found = next((t for t in TOOLS if t["id"] == tool_id), None)
    if found is None:
        raise KeyError(f"no tool {tool_id!r}; known: {', '.join(t['id'] for t in TOOLS)}")
    return found


def fill(tool_id: str, **params: Any) -> str:
    """A tool's command with its params filled (defaults for those not given); the loop's own
    placeholders stay for the loop."""
    t = tool(tool_id)
    if "run" not in t:
        raise ValueError(f"{tool_id} is an evaluator stage: its `stage` and `document` keys, not a command")
    values = {k: v["default"] for k, v in t["params"].items()} | params
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), t["run"])
