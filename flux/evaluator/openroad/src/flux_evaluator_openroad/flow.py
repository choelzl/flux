"""The Yosys → OpenROAD physical-design flow (D225).

One public function: `run_ppa_flow(verilog_source, module_name, ...) -> PpaReport`. Two real
tools, each a subprocess, each failure surfaced with its own log tail:

1. **Yosys**: synthesize against the merged ASAP7 RVT/TT liberty (the same cells
   `codegen/rtl_harness` ranks with), `dfflibmap` + `abc -liberty`, netlist out.
2. **OpenROAD**: read the ASAP7 tech + cell LEFs, floorplan, make tracks, place, estimate
   parasitics (or, when routed, CTS + route + extract), and report area, power and worst slack.

`PpaReport.flow_depth` says how far the flow went, so consumers can see the fidelity.
"""

from __future__ import annotations

import os
import gzip
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import OpenRoadError

_PLATFORM = Path(__file__).resolve().parent / "platform" / "asap7"

# ASAP7's reference placement density (platform config.mk: PLACE_DENSITY ?= 0.60).
# Floorplan utilization is set below it so the placer has slack.
_PLACE_DENSITY = 0.60
_CORE_UTILIZATION = 40  # percent

# ASAP7 liberty time unit is 1 ps. 2 ns default: the reference datapath needs ~1.7 ns, and a
# default nobody chose must not mark every result invalid. `worst_slack_ps` gives the margin.
_DEFAULT_CLOCK_PERIOD_PS = 2000.0


def _merged_liberty_path(scratch: Path) -> Path:
    """Decompress the merged liberty (RVT/TT NLDM) into the scratch dir."""
    src = _PLATFORM / "liberty" / "asap7sc7p5t_simple_invbuf_seq_rvt_tt.lib.gz"
    out = scratch / "asap7_rvt_tt.lib"
    out.write_bytes(gzip.decompress(src.read_bytes()))
    return out


def _cell_lef_path(scratch: Path) -> Path:
    src = _PLATFORM / "lef" / "asap7sc7p5t_28_R_1x_220121a.lef.gz"
    out = scratch / "asap7sc7p5t_28_R_1x_220121a.lef"
    out.write_bytes(gzip.decompress(src.read_bytes()))
    return out


@dataclass(frozen=True, slots=True)
class PpaReport:
    """Real physical-design numbers plus exactly how real they are."""

    area_um2: float
    utilization_pct: float
    power_total_w: float
    power_breakdown_w: dict[str, float]  # internal / switching / leakage
    worst_slack_ps: float
    clock_period_ps: float
    cell_count: int
    flow_depth: str  # "synthesis" | "placement" | "routed"
    yosys_log_tail: str
    openroad_log_tail: str
    critical_path: dict[str, Any] | None = None   # the worst path as data (`parse_critical_path`)

    @property
    def area_mm2(self) -> float:
        return self.area_um2 * 1e-6

    @property
    def fmax_mhz(self) -> float:
        """The clock the worst path supports: 1e6 / (period - slack) ps, inf for a zero path."""
        path = self.clock_period_ps - self.worst_slack_ps
        return 1e6 / path if path > 0 else float("inf")

    def metrics(self) -> dict[str, float]:
        """The report as a metric dict (D440):
        `area_um2`, `power_w`, `worst_slack_ps`, `clock_period_ps`, `cell_count`, `fmax_mhz`,
        `utilization_pct`."""
        return {"area_um2": float(self.area_um2), "power_w": float(self.power_total_w or 0.0),
                "worst_slack_ps": float(self.worst_slack_ps),
                "clock_period_ps": float(self.clock_period_ps), "cell_count": int(self.cell_count),
                "fmax_mhz": self.fmax_mhz, "utilization_pct": float(self.utilization_pct)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "area_um2": self.area_um2,
            "area_mm2": self.area_mm2,
            "utilization_pct": self.utilization_pct,
            "power_total_w": self.power_total_w,
            "power_breakdown_w": dict(self.power_breakdown_w),
            "worst_slack_ps": self.worst_slack_ps,
            "clock_period_ps": self.clock_period_ps,
            "cell_count": self.cell_count,
            "flow_depth": self.flow_depth,
        }


