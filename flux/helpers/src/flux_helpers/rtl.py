"""RTL helpers on ASAP7 (D968): lint with Verilator, measure with OpenROAD-flow-scripts (ORFS).

    fluxhelper rtl lint DESIGN.sv                      # hardware defects: "N failing"; exit 0, 1, 3 (no parse)
    fluxhelper rtl measure DESIGN.sv --stage stat|synth|place|route --clock-ps 1000
    # one line: fmax_mhz=... area_um2=... power_w=... cell_count=... path_ps=... (stat: area_um2, cell_count)

measure runs ORFS to the stage asked: stat is Yosys's mapped cells; synth times the netlist
unplaced (ORFS's floorplan); place after detailed placement; route after routing (timing from
global routing, plus drc_errors and wirelength_um from the detailed router). A module without
`clk` is timed input to output. Needs yosys, openroad, sta and ORFS's flow/ folder (FLOW_HOME, or
the Flux dev shell's ORFS); lint needs verilator. The golden test is `fluxhelper golden`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

#: Verilator's hardware defects only (D962): latches, multiple drivers, combinational loops,
#: blocking/non-blocking mixes, implicit nets; width and style warnings are not defects
LINT = ["verilator", "--lint-only", "-Wno-lint", "-Wno-style", "-Wwarn-LATCH", "-Wwarn-MULTIDRIVEN", "-Wwarn-UNOPTFLAT",
        "-Wwarn-COMBDLY", "-Wwarn-BLKANDNBLK", "-Wwarn-IMPLICIT"]

#: --stage -> (the ORFS target, its metrics file, the metrics' prefix)
STAGES = {"stat": ("synth", "1_synth", "synth"), "synth": ("floorplan", "2_1_floorplan", "floorplan"),
          "place": ("place", "3_5_place_dp", "detailedplace"), "route": ("route", "5_1_grt", "globalroute")}


def catalog() -> list[dict]:
    """This helper's catalog entries (D968): lint, and a measurement per stage."""
    from ._entries import HDL, metrics, param

    def stage(depth: str, title: str, what: str, reports: tuple[str, ...]) -> dict:
        return {"id": f"rtl-{depth}", "role": "stage", "title": title, "what": what,
                "run": f"fluxhelper rtl measure {{artifact}} --stage {depth} --clock-ps {{clock_ps}}",
                "params": {"clock_ps": param("Clock period" + (" (the mapper's target)" if depth == "stat" else ""), 1000, "ps")},
                "metrics": metrics(*reports), "needs": ["yosys", "openroad", "make"] + ([] if depth == "stat" else ["sta"]),
                "languages": HDL, "kinds": ["rtl"], "short": depth}
    timed = ("fmax_mhz", "area_um2", "power_w", "cell_count", "path_ps")
    return [{"id": "rtl-lint", "role": "check", "title": "Lint for hardware defects",
             "what": "Verilator's hardware defects only: latches, multiple drivers, combinational loops, blocking/non-blocking "
                     "mixes, implicit nets; width and style warnings are not defects. It also refuses what does not parse.",
             "run": "fluxhelper rtl lint {artifact}", "params": {}, "needs": ["verilator"],
             "pass": "passes when it prints `0 failing`; exit 3 = did not parse", "languages": HDL, "kinds": ["rtl"], "short": "lint"},
            stage("stat", "Area and cells (ASAP7)", "OpenROAD-flow-scripts' synthesis: the mapped cells' area; nothing timed, "
                  "the cheapest screen.", ("area_um2", "cell_count")),
            stage("synth", "Synthesise and time (ASAP7)", "OpenROAD-flow-scripts to its floorplan: the netlist timed unplaced, "
                  "seconds.", timed),
            stage("place", "Place and time (ASAP7)", "OpenROAD-flow-scripts to detailed placement, timed with wire estimates: "
                  "tens of seconds.", timed),
            stage("route", "Route and time (ASAP7)", "OpenROAD-flow-scripts to routing: timing after global routing, minutes.",
                  timed)]


def flow_home() -> Path:
    for home in (os.environ.get("FLOW_HOME", ""),
                 os.environ.get("OPENROAD_FLOW_SCRIPTS_HOME", "") + "/share/openroad-flow-scripts/flow"):
        if home and (Path(home) / "Makefile").is_file():
            return Path(home)
    raise SystemExit("ORFS not found: set FLOW_HOME to OpenROAD-flow-scripts' flow/ folder (the Flux dev shell has it)")


