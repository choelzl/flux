"""RTL helpers on ASAP7 (D968): lint with Verilator, measure with OpenROAD-flow-scripts (ORFS).

    fluxhelper rtl lint DESIGN.sv                      # hardware defects: "N failing"; exit 0, 1, 3 (no parse)
    fluxhelper rtl cycles DESIGN.sv --golden golden.py # a clocked design: latency_cycles= cycles_per_result=
    fluxhelper rtl equiv DESIGN.sv --reference ref.sv  # formally equal: "0 failing", else a counterexample
    fluxhelper rtl breakdown DESIGN.sv                 # area_seq_um2= area_arith_um2= area_xor_um2= area_buf_um2= ...
    fluxhelper rtl fmax DESIGN.sv --stage synth        # fmax_closed_mhz= closed_period_ps= runs=
    fluxhelper rtl power DESIGN.sv --golden golden.py --stage place   # power_w= with the vectors' own switching
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
                  timed),
            {"id": "rtl-cycles", "role": "stage", "title": "Clock cycles (Verilator)",
             "what": "A clocked design run on golden.py's vectors, each started when the last is done: its latency in "
                     "cycles (the most, the fewest) and the mean cycles per result.",
             "run": "fluxhelper rtl cycles {artifact} --golden {golden}", "params": {"golden": param("Golden model", "{home}/golden.py")},
             "metrics": metrics("latency_cycles", "latency_min_cycles", "cycles_per_result"), "needs": ["verilator"],
             "languages": HDL, "kinds": ["rtl"], "short": "cycles"},
            {"id": "rtl-equiv", "role": "check", "title": "Equal to a reference module",
             "what": "Formal equivalence with Yosys (a miter of the two, proved by SAT; a clocked pair by induction): "
                     "passes when they are equal for every input, else prints a counterexample.",
             "run": "fluxhelper rtl equiv {artifact} --reference {reference}",
             "params": {"reference": param("Reference module", "{home}/reference.sv")}, "needs": ["yosys"],
             "pass": "passes when it prints `0 failing`; exit 3 = did not parse", "languages": HDL, "kinds": ["rtl"], "short": "equiv"},
            {"id": "rtl-breakdown", "role": "stage", "title": "Where the area goes (ASAP7)",
             "what": "OpenROAD-flow-scripts' synthesis, its cells grouped: sequential, adders, XORs, buffers and inverters, "
                     "the rest of the logic; and the flops.",
             "run": "fluxhelper rtl breakdown {artifact} --clock-ps {clock_ps}",
             "params": {"clock_ps": param("Clock period (the mapper's target)", 1000, "ps")},
             "metrics": metrics("area_seq_um2", "area_arith_um2", "area_xor_um2", "area_buf_um2", "area_logic_um2",
                                "flop_count", "area_um2"),
             "needs": ["yosys", "openroad", "make"], "languages": HDL, "kinds": ["rtl"], "short": "breakdown"},
            {"id": "rtl-fmax", "role": "stage", "title": "Fastest clock that closes (ASAP7)",
             "what": "A few OpenROAD-flow-scripts runs, each at the period the last one's slack says, until the fastest "
                     "clock that closes timing at that stage is found: costly, and the honest fmax.",
             "run": "fluxhelper rtl fmax {artifact} --stage {stage} --clock-ps {clock_ps} --steps {steps}",
             "params": {"stage": param("Stage (synth, place, route)", "synth"), "clock_ps": param("First period tried", 1000, "ps"),
                        "steps": param("Runs at most", 6)},
             "metrics": metrics("fmax_closed_mhz", "closed_period_ps"), "needs": ["yosys", "openroad", "sta", "make"],
             "languages": HDL, "kinds": ["rtl"], "short": "fmax"},
            {"id": "rtl-power", "role": "stage", "title": "Power from the design's own switching (ASAP7)",
             "what": "Verilator runs golden.py's vectors and records the switching; OpenROAD reads it on the synthesised or "
                     "placed design and reports power: internal, switching, leakage.",
             "run": "fluxhelper rtl power {artifact} --golden {golden} --stage {stage} --clock-ps {clock_ps}",
             "params": {"golden": param("Golden model", "{home}/golden.py"), "stage": param("Stage (synth, place)", "place"),
                        "clock_ps": param("Clock period", 1000, "ps")},
             "metrics": metrics("power_w", "power_internal_w", "power_switching_w", "power_leakage_w"),
             "needs": ["verilator", "yosys", "openroad", "sta", "make"], "languages": HDL, "kinds": ["rtl"], "short": "power"}]


def flow_home() -> Path:
    for home in (os.environ.get("FLOW_HOME", ""),
                 os.environ.get("OPENROAD_FLOW_SCRIPTS_HOME", "") + "/share/openroad-flow-scripts/flow"):
        if home and (Path(home) / "Makefile").is_file():
            return Path(home)
    raise SystemExit("ORFS not found: set FLOW_HOME to OpenROAD-flow-scripts' flow/ folder (the Flux dev shell has it)")


def _ports(source: str, module: str) -> set[str]:
    head = re.search(rf"\bmodule\s+{module}\b(.*?);", source, re.S)
    return set(re.findall(r"\w+", head.group(1))) if head else set()


def _orfs(work: Path, source: str, module: str, clock_ps: float, utilization: float, timeout_s: float):
    """ORFS set up in `work` for this design; returns run(target, *make_args) -> stdout. The first
    run builds the netlist and strips `signed` from its ports, which OpenROAD refuses (STA-0171)."""
    ports = _ports(source, module)
    flow = flow_home()
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
    env = {"HOME": os.environ.get("HOME", str(work)), "PATH": os.environ["PATH"], "QT_QPA_PLATFORM": "offscreen",
           "YOSYS_EXE": shutil.which("yosys") or "yosys", "OPENROAD_EXE": shutil.which("openroad") or "openroad",
           "OPENSTA_EXE": shutil.which("sta") or "sta"}
    make = ["make", "-f", f"{flow}/Makefile", f"FLOW_HOME={flow}", f"WORK_HOME={work}", f"DESIGN_CONFIG={work}/config.mk"]
    netlist = work / "results/asap7/d/base/1_2_yosys.v"

    def run(target: str, *args: str) -> str:
        for step in ([str(netlist)] if not netlist.exists() else []) + [target]:
            r = subprocess.run(make + list(args) + [step], cwd=work, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=timeout_s)          # ABC waits on an open stdin
            if r.returncode != 0:
                raise SystemExit(f"ORFS {step} failed:\n{(r.stdout + r.stderr)[-1500:]}")
            if step == str(netlist):
                netlist.write_text(re.sub(r"^(\s*(?:input|output|wire))\s+signed\b", r"\1", netlist.read_text(), flags=re.M))
        return r.stdout
    return run


def _module(source: str, module: str | None) -> str:
    m = module or (re.search(r"^\s*module\s+(\w+)", source, re.M) or [None, None])[1]
    if not m:
        raise SystemExit("no `module <name>` in the design; say --module")
    return m


def measure(source: str, module: str | None = None, *, stage: str = "synth", clock_ps: float = 1000.0,
            timeout_s: float = 1800.0, utilization: float = 40) -> dict[str, Any]:
    module = _module(source, module)
    target, metrics, key = STAGES[stage]
    with tempfile.TemporaryDirectory(prefix="flux-rtl-") as d:
        work = Path(d)
        _orfs(work, source, module, clock_ps, utilization, timeout_s)(target)
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


#: ASAP7 cell names -> where the area goes (D972)
_KINDS = (("seq", r"^(DFF|DHL|DLL|SDF|ICG|ASYNC)"), ("arith", r"^(FA|HA|MAJ)"), ("xor", r"^(XOR|XNOR)"),
          ("buf", r"^(INV|BUF|HB|TIE)"), ("logic", r""))


def breakdown(source: str, module: str | None = None, *, clock_ps: float = 1000.0, timeout_s: float = 1800.0) -> dict[str, Any]:
    """Where a synthesised design's area goes (D972): sequential, adders, XORs, buffers and
    inverters, the rest of the logic -- from ORFS's synthesis statistics, per cell type."""
    module = _module(source, module)
    with tempfile.TemporaryDirectory(prefix="flux-rtl-") as d:
        work = Path(d)
        _orfs(work, source, module, clock_ps, 40, timeout_s)("synth")
        stat = (work / "reports/asap7/d/base/synth_stat.txt").read_text()
    got = {f"area_{k}_um2": 0.0 for k, _ in _KINDS} | {"flop_count": 0, "cell_count": 0, "area_um2": 0.0}
    for count, area, cell in re.findall(r"^\s+(\d+)\s+([\d.]+)\s+\d+\s+[\d.]+\s+([A-Za-z]\w*_ASAP7\w*)\s*$", stat, re.M):
        kind = next(k for k, pat in _KINDS if re.match(pat, cell))
        got[f"area_{kind}_um2"] += float(area)
        got["cell_count"] += int(count)
        got["area_um2"] += float(area)
        got["flop_count"] += int(count) if kind == "seq" else 0
    return got