def openroad_threads() -> int:
    """How many threads one OpenROAD run gets (D570): `FLUX_OPENROAD_THREADS`, else the box's
    cores over the four placements the loop's pool runs at once, at least one."""
    import os

    env = os.environ.get("FLUX_OPENROAD_THREADS", "").strip()
    if env.isdigit() and int(env) > 0:
        return int(env)
    return max(1, min(16, (os.cpu_count() or 4) // 4))


def _openroad_cmd(openroad_bin: str, tcl: Path) -> list[str]:
    return [openroad_bin, "-no_init", "-no_splash", "-threads", str(openroad_threads()), "-exit", str(tcl)]


def _run(cmd: list[str], *, cwd: Path, timeout_s: float, what: str) -> str:
    """One tool launch through the ABI's runner (D429), timed as a `tool:` phase; a missing
    binary, timeout or non-zero exit raises `OpenRoadError` naming `what` with the tool's tail."""
    from flux_evaluator_abi.tools import run_tool

    return run_tool(cmd, cwd=cwd, timeout_s=timeout_s, what=what, error=OpenRoadError).stdout


def _yosys_synth(
    verilog_source: str, module_name: str, liberty: Path, scratch: Path,
    *, chparams: dict[str, int], yosys_bin: str, timeout_s: float,
    full_mapping: bool = True,
    sv_frontend: str = "builtin", abc_delay_target_ps: float | None = None,
    keep_nets: tuple[str, ...] = (),
) -> tuple[Path, int]:
    """Synthesize to an ASAP7-mapped netlist; returns (netlist path, mapped cell count).
    `keep_nets` (D527): wire-name globs kept through
    `opt_clean -purge` so a timing path reads back to them; meant for register boundaries."""
    rtl = scratch / "design.sv"
    rtl.write_text(verilog_source)
    netlist = scratch / "netlist.v"
    # Parameters go on `hierarchy` itself: a separate `chparam -set` before elaboration is
    # silently ignored.
    chparam_args = "".join(f" -chparam {n} {v}" for n, v in chparams.items())
    # Yosys's own SystemVerilog reader is a subset (no type parameters, package imports on a
    # header, ...) (D276); `read_slang` is a full front end that elaborates the top itself.
    # Selected explicitly, not sniffed, so a design keeps synthesising the same way.
    slang_plugin = os.environ.get("YOSYS_SLANG_PLUGIN", "")
    if sv_frontend == "slang":
        if not slang_plugin or not Path(slang_plugin).exists():
            raise OpenRoadError(
                "sv_frontend='slang' needs the yosys-slang plugin: set YOSYS_SLANG_PLUGIN to "
                "its slang.so (the nix dev shell exports it)")
        # `read_slang` elaborates the top itself, so `hierarchy -chparam` would be silently
        # ignored: overrides go in as `-G name=value` at read time.
        slang_params = "".join(f" -G {n}={v}" for n, v in chparams.items())
        read_block = (f"plugin -i {slang_plugin}\n"
                      f"read_slang --top {module_name}{slang_params} {rtl}\n")
    else:
        read_block = (f"read_verilog -sv {rtl}\n"
                      f"hierarchy -top {module_name}{chparam_args}\n")
    keep_block = "".join(f"setattr -set keep 1 w:{pattern}\n" for pattern in keep_nets)
    script = (
        f"{read_block}"
        f"{keep_block}"
        "synth -flatten -noabc\n"
        f"dfflibmap -liberty {liberty}\n"
        # `-D` gives ABC the period to map against; without it the mapper optimises for area
        # (D278). `-fast` cuts effort at a large cost in frequency and area: screening only.
        f"abc {'' if full_mapping else '-fast '}-liberty {liberty}"
        f"{f' -D {abc_delay_target_ps:.0f}' if abc_delay_target_ps else ''}\n"
        "opt_clean -purge\n"
        f"stat -liberty {liberty}\n"
        # setundef: ABC can leave x-constants; OpenROAD's verilog reader wants driven nets.
        "setundef -zero\n"
        f"write_verilog -noattr -noexpr -nohex -nodec {netlist}\n"
    )
    (scratch / "synth.ys").write_text(script)
    # `-l <file>`, never `-q`: quiet mode suppresses `stat`'s report, which is parsed.
    log_path = scratch / "yosys.log"
    _run([yosys_bin, "-l", str(log_path), "-s", str(scratch / "synth.ys")],
         cwd=scratch, timeout_s=timeout_s, what="yosys synthesis")
    log = log_path.read_text()

    # `stat -liberty` (yosys 0.66) total row: `  1025  125.971 cells`, and past ~1e3 um^2
    # `  19321 1.93E+03 cells` -- the pattern must accept scientific notation (D181).
    cells = re.search(r"^\s*(\d+)\s+([\d.eE+-]+)\s+cells\s*$", log, re.MULTILINE)
    if not cells:
        raise OpenRoadError(f"yosys ran but its `stat` report is unparseable:\n{log[-1500:]}")
    # The same total row carries the liberty-summed cell area, which is what the synthesis stage
    # reports when nothing is placed (D365).
    (scratch / "synth_area_um2").write_text(cells.group(2))

    # OpenROAD's netlist reader rejects `input signed [31:0] x;` (STA-0171). On a mapped
    # netlist signedness carries no information, so it is stripped.
    stripped = re.sub(r"^(\s*(?:input|output|wire))\s+signed\b", r"\1",
                      netlist.read_text(), flags=re.MULTILINE)
    netlist.write_text(stripped)
    return netlist, int(cells.group(1))


# ASAP7's buffer family is BUFx2..BUFx24; x4 is ORFS's customary CTS buffer (in the vendored liberty).
_CTS_BUF_CELL = "BUFx4_ASAP7_75t_R"


def _parasitics_block(flow_depth: str, clock_port: str | None) -> str:
    """The part of the flow between placement and the reports, per depth (D229)."""
    if flow_depth == "placement":
        return "estimate_parasitics -placement\n"
    # CTS only where a clock NET exists: a combinational design under a virtual clock has no
    # clock pin to build a tree from, and OpenROAD errors on an empty clock net accordingly.
    cts = (
        f"repair_clock_inverters\n"
        f"clock_tree_synthesis -buf_list {{{_CTS_BUF_CELL}}} -root_buf {_CTS_BUF_CELL}\n"
        "detailed_placement\n"
        if clock_port
        else ""
    )
    return (
        cts
        + "global_route -congestion_iterations 30\n"
        + "detailed_route -verbose 0\n"
        + "define_process_corner -ext_model_index 0 TT\n"
        + f"extract_parasitics -ext_model_file {_PLATFORM / 'rcx_patterns.rules'}\n"
    )


def _openroad_tcl(
    netlist: Path, module_name: str, liberty: Path, cell_lef: Path, scratch: Path,
    *, clock_period_ps: float, clock_port: str | None, flow_depth: str = "placement",
    core_utilization: float = _CORE_UTILIZATION,
    reset_port: str | None = None,
    wire_rc_layer: str = "M3",
    max_fanout: int | None = None,
    max_transition_ps: float | None = None,
    pin_layers: tuple[tuple[str, ...], tuple[str, ...]] = (("M4",), ("M5",)),
    repair_design: bool = False,
) -> Path:
    tech_lef = _PLATFORM / "lef" / "asap7_tech_1x_201209.lef"
    make_tracks = _PLATFORM / "openRoad" / "make_tracks.tcl"
    clock_block = (
        # A clocked design also needs I/O delays, or input->flop paths are unconstrained and
        # report_worst_slack answers INF (D261). The clock port is excluded from all_inputs,
        # otherwise OpenSTA constrains the clock as data.
        f"create_clock -name core_clock -period {clock_period_ps} [get_ports {clock_port}]\n"
        # `all_inputs -no_clocks`: this OpenSTA has no remove_from_collection.
        f"set_input_delay 0 -clock core_clock [all_inputs -no_clocks]\n"
        "set_output_delay 0 -clock core_clock [all_outputs]\n"
        # The reset port is a false path: unbuffered it fans out to every flop and would set the
        # frequency (D274). Real flows build a reset tree in CTS; reset recovery/removal stays
        # unchecked here and a real implementation still owes the tree.
        + (f"set_false_path -from [get_ports {reset_port}]\n" if reset_port else "")
        if clock_port
        else
        # A purely combinational design still needs a timing reference for power/slack: a
        # virtual clock constrains the ports without requiring a clock pin.
        f"create_clock -name core_clock -period {clock_period_ps}\n"
        f"set_input_delay 0 -clock core_clock [all_inputs]\n"
        f"set_output_delay 0 -clock core_clock [all_outputs]\n"
    )
    tcl = (
        f"read_lef {tech_lef}\n"
        f"read_lef {cell_lef}\n"
        f"read_liberty {liberty}\n"
        f"read_verilog {netlist}\n"
        f"link_design {module_name}\n"
        # Tie cells give constant nets a real driver: TritonRoute refuses them as POWER nets
        # (DRT-0305). Names from ASAP7 config.mk's TIEHI/TIELO_CELL_AND_PORT, resolved here
        # because the .mk values are Make expansions.
        "insert_tiecells TIEHIx1_ASAP7_75t_R/H\n"
        "insert_tiecells TIELOx1_ASAP7_75t_R/L\n"
        f"initialize_floorplan -utilization {core_utilization} "
        "-aspect_ratio 1.0 -core_space 2.0 -site asap7sc7p5t\n"
        f"source {make_tracks}\n"
        # Pin-limited blocks need a bigger die and more pin layers, or place_pins refuses with
        # PPL-0024 (D261). The reported `Design area` stays cell area either way.
        f"place_pins -hor_layers {{{' '.join(pin_layers[0])}}} "
        f"-ver_layers {{{' '.join(pin_layers[1])}}}\n"
        f"global_placement -density {_PLACE_DENSITY}\n"
        # ORFS's order: place, estimate RC, buffer, then legalize (D261). Opt-in; high-fanout
        # nets are the case that needs it.
        + (
            # The platform's own RC setup (`setRC.tcl`): ASAP7's tech LEF has no resistance or
            # capacitance, so `set_wire_rc -layer` sets nothing and repair_timing fails (D278).
            f"source {_PLATFORM / 'setRC.tcl'}\n"
            # Design-rule targets are opt-in (D277): `set_max_fanout` makes repair_design chain
            # weak ASAP7 buffers onto the critical path; unset, the resizer buffers for timing.
            + (f"set_max_fanout {max_fanout} [current_design]\n" if max_fanout else "")
            + (f"set_max_transition {max_transition_ps} [current_design]\n"
               if max_transition_ps else "")
            + "estimate_parasitics -placement\n"
            "repair_design\n"
            # Setup violations are `repair_timing`'s job (D278). Re-estimate first (repair_design
            # moved things) and legalise after (resizing leaves cells overlapping).
            "detailed_placement\n"
            "estimate_parasitics -placement\n"
            "repair_timing -setup\n"
            "detailed_placement\n"
            "estimate_parasitics -placement\n"
            if repair_design else ""
        )
        + "detailed_placement\n"
        f"{clock_block}"
        f"source {_PLATFORM / 'setRC.tcl'}\n"
        f"{_parasitics_block(flow_depth, clock_port)}"
        'puts "FLUX_AREA_REPORT_BEGIN"\n'
        "report_design_area\n"
        # report_design_area rounds to whole um^2 (D870): 6.3 and 6.9 both read 6, and an area
        # tie-break goes blind. The same sum, unrounded.
        'puts "FLUX_DESIGN_AREA_UM2 [format %.4f [expr {[rsz::design_area] * 1e12}]]"\n'
        'puts "FLUX_POWER_REPORT_BEGIN"\n'
        "report_power\n"
        'puts "FLUX_SLACK_REPORT_BEGIN"\n'
        "report_worst_slack\n"
        # The worst path, not just its slack: it says what makes the design slow (D274).
        'puts "FLUX_PATH_REPORT_BEGIN"\n'
        # Compact: the verbose form is long enough that a log tail loses the startpoint.
        "report_checks -path_delay max -digits 3 -group_count 4 -format summary\n"
        # `net` names the net each pin drives, so a path reads back to RTL signal names (D526)
        "report_checks -path_delay max -digits 3 -fields {slew capacitance fanout net} "
        "-format full_clock_expanded\n"
        'puts "FLUX_DONE"\n'
        "exit\n"
    )
    path = scratch / "flow.tcl"
    path.write_text(tcl)
    return path


def run_synthesis_flow(
    verilog_source: str,
    module_name: str,
    *,
    clock_port: str | None = None,
    clock_period_ps: float = _DEFAULT_CLOCK_PERIOD_PS,
    reset_port: str | None = None,
    yosys_bin: str = "yosys",
    openroad_bin: str = "openroad",
    timeout_s: float = 300.0,
    full_mapping: bool = True,
    sv_frontend: str = "builtin",
    map_for: str = "delay",
    keep_nets: tuple[str, ...] = (),
) -> PpaReport:
    """Synthesize on ASAP7 and time the mapped netlist with OpenSTA, placing nothing (D365).

    `map_for`: "delay" hands ABC the clock period to map against (D278); "area" withholds it,
    so the mapper optimises for area and the timing is whatever that costs -- a different
    netlist from the same RTL, and a real lever when area is the objective (D366).

    The screening stage below placement: the same Yosys mapping, then static timing with no
    parasitics -- optimistic, but identical in method for every candidate, so it orders a space
    in seconds. `area_um2` is the liberty-summed cell area; `flow_depth` is "synthesis".
    """
    if shutil.which(yosys_bin) is None:
        raise OpenRoadError(f"yosys binary {yosys_bin!r} not on PATH")
    if shutil.which(openroad_bin) is None:
        raise OpenRoadError(f"openroad binary {openroad_bin!r} not on PATH")
    with tempfile.TemporaryDirectory(prefix="flux-synth-") as td:
        scratch = Path(td)
        liberty = _merged_liberty_path(scratch)
        netlist, cell_count = _yosys_synth(
            verilog_source, module_name, liberty, scratch, chparams={}, yosys_bin=yosys_bin,
            timeout_s=timeout_s, full_mapping=full_mapping, sv_frontend=sv_frontend,
            abc_delay_target_ps=clock_period_ps if map_for == "delay" else None,
            keep_nets=keep_nets)
        area_um2 = float((scratch / "synth_area_um2").read_text())
        clock_block = (
            f"create_clock -name core_clock -period {clock_period_ps} [get_ports {clock_port}]\n"
            f"set_input_delay 0 -clock core_clock [all_inputs -no_clocks]\n"
            "set_output_delay 0 -clock core_clock [all_outputs]\n"
            + (f"set_false_path -from [get_ports {reset_port}]\n" if reset_port else "")
            if clock_port else
            f"create_clock -name core_clock -period {clock_period_ps}\n"
            f"set_input_delay 0 -clock core_clock [all_inputs]\n"
            f"set_output_delay 0 -clock core_clock [all_outputs]\n")
        # OpenROAD's netlist reader needs a technology loaded even when nothing is placed
        # ("[ERROR ORD-2010] no technology has been read"): the platform's LEFs, no floorplan.
        tech_lef = _PLATFORM / "lef" / "asap7_tech_1x_201209.lef"
        cell_lef = _cell_lef_path(scratch)
        tcl = scratch / "sta.tcl"
        tcl.write_text(
            f"read_lef {tech_lef}\n"
            f"read_lef {cell_lef}\n"
            f"read_liberty {liberty}\n"
            f"read_verilog {netlist}\n"
            f"link_design {module_name}\n"
            f"{clock_block}"
            'puts "FLUX_POWER_REPORT_BEGIN"\n'
            "report_power\n"
            'puts "FLUX_SLACK_REPORT_BEGIN"\n'
            "report_worst_slack\n"
            'puts "FLUX_PATH_REPORT_BEGIN"\n'
            "report_checks -path_delay max -digits 3 -group_count 2 -format summary\n"
            "report_checks -path_delay max -digits 3 -fields {net} -format full_clock_expanded\n"
            'puts "FLUX_DONE"\n'
            "exit\n")
        log = _run(_openroad_cmd(openroad_bin, tcl),
                   cwd=scratch, timeout_s=timeout_s, what="opensta on the synthesized netlist")
        if "FLUX_DONE" not in log:
            raise OpenRoadError(f"sta flow did not reach completion:\n{log[-3000:]}")
        slack = _SLACK_RE.search(log)
        if not slack:
            raise OpenRoadError(f"sta ran but its slack report was unparseable:\n{log[-3000:]}")
        power = _POWER_TOTAL_RE.search(log)
        internal, switching, leakage, total = (
            (float(g) for g in power.groups()) if power else (float("nan"),) * 4)
        return PpaReport(
            area_um2=area_um2, utilization_pct=0.0, power_total_w=total,
            power_breakdown_w={"internal": internal, "switching": switching,
                               "leakage": leakage},
            worst_slack_ps=float(slack.group(1).replace("INF", "inf")),
            clock_period_ps=clock_period_ps, cell_count=cell_count, flow_depth="synthesis",
            yosys_log_tail=(scratch / "yosys.log").read_text()[-2000:],
            openroad_log_tail=log[-8000:], critical_path=parse_critical_path(log))


# The critical path as data (D526): the `full_clock_expanded` report parsed into its steps.
# Format (openroad 26Q2):
#     Fanout      Cap     Slew    Delay     Time   Description
#     ---------------------------------------------------------------------------------
#          2    1.550   25.734   49.502   49.502 v _6597_/QN (DFFHQNx1_ASAP7_75t_R)
#                                       2035.062   data arrival time
#                                       -792.996   slack (VIOLATED)
_PATH_STEP_RE = re.compile(
    r"^\s*(?:(?P<fanout>\d+)\s+)?(?:(?P<cap>[\d.]+)\s+)?(?:(?P<slew>[\d.]+)\s+)?(?P<delay>-?[\d.]+)\s+(?P<time>-?[\d.]+)\s+"
    r"(?P<edge>[v^])\s+(?P<pin>\S+)\s+\((?P<cell>[^)]+)\)\s*$", re.MULTILINE)
_PATH_NET_RE = re.compile(r"^\s+(?P<net>\S+) \(net\)\s*$", re.MULTILINE)   # `-fields {net}`: the line after its pin
_PATH_START_RE = re.compile(r"^Startpoint: (\S+)", re.MULTILINE)
_PATH_END_RE = re.compile(r"^Endpoint: (\S+)", re.MULTILINE)
_PATH_SLACK_RE = re.compile(r"^\s*(-?[\d.]+)\s+slack \((MET|VIOLATED)\)", re.MULTILINE)
_PATH_ARRIVAL_RE = re.compile(r"^\s*(-?[\d.]+)\s+data arrival time", re.MULTILINE)
_PATH_REQUIRED_RE = re.compile(r"^\s*(-?[\d.]+)\s+data required time", re.MULTILINE)


def parse_critical_path(log: str) -> dict[str, Any] | None:
    """The worst path of an OpenSTA `report_checks -format full_clock_expanded` report, from
    the log that holds it: `{startpoint, endpoint, slack_ps, met, arrival_ps, required_ps,
    steps}` with one step per pin `{pin, cell, net, delay_ps, time_ps, edge, fanout}` in
    path order (the clock pin and the endpoint's D pin included), or None when the log has
    no path. Times are the report's units (picoseconds on ASAP7)."""
    i = log.rfind("Startpoint:")
    if i < 0:
        return None
    text = log[i:]
    start, end = _PATH_START_RE.search(text), _PATH_END_RE.search(text)
    steps: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _PATH_STEP_RE.match(line)
        if m:
            steps.append({"pin": m.group("pin"), "cell": m.group("cell"), "net": None,
                          "delay_ps": float(m.group("delay")), "time_ps": float(m.group("time")),
                          "edge": m.group("edge"),
                          "fanout": int(m.group("fanout")) if m.group("fanout") else None})
            continue
        n = _PATH_NET_RE.match(line)
        if n and steps:
            steps[-1]["net"] = n.group("net")
        if "data arrival time" in line:
            break
    slack = _PATH_SLACK_RE.search(text)
    arrival = _PATH_ARRIVAL_RE.search(text)
    required = _PATH_REQUIRED_RE.search(text)
    if not steps and slack is None:
        return None
    return {"startpoint": start.group(1) if start else None, "endpoint": end.group(1) if end else None,
            "slack_ps": float(slack.group(1)) if slack else None,
            "met": (slack.group(2) == "MET") if slack else None,
            "arrival_ps": float(arrival.group(1)) if arrival else None,
            "required_ps": float(required.group(1)) if required else None,
            "steps": steps}


# Report formats (openroad 26Q2):
#   `Design area 1928 um^2 40% utilization.`
#   `Total   1.45e-02   2.51e-02   9.85e-07   3.97e-02 100.0%`
#   `worst slack max -707.03`
_AREA_RE = re.compile(r"Design area (\d+) um\^2 (\d+)% utilization")
#   `FLUX_DESIGN_AREA_UM2 6.3132`: the flow's own unrounded line (D870)
_AREA_EXACT_RE = re.compile(r"^FLUX_DESIGN_AREA_UM2 ([\d.eE+-]+)\s*$", re.MULTILINE)
_POWER_TOTAL_RE = re.compile(
    r"^Total\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)", re.MULTILINE
)
# `INF` is a legitimate report_worst_slack output (no constrained paths): float("inf").
_SLACK_RE = re.compile(r"worst slack (?:max )?(-?(?:[\d.eE+-]+|INF|inf))")


def run_ppa_flow(
    verilog_source: str,
    module_name: str,
    *,
    chparams: dict[str, int] | None = None,
    clock_port: str | None = None,
    clock_period_ps: float = _DEFAULT_CLOCK_PERIOD_PS,
    yosys_bin: str = "yosys",
    openroad_bin: str = "openroad",
    timeout_s: float = 600.0,
    full_mapping: bool = True,
    flow_depth: str = "placement",
    core_utilization: float = _CORE_UTILIZATION,
    reset_port: str | None = None,
    wire_rc_layer: str = "M3",
    max_fanout: int | None = None,
    max_transition_ps: float | None = None,
    pin_layers: tuple[tuple[str, ...], tuple[str, ...]] = (("M4",), ("M5",)),
    repair_design: bool = False,
    sv_frontend: str = "builtin",
    map_for: str = "delay",
    keep_nets: tuple[str, ...] = (),
) -> PpaReport:
    """Synthesize + place (and, at `flow_depth="routed"`, clock-tree + route + extract)
    `verilog_source` on ASAP7 and report real PPA (D225/D229).

    `"placement"`: global+detailed placement, parasitics estimated from placement — fast, the
    screening-grade physical number. `"routed"`: adds clock-tree synthesis (only when the design
    has a real clock port — a combinational datapath has no clock net to build a tree on),
    global + detailed routing (TritonRoute), and OpenRCX extraction against the platform's own
    rules file, so timing/power rest on extracted RC rather than placement estimates.
    `PpaReport.flow_depth` records which ran.
    """
    if flow_depth not in ("placement", "routed"):
        raise OpenRoadError(f"flow_depth={flow_depth!r} must be 'placement' or 'routed'")
    if shutil.which(yosys_bin) is None:
        raise OpenRoadError(f"yosys binary {yosys_bin!r} not on PATH")
    if shutil.which(openroad_bin) is None:
        raise OpenRoadError(f"openroad binary {openroad_bin!r} not on PATH")

    with tempfile.TemporaryDirectory(prefix="flux-openroad-") as td:
        scratch = Path(td)
        liberty = _merged_liberty_path(scratch)
        cell_lef = _cell_lef_path(scratch)

        # Full ABC mapping by default: -fast costs too much area and power (D564).
        netlist, cell_count = _yosys_synth(
            verilog_source, module_name, liberty, scratch,
            chparams=chparams or {}, yosys_bin=yosys_bin, timeout_s=timeout_s,
            full_mapping=full_mapping,
            sv_frontend=sv_frontend,
            abc_delay_target_ps=clock_period_ps if map_for == "delay" else None,
            keep_nets=keep_nets,
        )
        tcl = _openroad_tcl(
            netlist, module_name, liberty, cell_lef, scratch,
            clock_period_ps=clock_period_ps, clock_port=clock_port, flow_depth=flow_depth,
            core_utilization=core_utilization, reset_port=reset_port,
            wire_rc_layer=wire_rc_layer, max_fanout=max_fanout,
            max_transition_ps=max_transition_ps,
            pin_layers=pin_layers,
            repair_design=repair_design,
        )
        log = _run(_openroad_cmd(openroad_bin, tcl),
                   cwd=scratch, timeout_s=timeout_s, what="openroad placement flow")

        if "FLUX_DONE" not in log:
            raise OpenRoadError(f"openroad flow did not reach completion:\n{log[-3000:]}")
        area = _AREA_RE.search(log)
        exact = _AREA_EXACT_RE.search(log)
        power = _POWER_TOTAL_RE.search(log)
        slack = _SLACK_RE.search(log)
        if not (area and power and slack):
            missing = [n for n, m in
                       (("area", area), ("power", power), ("slack", slack)) if not m]
            raise OpenRoadError(
                f"openroad ran but its {missing} report(s) were unparseable:\n{log[-3000:]}"
            )

        internal, switching, leakage, total = (float(g) for g in power.groups())
        yosys_log = (scratch / "yosys.log").read_text()
        return PpaReport(
            area_um2=float(exact.group(1)) if exact else float(area.group(1)),
            utilization_pct=float(area.group(2)),
            power_total_w=total,
            power_breakdown_w={
                "internal": internal, "switching": switching, "leakage": leakage,
            },
            worst_slack_ps=float(slack.group(1).replace("INF", "inf")),
            clock_period_ps=clock_period_ps,
            cell_count=cell_count,
            flow_depth=flow_depth,
            yosys_log_tail=yosys_log[-2000:],
            # wide enough to hold a timing path with its startpoint (D277)
        openroad_log_tail=log[-8000:],
        critical_path=parse_critical_path(log),
        )


#: The stages an RTL measurement stops at (D582): the loop's words for the flow's depths.
#: `stat` (D662) is Yosys alone: the mapped cells and their liberty area, no timing.
RTL_STAGES = ("stat", "synth", "place", "route")


def run_stat_flow(verilog_source: str, module_name: str, *, yosys_bin: str = "yosys",
                  timeout_s: float = 300.0, abc_delay_target_ps: float | None = None) -> dict[str, Any]:
    """The synthesis stage's Yosys mapping and `stat -liberty`, nothing timed (D662):
    `area_um2` (liberty-summed cell area) and `cell_count`, in about the time Yosys takes."""
    if shutil.which(yosys_bin) is None:
        raise OpenRoadError(f"yosys binary {yosys_bin!r} not on PATH")
    with tempfile.TemporaryDirectory(prefix="flux-stat-") as td:
        scratch = Path(td)
        _, cell_count = _yosys_synth(verilog_source, module_name, _merged_liberty_path(scratch), scratch,
                                     chparams={}, yosys_bin=yosys_bin, timeout_s=timeout_s,
                                     abc_delay_target_ps=abc_delay_target_ps)
        area = float((scratch / "synth_area_um2").read_text())
    return {"area_um2": area, "cell_count": cell_count, "flow_depth": "stat"}


def _port_of(sources: str, module: str, name: str) -> str | None:
    """`name` when `module`'s header declares a port of that name, else None."""
    m = re.search(rf"\bmodule\s+{re.escape(module)}\b(.*?)\)\s*;", sources, re.S)
    return name if m and re.search(rf"\b{re.escape(name)}\b", m.group(1)) else None


def measure_rtl(sources: str, module: str, *, stage: str, clock_period_ps: float,
                clock_port: str | None = "auto", reset_port: str | None = "auto",
                repair_design: bool = False, map_for: str = "delay",
                timeout_s: float | None = None) -> dict[str, Any]:
    """One RTL design through one stage (D582): what `flux rtl measure` prints and what a
    world measures its designs with -- one implementation. `stage` is `stat` (Yosys alone:
    area and cells, D662), `synth` (Yosys +
    OpenSTA, nothing placed), `place` or `route` (OpenROAD). `clock_port`/`reset_port`
    "auto" take `clk`/`rst_n` when the module has them (a combinational module is timed
    input to output). Returns the report's metrics plus `flow_depth`, `path_ps` (the worst
    path: the period less the slack) and `critical_path` ("start -> end")."""
    if stage not in RTL_STAGES:
        raise ValueError(f"stage {stage!r} is one of {', '.join(RTL_STAGES)}")
    if clock_port == "auto":
        clock_port = _port_of(sources, module, "clk")
    if reset_port == "auto":
        reset_port = _port_of(sources, module, "rst_n") if clock_port else None
    kw: dict[str, Any] = dict(clock_port=clock_port, reset_port=reset_port, clock_period_ps=clock_period_ps,
                              map_for=map_for, **({"timeout_s": timeout_s} if timeout_s else {}))
    if stage == "stat":             # the same mapping as synth (ABC given the period), untimed
        return run_stat_flow(sources, module, abc_delay_target_ps=clock_period_ps if map_for == "delay" else None,
                             **({"timeout_s": timeout_s} if timeout_s else {}))
    if stage == "synth":
        r = run_synthesis_flow(sources, module, **kw)
    else:
        r = run_ppa_flow(sources, module, flow_depth="placement" if stage == "place" else "routed",
                         repair_design=repair_design, **kw)
    cp = r.critical_path or {}
    start, end = cp.get("startpoint") or "", cp.get("endpoint") or ""
    return {**r.metrics(), "flow_depth": r.flow_depth, "path_ps": r.clock_period_ps - r.worst_slack_ps,
            "critical_path": f"{start} -> {end}" if start or end else ""}