def measure(source: str, module: str | None = None, *, stage: str = "synth", clock_ps: float = 1000.0,
            timeout_s: float = 1800.0, utilization: float = 40) -> dict[str, Any]:
    module = module or re.search(r"^\s*module\s+(\w+)", source, re.M).group(1)
    head = re.search(rf"\bmodule\s+{module}\b(.*?);", source, re.S)
    ports = set(re.findall(r"\w+", head.group(1))) if head else set()
    target, metrics, key = STAGES[stage]
    flow = flow_home()
    with tempfile.TemporaryDirectory(prefix="flux-rtl-") as d:
        work = Path(d)
        (work / "design.sv").write_text(re.sub(r"\bendmodule\s*;", "endmodule", source) + "\n")
        (work / "design.sdc").write_text(
            f"create_clock -name clk -period {clock_ps}" + (" [get_ports clk]\n" if "clk" in ports else "\n")
            + f"set_input_delay 0 -clock clk [{'all_inputs -no_clocks' if 'clk' in ports else 'all_inputs'}]\n"
            + "set_output_delay 0 -clock clk [all_outputs]\n" + ("set_false_path -from [get_ports rst_n]\n" if "rst_n" in ports else ""))
        (work / "config.mk").write_text(
            f"export PLATFORM = asap7\nexport CORNER = TC\nexport DESIGN_NAME = {module}\nexport DESIGN_NICKNAME = d\n"
            f"export VERILOG_FILES = {work}/design.sv\nexport SDC_FILE = {work}/design.sdc\n"
            f"export CORE_UTILIZATION = {utilization:g}\nexport PLACE_DENSITY = 0.60\n")
        # ORFS's make is slow in a big environment (each of its shells inherits it): a small one
        env = {"HOME": os.environ.get("HOME", d), "PATH": os.environ["PATH"], "QT_QPA_PLATFORM": "offscreen",
               "YOSYS_EXE": shutil.which("yosys") or "yosys", "OPENROAD_EXE": shutil.which("openroad") or "openroad",
               "OPENSTA_EXE": shutil.which("sta") or "sta"}
        make = ["make", "-f", f"{flow}/Makefile", f"FLOW_HOME={flow}", f"WORK_HOME={work}", f"DESIGN_CONFIG={work}/config.mk"]
        netlist = work / "results/asap7/d/base/1_2_yosys.v"
        for step in (str(netlist), target):
            r = subprocess.run(make + [step], cwd=work, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=timeout_s)        # ABC waits on an open stdin
            if r.returncode != 0:
                raise SystemExit(f"ORFS {step} failed:\n{(r.stdout + r.stderr)[-1500:]}")
            if step != target:     # OpenROAD refuses `input signed [7:0] a;` (STA-0171); mapped, it says nothing
                netlist.write_text(re.sub(r"^(\s*(?:input|output|wire))\s+signed\b", r"\1", netlist.read_text(), flags=re.M))
        logs = work / "logs/asap7/d/base"
        m = json.loads((logs / f"{metrics}.json").read_text())
        routed = json.loads((logs / "5_2_route.json").read_text()) if stage == "route" else {}
    got: dict[str, Any] = {"area_um2": m[f"{key}__design__instance__area__stdcell"],
                           "cell_count": m[f"{key}__design__instance__count__stdcell"]}
    if stage == "stat":
        return got
    slack = float(m[f"{key}__timing__setup__ws"])
    path = clock_ps - slack
    got = {"fmax_mhz": 1e6 / path if path > 0 else float("inf"), **got, "power_w": m.get(f"{key}__power__total", float("nan")),
           "path_ps": path, "worst_slack_ps": slack, "clock_period_ps": clock_ps,
           "flow_depth": {"synth": "synthesis", "place": "placement", "route": "routing"}[stage]}
    if routed:
        got |= {"drc_errors": routed["detailedroute__route__drc_errors"], "wirelength_um": routed["detailedroute__route__wirelength"]}
    return got


def lint(path: str, timeout_s: float = 120.0) -> tuple[int, list[str]]:
    """(exit code, the lines to print): each defect a line, then `N failing`; 3 when it does not parse."""
    r = subprocess.run([*LINT, path], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout_s)
    said = (r.stdout + r.stderr).splitlines()
    defects = [ln for ln in said if ln.startswith("%Warning-")]
    errors = [ln for ln in said if ln.startswith("%Error") and not ln.startswith("%Error: Exiting")]
    if errors and not defects:
        return 3, ["did not parse: " + errors[0], "1 failing"]
    if r.returncode != 0 and not defects:
        return 1, [*(said[-5:] or ["verilator failed"]), "1 failing"]
    return (1 if defects else 0), [*defects, f"{len(defects)} failing"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxhelper rtl", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    li = sub.add_parser("lint", help="Verilator's hardware defects only: N failing; exit 3 when it does not parse")
    li.add_argument("artifact")
    li.add_argument("--timeout", type=float, default=120.0)
    me = sub.add_parser("measure", help="ORFS on ASAP7 to --stage: fmax, area, power, cells")
    me.add_argument("artifact")
    me.add_argument("--stage", choices=STAGES, default="synth")
    me.add_argument("--clock-ps", type=float, default=1000.0)
    me.add_argument("--module")
    me.add_argument("--utilization", type=float, default=40)
    me.add_argument("--timeout", type=float, default=1800.0)
    a = ap.parse_args(argv)
    if a.command == "lint":
        code, lines = lint(a.artifact, a.timeout)
        print("\n".join(lines))
        return code
    got = measure(Path(a.artifact).read_text(), a.module, stage=a.stage, clock_ps=a.clock_ps, timeout_s=a.timeout,
                  utilization=a.utilization)
    print(" ".join(f"{k}={v:.6g}" if isinstance(v, float) else f"{k}={v}" for k, v in got.items() if k != "flow_depth")
          + f" stage={a.stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