def fmax(source: str, module: str | None = None, *, stage: str = "synth", clock_ps: float = 1000.0, steps: int = 6,
         timeout_s: float = 1800.0, utilization: float = 40) -> dict[str, Any]:
    """The fastest clock that closes timing (D972): each run aims at the period the last one's
    slack says, closing (slack >= 0) or not, until two periods within 1% or `steps` runs."""
    closed, open_, period, runs = float("inf"), 0.0, clock_ps, 0
    while runs < steps:
        got = measure(source, module, stage=stage, clock_ps=period, timeout_s=timeout_s, utilization=utilization)
        runs += 1
        if got["worst_slack_ps"] >= 0:
            closed = min(closed, period)
        else:
            open_ = max(open_, period)
        guess = got["path_ps"] * (1.01 if got["worst_slack_ps"] < 0 else 0.99)
        nxt = guess if open_ < guess < closed else (open_ + closed) / 2 if closed < float("inf") else got["path_ps"] * 1.1
        if closed < float("inf") and closed - open_ <= 0.01 * closed or abs(nxt - period) <= 0.005 * period:
            break
        period = nxt
    if closed == float("inf"):
        raise SystemExit(f"no clock closed in {runs} runs (the last aimed at {period:.0f} ps)")
    return {"fmax_closed_mhz": 1e6 / closed, "closed_period_ps": closed, "runs": runs}


