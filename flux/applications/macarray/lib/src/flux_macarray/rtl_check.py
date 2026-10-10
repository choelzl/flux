"""This application's RTL checks and measurements: Verilator against golden.py, and Yosys +
OpenROAD on ASAP7 (OpenROAD-flow-scripts' platform files).

    python rtl.py test DESIGN.sv --golden golden.py        # "N failing of M"; exit 0, 1, or 3 (no build)
    python rtl.py measure DESIGN.sv --stage synth --clock-ps 1000   # fmax_mhz=... area_um2=... power_w=...

The golden model declares `PORTS` ([{name, dir, bits}], signed unless `unsigned: true`) and
`golden(**inputs) -> {output: value}`; optionally `VECTORS`, `COUNT`, `SEED`, `EXHAUSTIVE`,
`CLOCK` (the design has `clk` and `rst_n`), `LATENCY` (it also has `start` and `done`; the
cycles are checked) and `TOLERANCE_ULP` ({output: n}, a float output's allowed error).

`measure --stage stat` is Yosys alone (area, cells); `synth` times Yosys's netlist with
OpenROAD, nothing placed; `place` after placement. ASAP7's typical RVT cells; a module without `clk` is timed input to output. Needs
verilator, yosys and openroad on PATH and ASAP7 from OpenROAD-flow-scripts: FLOW_HOME (its
flow/ folder), or the Flux dev shell's OPENROAD_FLOW_SCRIPTS_HOME. One file, the same in every
RTL application: edit applications/mul8/rtl.py, then run scripts/sync-app-tools.py.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flux_loop.golden_model import EXHAUSTIVE_MAX_BITS, Golden, golden_vectors, ulp_distance  # noqa: F401

#: What a generated module is given before Verilator sees it: -Wall would refuse the sign
#: extensions and the unused bits a correct multiplier is full of.
LINT_PRAGMA = "/* verilator lint_off WIDTH */\n/* verilator lint_off UNUSEDSIGNAL */\n"
VERILATOR_FLAGS = ["-Wall", "-Wno-DECLFILENAME", "-Wno-UNUSEDSIGNAL", "-Wno-UNUSEDPARAM"]
MAX_CYCLES = 10_000


def lint_relaxed(source: str) -> str:
    return source if source.startswith(LINT_PRAGMA) else LINT_PRAGMA + source


def module_of(source: str, given: str | None = None) -> str:
    if given:
        return given
    m = re.search(r"^\s*module\s+([A-Za-z_]\w*)", source, re.M)
    if not m:
        raise SystemExit("no `module <name>` in the design; say --module")
    return m.group(1)


def _clean(source: str) -> str:
    """Verilator refuses a missing last newline, Yosys `endmodule;`."""
    source = re.sub(r"\bendmodule\s*;", "endmodule", source)
    return source if source.endswith("\n") else source + "\n"


def load_golden(path: str | Path) -> Golden:
    p = Path(path)
    spec = importlib.util.spec_from_file_location(f"golden_{abs(hash(str(p)))}", p)
    if spec is None or not p.is_file():
        raise SystemExit(f"golden model {p} is not a file")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        return Golden.from_module(mod)
    except ValueError as exc:
        raise SystemExit(f"{p}: {exc}") from exc


# ---- test: the golden's vectors through Verilator ---------------------------------------------

@dataclass(frozen=True)
class Check:
    total: int
    failing: int
    lines: tuple[str, ...] = ()     # the first failures: what came out, what went in, what was expected
    latency: int | None = None      # measured cycles (a design with start/done)
    error: str = ""                 # why it did not build, or the latency refused

    @property
    def ok(self) -> bool:
        return self.failing == 0 and not self.error

    @property
    def why(self) -> str:
        if self.error:
            return self.error
        return f"{self.total - self.failing}/{self.total} vectors passed" + (f": {self.lines[0]}" if self.lines else "")


def _testbench(g: Golden, module: str, n: int) -> str:
    """Drives each row of vectors.hex (the inputs, packed) and prints `OUT <row> <cycles> <outputs in hex>`;
    Python compares. A clocked design gets `clk`/`rst_n` (held low two edges); one with LATENCY
    also `start`/`done`, its cycles counted from the edge that took the inputs."""
    ins = [p for p in g.ports if p["dir"] == "in"]
    outs = [p for p in g.ports if p["dir"] == "out"]
    decl = lambda p: f"  logic [{int(p['bits']) - 1}:0] {p['name']};"   # noqa: E731
    pins = [f".{p['name']}({p['name']})" for p in g.ports]
    sync = g.clocked
    handshake = sync and g.latency is not None
    if sync:
        pins = [".clk(clk)", ".rst_n(rst_n)"] + ([".start(start)", ".done(done)"] if handshake else []) + pins
    width = sum(int(p["bits"]) for p in ins)
    body = ["    {" + ", ".join(p["name"] for p in ins) + "} = tab[v];"]
    if handshake:
        body += ["    start = 1'b1; @(posedge clk); #1; start = 1'b0; cycles = 0;",
                 f"    while (done !== 1'b1 && cycles < {MAX_CYCLES}) begin @(posedge clk); #1; cycles = cycles + 1; end"]
    elif sync:
        body += ["    @(posedge clk); #1;"]
    else:
        body += ["    #1;"]
    fmt = " ".join("%h" for _ in outs)
    body += [f'    $display("OUT %0d %0d {fmt}", v, cycles, {", ".join(p["name"] for p in outs)});']
    return "\n".join([
        "`timescale 1ns/1ps", "module testbench;",
        *(decl(p) for p in ins + outs),
        *(["  logic clk;", "  logic rst_n;", "  initial clk = 1'b0;", "  always #5 clk = ~clk;"] if sync else []),
        *(["  logic start;", "  logic done;", "  initial start = 1'b0;"] if handshake else []),
        f"  logic [{width - 1}:0] tab [0:{n - 1}];", "  int v;", "  int cycles = 0;",
        f"  {module} dut ({', '.join(pins)});",
        "  initial begin",
        '    $readmemh("vectors.hex", tab);',
        *(["    rst_n = 1'b0; @(posedge clk); @(posedge clk); #1; rst_n = 1'b1;"] if sync else []),
        f"    for (v = 0; v < {n}; v = v + 1) begin", *body, "    end", "    $finish;", "  end", "endmodule", ""])


def _value(hexs: str, bits: int, signed: bool) -> int | None:
    if re.search(r"[xXzZ]", hexs):
        return None
    v = int(hexs, 16) & ((1 << bits) - 1)
    return v - (1 << bits) if signed and bits > 1 and v >> (bits - 1) else v


#: Slips a model makes in SystemVerilog and the fix (D552), shown beside the quoted line
HINTS = (
    (r"unexpected ',', expecting '\}'", r"\{\s*\d+\s*\{",
     "a replication inside a concatenation needs braces of its own: write {{N{x}}, y}, not {N{x}, y}"),
    (r"unexpected ',', expecting '\}'", r"\d+'[bdh]",
     "a sized literal is fine in a concatenation ({16'd0, x}); look for a replication {N{...}} without its own braces on this line"),
    (r"Can't find definition of variable", r".",
     "the name is used before any declaration; declare the wire (with its width) above its first use"),
    (r"Operator ASSIGN expects .* on the Assign RHS", r".",
     "the widths of the two sides differ; make them the same width or slice explicitly"),
    (r"expecting IDENTIFIER", r"\blogic\b|\bwire\b",
     "a declaration is malformed; one name per declaration, the width before the name"),
)


def explain_diagnostic(message: str, source: str, *, prefix_lines: int = 0, file: str = "dut.sv") -> str:
    """Verilator's first diagnostic re-said for the author of `source` (D552): the line counted
    in their text, quoted with a caret at the column, and a hint for a known slip; the first
    %Error line as it was when nothing maps."""
    first = next((ln.strip() for ln in message.splitlines() if ln.lstrip().startswith(("%Error", "%Warning"))),
                 message.strip().splitlines()[0] if message.strip() else "")
    m = re.search(rf"{re.escape(file)}:(\d+):(\d+):\s*(.*)", first)
    lines = source.splitlines()
    if not m or not 1 <= int(m.group(1)) - prefix_lines <= len(lines):
        return first[:300]
    line_no, col, what = int(m.group(1)) - prefix_lines, int(m.group(2)), m.group(3).strip()
    text = lines[line_no - 1]
    out = f"{what} -- line {line_no} of your module, column {col}:\n    {text.rstrip()}\n    {' ' * max(0, col - 1)}^"
    hint = next((h for pat, on_line, h in HINTS if re.search(pat, what) and re.search(on_line, text)), None)
    return out + (f"\n  hint: {hint}" if hint else "")


def check_rtl(source: str, g: Golden, *, module: str | None = None, rows: list[dict[str, Any]] | None = None,
              extra_sources: dict[str, str] | None = None, timeout_s: float = 120.0,
              relaxed: bool = False, show: int = 5) -> Check:
    """Verilator on `source` against the golden's vectors (`rows` when made already). `relaxed`
    turns width and unused warnings off: a generated module is judged on its values."""
    try:
        module = module_of(source, module)
    except SystemExit:
        return Check(0, 0, error="no `module <name>` in the source")
    rows = rows if rows is not None else golden_vectors(g)
    ins = [p for p in g.ports if p["dir"] == "in"]
    outs = [p for p in g.ports if p["dir"] == "out"]
    signed = {p["name"]: not p.get("unsigned") and int(p["bits"]) > 1 for p in g.ports}
    text = lint_relaxed(source) if relaxed else source
    prefix = text.count("\n", 0, len(text) - len(source)) if relaxed else 0
    with tempfile.TemporaryDirectory(prefix="flux-rtl-test-") as d:
        work = Path(d)
        (work / "dut.sv").write_text(_clean(text))
        for stem, src in (extra_sources or {}).items():
            (work / f"{stem}.sv").write_text(_clean(src))
        (work / "testbench.sv").write_text(_testbench(g, module, len(rows)))
        packed = []
        for r in rows:
            word = 0
            for p in ins:
                word = (word << int(p["bits"])) | (int(r["inputs"][p["name"]]) & ((1 << int(p["bits"])) - 1))
            packed.append(f"{word:x}")
        (work / "vectors.hex").write_text("\n".join(packed) + "\n")
        srcs = ["testbench.sv", "dut.sv", *(f"{s}.sv" for s in (extra_sources or {}))]
        build = subprocess.run(["verilator", "--binary", "--timing", *VERILATOR_FLAGS, "-j", "1",
                                "--top-module", "testbench", *srcs],
                               cwd=work, capture_output=True, text=True, timeout=timeout_s)
        if build.returncode != 0:
            why = "did not compile: " + explain_diagnostic(build.stderr, source, prefix_lines=prefix)
            pin = re.search(r"[Mm]issing pin.*'(\w+)'|PINMISSING.*'(\w+)'", build.stderr)
            if pin and not g.clocked:
                why = (f"the module has a `{pin.group(1) or pin.group(2)}` port and the golden model declares no CLOCK, "
                       "so the test bench cannot drive it: write the module with the golden's ports only, or "
                       "the golden needs CLOCK and LATENCY. " + why)
            return Check(len(rows), len(rows), error=why)
        run = subprocess.run([str(work / "obj_dir" / "Vtestbench")], cwd=work, capture_output=True, text=True,
                             timeout=timeout_s)
    got = {int(m.group(1)): (int(m.group(2)), m.group(3).split())
           for m in re.finditer(r"^OUT (\d+) (\d+) ?(.*)$", run.stdout, re.M)}
    fails, cycles = [], []
    for i, r in enumerate(rows):
        if i not in got:
            fails.append(f"VECTOR {i} FAIL no output (the simulation stopped: {(run.stderr or run.stdout).strip()[-200:]})")
            continue
        n, hexs = got[i]
        cycles.append(n)
        vals = {p["name"]: _value(h, int(p["bits"]), signed[p["name"]]) for p, h in zip(outs, hexs)}
        bad = g.latency is not None and g.clocked and n >= MAX_CYCLES
        for p in outs:
            want, have, bits = int(r["expected"][p["name"]]), vals.get(p["name"]), int(p["bits"])
            tol = g.ulp.get(p["name"])
            if have is None or (tol is None and (have - want) % (1 << bits)) or \
                    (tol is not None and ulp_distance(have & ((1 << bits) - 1), want & ((1 << bits) - 1), bits) > int(tol)):
                bad = True
        if bad:
            said = " ".join(f"{k}={'x' if v is None else v}" for k, v in vals.items())
            fails.append(f"VECTOR {i} FAIL {said}" + (f" (done never rose in {MAX_CYCLES} cycles)" if n >= MAX_CYCLES else "")
                         + " -- for " + ", ".join(f"{k}={x}" for k, x in r["inputs"].items())
                         + " expected " + ", ".join(f"{k}={x}" for k, x in r["expected"].items()))
    latency = None
    if g.clocked and g.latency is not None and cycles and not fails:
        latency = max(cycles) + 1        # the edges after the one that took the inputs, and that one
        if latency != g.latency:
            return Check(len(rows), 0, (), latency, error=f"claims {g.latency} cycle(s) of latency, measured {latency}")
    return Check(len(rows), len(fails), tuple(fails[:show]), latency)


# ---- measure: Yosys and OpenROAD on ORFS's ASAP7 platform -------------------------------------

#: The platform's typical (TT) RVT cells: simple gates, inverters/buffers, flops
LIBS = ("lib/NLDM/asap7sc7p5t_SIMPLE_RVT_TT_nldm_211120.lib.gz", "lib/NLDM/asap7sc7p5t_INVBUF_RVT_TT_nldm_220122.lib.gz",
        "lib/NLDM/asap7sc7p5t_SEQ_RVT_TT_nldm_220123.lib")
STAGES = ("stat", "synth", "place")


def platform() -> Path:
    """ORFS's ASAP7 folder: $FLOW_HOME/platforms/asap7, or the Flux dev shell's ORFS."""
    homes = [os.environ.get("FLOW_HOME", "")]
    if os.environ.get("OPENROAD_FLOW_SCRIPTS_HOME"):
        homes.append(os.environ["OPENROAD_FLOW_SCRIPTS_HOME"] + "/share/openroad-flow-scripts/flow")
    for h in homes:
        if h and (Path(h) / "platforms/asap7/setRC.tcl").is_file():
            return Path(h) / "platforms/asap7"
    raise SystemExit("ASAP7 not found: set FLOW_HOME to OpenROAD-flow-scripts' flow/ folder (the Flux dev shell has it)")


def _ports(source: str, module: str) -> set[str]:
    m = re.search(rf"\bmodule\s+{re.escape(module)}\b(.*?);", source, re.S)
    return set(re.findall(r"\b([A-Za-z_]\w*)\b", m.group(1))) if m else set()


def _run(cmd: list[str], work: Path, timeout_s: float, what: str) -> str:
    r = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout_s)
    if r.returncode != 0:
        raise SystemExit(f"{what} failed:\n{(r.stdout + r.stderr)[-1500:]}")
    return r.stdout


def measure(source: str, module: str | None = None, *, stage: str = "synth", clock_ps: float = 1000.0,
            timeout_s: float = 900.0, utilization: float = 40, density: float = 0.60,
            repair_design: bool = False) -> dict[str, Any]:
    """One design on ASAP7: Yosys maps it against the clock (ABC -D), then OpenROAD times it --
    `synth` the netlist as mapped (nothing placed), `place` after placement with estimated
    wires. fmax_mhz, area_um2 (cells), power_w, cell_count, path_ps (the period less the worst
    slack) and critical_path ("start -> end"). A module without `clk` is timed input to output."""
    module = module_of(source, module)
    pdk = platform()
    ports = _ports(source, module)
    clock = "clk" if "clk" in ports else None
    with tempfile.TemporaryDirectory(prefix="flux-rtl-measure-") as d:
        work = Path(d)
        libs = [str(pdk / rel) for rel in LIBS]        # read as they are, .gz too (Yosys, ABC, OpenSTA)
        lib = " ".join(f"-liberty {x}" for x in libs)
        (work / "design.sv").write_text(_clean(source))
        (work / "synth.ys").write_text(
            f"read_verilog -sv design.sv\nhierarchy -top {module}\nsynth -flatten -noabc\n"
            f"dfflibmap {lib}\nabc {lib} -D {clock_ps:.0f}\nopt_clean -purge\nstat {lib}\n"
            "setundef -zero\nwrite_verilog -noattr -noexpr -nohex -nodec netlist.v\n")
        log = _run(["yosys", "-q", "-l", "yosys.log", "-s", "synth.ys"], work, timeout_s, "Yosys")
        log = (work / "yosys.log").read_text()
        cells = re.findall(r"^\s*(\d+)\s+([\d.eE+-]+)\s+cells\s*$", log, re.M)
        if not cells:
            raise SystemExit(f"Yosys's stat is unreadable:\n{log[-1500:]}")
        if stage == "stat":                 # Yosys alone (D662): the mapped cells, untimed
            return {"area_um2": float(cells[-1][1]), "cell_count": int(cells[-1][0])}
        # OpenROAD's netlist reader refuses `input signed [7:0] a;` (STA-0171); mapped, signedness says nothing
        net = work / "netlist.v"
        net.write_text(re.sub(r"^(\s*(?:input|output|wire))\s+signed\b", r"\1", net.read_text(), flags=re.M))
        sdc = (f"create_clock -name clk -period {clock_ps} [get_ports {clock}]\n"
               "set_input_delay 0 -clock clk [all_inputs -no_clocks]\n"
               + ("set_false_path -from [get_ports rst_n]\n" if "rst_n" in ports else "")
               if clock else f"create_clock -name clk -period {clock_ps}\nset_input_delay 0 -clock clk [all_inputs]\n") \
            + "set_output_delay 0 -clock clk [all_outputs]\n"
        load = (f"read_lef {pdk / 'lef/asap7_tech_1x_201209.lef'}\nread_lef {pdk / 'lef/asap7sc7p5t_28_R_1x_220121a.lef'}\n"
                + "".join(f"read_liberty {x}\n" for x in libs) + f"read_verilog {net}\nlink_design {module}\n")
        place = "" if stage == "synth" else (
            "insert_tiecells TIEHIx1_ASAP7_75t_R/H\ninsert_tiecells TIELOx1_ASAP7_75t_R/L\n"
            f"initialize_floorplan -utilization {utilization:g} -aspect_ratio 1.0 -core_space 2.0 -site asap7sc7p5t\n"
            f"source {pdk / 'openRoad/make_tracks.tcl'}\nplace_pins -hor_layers M4 -ver_layers M5\n"
            f"global_placement -density {density:g}\nsource {pdk / 'setRC.tcl'}\n"
            + ("estimate_parasitics -placement\nrepair_design\ndetailed_placement\nestimate_parasitics -placement\n"
               "repair_timing -setup\n" if repair_design else "")
            + "detailed_placement\n")
        tail = (sdc + ("" if stage == "synth" else f"source {pdk / 'setRC.tcl'}\nestimate_parasitics -placement\n")
                + 'puts "AREA [expr {[rsz::design_area] * 1e12}]"\nreport_power\nreport_worst_slack\n'
                "report_checks -path_delay max\nexit\n")
        (work / "flow.tcl").write_text(load + place + tail)
        log = _run(["openroad", "-no_init", "-no_splash", "-exit", "flow.tcl"], work, timeout_s, "OpenROAD")
    slack = re.search(r"worst slack (?:max )?(-?(?:[\d.eE+-]+|INF|inf))", log)
    power = re.search(r"^Total\s+[\d.eE+-]+\s+[\d.eE+-]+\s+[\d.eE+-]+\s+([\d.eE+-]+)", log, re.M)
    area = re.search(r"^AREA ([\d.eE+-]+)", log, re.M)
    if not slack:
        raise SystemExit(f"OpenROAD's report is unreadable:\n{log[-1500:]}")
    ws = float(slack.group(1).replace("INF", "inf"))
    path = clock_ps - ws
    start, end = re.search(r"Startpoint: (\S+)", log), re.search(r"Endpoint: (\S+)", log)
    return {"fmax_mhz": 1e6 / path if path > 0 else float("inf"),
            "area_um2": float(cells[-1][1]) if stage == "synth" or not area else float(area.group(1)),
            "power_w": float(power.group(1)) if power else float("nan"),
            "cell_count": int(cells[-1][0]), "path_ps": path, "worst_slack_ps": ws, "clock_period_ps": clock_ps,
            "flow_depth": "synthesis" if stage == "synth" else "placement",
            "critical_path": f"{start.group(1)} -> {end.group(1)}" if start and end else ""}


# ---- the commands ------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    t = sub.add_parser("test", help="check the design against golden.py's vectors")
    t.add_argument("artifact")
    t.add_argument("--golden", required=True)
    t.add_argument("--module")
    t.add_argument("--extra", action="append", default=[], help="another module file the design instantiates")
    t.add_argument("--show", type=int, default=8)
    t.add_argument("--timeout", type=float, default=300.0)
    me = sub.add_parser("measure", help="Yosys + OpenROAD on ASAP7: fmax, area, power, cells")
    me.add_argument("artifact")
    me.add_argument("--stage", choices=STAGES, default="synth")
    me.add_argument("--clock-ps", type=float, default=1000.0)
    me.add_argument("--module")
    me.add_argument("--utilization", type=float, default=40)
    me.add_argument("--repair-design", action="store_true", help="buffer and resize after placement")
    me.add_argument("--timeout", type=float, default=900.0)
    a = ap.parse_args(argv)
    source = Path(a.artifact).read_text()
    extra = {Path(f).stem: Path(f).read_text() for f in getattr(a, "extra", [])}
    if a.command == "test":
        got = check_rtl(source, load_golden(a.golden), module=a.module, extra_sources=extra or None,
                        timeout_s=a.timeout, show=a.show)
        print("\n".join(([got.error] if got.error else []) + list(got.lines)
                        + ([f"latency={got.latency}"] if got.latency is not None else [])
                        + [f"{got.failing if not got.error else got.total} failing of {got.total}"]))
        # exit 3: it did not build (a build failure to the gate); 1: it built and failed (D594)
        return 0 if got.ok else 3 if got.error.startswith(("did not compile", "the module has", "no `module")) else 1
    got = measure(source, a.module, stage=a.stage, clock_ps=a.clock_ps, timeout_s=a.timeout,
                  utilization=a.utilization, repair_design=a.repair_design)
    print(" ".join(f"{k}={v:.6g}" if isinstance(v, float) else f"{k}={v}" for k, v in got.items()
                   if k not in ("critical_path", "flow_depth")) + f" stage={a.stage}")
    if got.get("critical_path"):
        print("critical_path=" + got["critical_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
