"""The tool catalog (D654): the checks a gate may run and the stages a document may measure with,
in one place. `flux tools` prints it, `flux tools --json` writes it for the loop crafter
(website/docs/assets/tools.json, kept equal by a unit test).

Each entry: `id`, `role` ("check" or "stage"), `title` and `what` in plain words, `run` (the
command template: the loop's placeholders `{artifact}`, `{home}`, `{python}`, and the entry's
own `params` by name, which the author fills), `params` (name -> label, default, unit),
`metrics` (a stage's, name -> unit), `needs` (tools on PATH), `pass` (a check's pass rule),
`languages` (the artifacts it fits) and `kinds` (the crafter's kinds of problem it belongs to).
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


def _rtl_stage(depth: str, title: str, what: str) -> dict[str, Any]:
    return {"id": f"rtl-{depth}", "role": "stage", "title": title, "what": what,
            "run": f"flux rtl measure {{artifact}} --stage {depth} --clock-ps {{clock_ps}}",
            "params": {"clock_ps": _p("Clock period", 1000, "ps")},
            "metrics": _m("fmax_mhz", "area_um2", "power_w", "cell_count", "path_ps"),
            "needs": ["yosys", "openroad"], "languages": _HDL, "kinds": ["rtl"]}


TOOLS: list[dict[str, Any]] = [
    # ---- checks: a gate runs them in order, cheapest first (D652)
    {"id": "rtl-lint", "role": "check", "title": "Lint for hardware defects",
     "what": "Verilator lint: latches, multiple drivers, combinational loops, `<=` in combinational logic, implicit nets.",
     "run": "flux rtl lint {artifact}", "params": {}, "needs": ["verilator"],
     "pass": "passes with no defect; exit 3 = does not parse", "languages": _HDL, "kinds": ["rtl"]},
    {"id": "rtl-golden", "role": "check", "title": "Test against a golden model",
     "what": "Verilator runs the module on golden.py's vectors and counts the wrong outputs.",
     "run": "flux rtl test {artifact} --golden {golden}", "params": {"golden": _p("Golden model", "{home}/golden.py")},
     "needs": ["verilator"], "pass": _N_FAILING, "languages": _HDL, "kinds": ["rtl"]},
    {"id": "champsim-build", "role": "check", "title": "Build the prefetcher into ChampSim",
     "what": "Compiles a prefetcher header into ChampSim; the first compiler error is the report.",
     "run": "flux champsim build {artifact}", "params": {}, "needs": ["pythia"],
     "pass": "passes when it builds; exit 3 = did not build", "languages": ["cpp"], "kinds": ["champsim"]},
    {"id": "champsim-check", "role": "check", "title": "Smoke-run the prefetcher",
     "what": "Builds the prefetcher and runs it on one trace; refused when it issues no prefetches.",
     "run": "flux champsim check {artifact} --traces {traces}", "params": {"traces": _p("Trace folder", "{home}/traces")},
     "needs": ["pythia"], "pass": _N_FAILING, "languages": ["cpp"], "kinds": ["champsim"]},
    {"id": "python-test-script", "role": "check", "title": "A test script of yours",
     "what": "A Python script beside the document runs the design on known cases and prints `N failing`.",
     "run": "{python} {script} {artifact}", "params": {"script": _p("Script", "{home}/check.py")},
     "needs": [], "pass": _N_FAILING, "languages": ["python", "text", "ini", "yaml", "c", "cpp"],
     "kinds": ["program", "python", "champsim", "zigzag", "own"]},
    {"id": "custom-check", "role": "check", "title": "Any command",
     "what": "Your own command; it prints `N failing` (or say `count_re` / `fail_re`), or its exit code decides.",
     "run": "{command}", "params": {"command": _p("Command", "")},
     "needs": [], "pass": "passes when it prints `0 failing`, or else exits 0; exit 3 = did not build",
     "languages": [], "kinds": ["own"]},
    # ---- stages: measurements, cheapest first; a `cutoff` is a stage's gate
    _rtl_stage("synth", "Synthesise and time (ASAP7)", "Yosys synthesis, timed by OpenROAD's OpenSTA: seconds per design."),
    _rtl_stage("place", "Place and time (ASAP7)", "OpenROAD placement, timed with wire estimates: tens of seconds."),
    _rtl_stage("route", "Route and time (ASAP7)", "OpenROAD placement and routing, the signoff numbers: minutes."),
    {"id": "champsim-run", "role": "stage", "title": "Simulate on your traces (ChampSim)",
     "what": "Runs an .ini or a prefetcher header on every trace; the geometric-mean IPC speed-up over no prefetcher.",
     "run": "flux champsim run {artifact} --traces {traces} --warmup {warmup} --sim {sim}",
     "params": {"traces": _p("Trace folder", "{home}/traces"), "warmup": _p("Warm-up", 10_000_000, "instructions"),
                "sim": _p("Simulated", 15_000_000, "instructions")},
     "metrics": _m("geomean_speedup"), "needs": ["pythia"], "languages": ["ini", "cpp"], "kinds": ["champsim"]},
    {"id": "bench-script", "role": "stage", "title": "A timing script of yours",
     "what": "A Python script beside the document runs the design and prints `time_ms=...`.",
     "run": "{python} {script} {artifact}", "params": {"script": _p("Script", "{home}/bench.py")},
     "metrics": _m("time_ms"), "needs": [], "languages": ["python", "text", "c", "cpp"], "kinds": ["program", "python"]},
    {"id": "zigzag-model", "role": "stage", "title": "Cost the architecture (ZigZag)",
     "what": "ZigZag maps the workload on the architecture for cycles and energy; the script adds an area estimate (as applications/npu_gemm).",
     "run": "{python} {script} {artifact} {workload}",
     "params": {"script": _p("Script", "{home}/measure.py"), "workload": _p("Workload", "{home}/workload.yaml")},
     "metrics": _m("latency_cycles", "energy_pj", "area_mm2"), "needs": [], "languages": ["yaml"], "kinds": ["zigzag"]},
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
    values = {k: v["default"] for k, v in t["params"].items()} | params
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), t["run"])