def power(source: str, golden: str, module: str | None = None, *, stage: str = "place", clock_ps: float = 1000.0,
          timeout_s: float = 1800.0) -> dict[str, Any]:
    """Power with the golden vectors' own switching (D972): Verilator runs them and writes a VCD, and
    OpenROAD reads it on the design ORFS made (`synth` or `place`) before reporting power."""
    from .golden import check_rtl, load_golden

    module = _module(source, module)
    odb = {"synth": "1_synth", "place": "3_place"}[stage]
    with tempfile.TemporaryDirectory(prefix="flux-rtl-") as d:
        work = Path(d)
        vcd = work / "activity.vcd"
        checked = check_rtl(source, load_golden(golden), module=module, timeout_s=timeout_s, vcd=vcd)
        if not checked.ok:
            raise SystemExit("a design that fails its golden vectors has no activity to measure: " + checked.why)
        run = _orfs(work, source, module, clock_ps, 40, timeout_s)
        run({"synth": "synth", "place": "place"}[stage])
        (work / "power.tcl").write_text(
            "source $::env(SCRIPTS_DIR)/load.tcl\n"
            f"load_design {odb}.odb {odb}.sdc\n"
            + ("estimate_parasitics -placement\n" if stage == "place" else "")
            + f"read_vcd -scope testbench/dut {vcd}\nreport_power\n")
        out = run("run", f"RUN_SCRIPT={work / 'power.tcl'}")
    total = re.search(r"^Total\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)", out, re.M)
    if not total:
        raise SystemExit(f"OpenROAD's power report is unreadable:\n{out[-1500:]}")
    internal, switching, leakage, all_ = (float(x) for x in total.groups())
    return {"power_w": all_, "power_internal_w": internal, "power_switching_w": switching, "power_leakage_w": leakage}


def equiv(path: str, reference: str, module: str | None = None, timeout_s: float = 600.0) -> tuple[int, list[str]]:
    """(exit code, the lines to print): the design formally equal to a reference module (D972) --
    a Yosys miter of the two (same ports), proved by SAT; a clocked pair by induction from zero."""
    design, ref = Path(path).read_text(), Path(reference).read_text()
    top, ref_top = _module(design, module), _module(ref, module)
    clocked = "clk" in _ports(design, top)
    with tempfile.TemporaryDirectory(prefix="flux-rtl-equiv-") as d:
        work = Path(d)
        (work / "design.sv").write_text(re.sub(r"\bendmodule\s*;", "endmodule", design) + "\n")
        (work / "ref.sv").write_text(re.sub(r"\bendmodule\s*;", "endmodule", ref) + "\n")
        prove = ("sat -tempinduct -prove trigger 0 -set-init-zero -seq 1 -verify -show-ports miter" if clocked
                 else "sat -prove trigger 0 -verify -show-ports miter")
        (work / "equiv.ys").write_text(
            f"read_verilog -sv design.sv\nhierarchy -top {top}\nproc\nflatten\nrename {top} gold\ndesign -stash gold\n"
            f"read_verilog -sv ref.sv\nhierarchy -top {ref_top}\nproc\nflatten\nrename {ref_top} gate\ndesign -stash gate\n"
            "design -copy-from gold -as gold gold\ndesign -copy-from gate -as gate gate\n"
            # no -ignore_gold_x, no opt: either hid a real difference in testing (D972)
            f"miter -equiv -flatten -make_outputs gold gate miter\nhierarchy -top miter\n{prove}\n")
        r = subprocess.run(["yosys", "-q", "-l", "equiv.log", "equiv.ys"], cwd=work, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=timeout_s)
        log = (work / "equiv.log").read_text() if (work / "equiv.log").exists() else r.stdout + r.stderr
    if r.returncode == 0 and re.search(r"SUCCESS|QED|Induction step proven", log):
        return 0, ["0 failing"]
    if re.search(r"(syntax error|ERROR: (Module|Can't|Parser|.*not found))", log) and not re.search(r"SAT (proof|temporal)", log):
        return 3, ["did not parse: " + next((ln for ln in log.splitlines() if "ERROR" in ln or "syntax error" in ln), "yosys failed"),
                   "1 failing"]
    if re.search(r"(different|mismatch|port).*(width|names?)", log, re.I) and "miter" in log and "ERROR" in log:
        return 1, ["the design and the reference do not have the same ports", "1 failing"]
    rows = re.findall(r"^\s+\\(in_\w+|gold_\w+|gate_\w+)\s+(\S+)\s+\S+\s+\S+\s*$", log, re.M)
    example = [f"{name.replace('in_', 'input ', 1).replace('gold_', 'design ', 1).replace('gate_', 'reference ', 1)} = {value}"
               for name, value in rows][:16]
    return 1, ["FAIL not equivalent; a counterexample:", *example, "1 failing"]


def cycles(path: str, golden: str, module: str | None = None, timeout_s: float = 300.0) -> tuple[int, list[str]]:
    """(exit code, the lines to print): a clocked design's cycles over the golden's vectors (D971),
    each vector started when the last is done -- the latency, not overlapped throughput."""
    from dataclasses import replace

    from .golden import check_rtl, load_golden

    g = load_golden(golden)
    if not g.clocked:
        return 2, ["the golden model declares no CLOCK: a design without a clock takes no cycles"]
    # start/done driven whatever LATENCY the golden says (or none): measured, not checked
    got = check_rtl(Path(path).read_text(), replace(g, latency=g.latency if g.latency is not None else 0),
                    module=module, timeout_s=timeout_s, claim=False)
    if got.error:
        return (3 if got.error.startswith(("did not compile", "the module has", "no `module")) else 1), [got.error]
    if not got.ok or not got.cycles:
        return 1, [*got.lines, "a design that fails its golden vectors is not timed"]
    n = len(got.cycles)
    return 0, [f"latency_cycles={max(got.cycles)} latency_min_cycles={min(got.cycles)} "
               f"cycles_per_result={sum(got.cycles) / n:.4g} vectors={n}"]


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
    eq = sub.add_parser("equiv", help="formally equal to a reference module (Yosys miter + SAT): 0 failing or a counterexample")
    eq.add_argument("artifact")
    eq.add_argument("--reference", required=True)
    eq.add_argument("--module")
    eq.add_argument("--timeout", type=float, default=600.0)
    br = sub.add_parser("breakdown", help="where the synthesised area goes: sequential, adders, XORs, buffers, logic")
    br.add_argument("artifact")
    br.add_argument("--clock-ps", type=float, default=1000.0)
    br.add_argument("--module")
    br.add_argument("--timeout", type=float, default=1800.0)
    fm = sub.add_parser("fmax", help="the fastest clock that closes timing at --stage, by a few ORFS runs")
    fm.add_argument("artifact")
    fm.add_argument("--stage", choices=("synth", "place", "route"), default="synth")
    fm.add_argument("--clock-ps", type=float, default=1000.0, help="the first period tried")
    fm.add_argument("--steps", type=int, default=6)
    fm.add_argument("--module")
    fm.add_argument("--utilization", type=float, default=40)
    fm.add_argument("--timeout", type=float, default=1800.0)
    pw = sub.add_parser("power", help="power with golden.py's vectors' own switching (Verilator VCD, OpenROAD)")
    pw.add_argument("artifact")
    pw.add_argument("--golden", required=True)
    pw.add_argument("--stage", choices=("synth", "place"), default="place")
    pw.add_argument("--clock-ps", type=float, default=1000.0)
    pw.add_argument("--module")
    pw.add_argument("--timeout", type=float, default=1800.0)
    cy = sub.add_parser("cycles", help="a clocked design's cycles over golden.py's vectors (Verilator)")
    cy.add_argument("artifact")
    cy.add_argument("--golden", required=True)
    cy.add_argument("--module")
    cy.add_argument("--timeout", type=float, default=300.0)
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
    if a.command in ("lint", "cycles", "equiv"):
        code, lines = (lint(a.artifact, a.timeout) if a.command == "lint" else cycles(a.artifact, a.golden, a.module, a.timeout)
                       if a.command == "cycles" else equiv(a.artifact, a.reference, a.module, a.timeout))
        print("\n".join(lines))
        return code
    source = Path(a.artifact).read_text()
    if a.command in ("breakdown", "fmax", "power"):
        got = (breakdown(source, a.module, clock_ps=a.clock_ps, timeout_s=a.timeout) if a.command == "breakdown" else
               fmax(source, a.module, stage=a.stage, clock_ps=a.clock_ps, steps=a.steps, timeout_s=a.timeout,
                    utilization=a.utilization) if a.command == "fmax" else
               power(source, a.golden, a.module, stage=a.stage, clock_ps=a.clock_ps, timeout_s=a.timeout))
        print(" ".join(f"{k}={v:.6g}" if isinstance(v, float) else f"{k}={v}" for k, v in got.items()))
        return 0
    got = measure(source, a.module, stage=a.stage, clock_ps=a.clock_ps, timeout_s=a.timeout, utilization=a.utilization)
    print(" ".join(f"{k}={v:.6g}" if isinstance(v, float) else f"{k}={v}" for k, v in got.items() if k != "flow_depth")
          + f" stage={a.stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
