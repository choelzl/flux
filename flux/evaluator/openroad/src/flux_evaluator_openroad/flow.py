"""The Yosys → OpenROAD physical-design flow (docs/decisions.md D225).

One public function: `run_ppa_flow(verilog_source, module_name, ...) -> PpaReport`. Two real
tools, each a subprocess, each failure surfaced with its own log tail:

1. **Yosys**: synthesize the RTL against the merged ASAP7 RVT/TT liberty
   (`codegen/rtl_harness`'s D92 ingestion — the same cells, so synthesis here and gate-count
   ranking there agree by construction), `dfflibmap` + `abc -liberty`, netlist out.
2. **OpenROAD**: read the ASAP7 tech + cell LEFs (this package's D225 ingestion), floorplan at
   ASAP7's own reference density, make tracks from the platform's own definitions, place
   (global + detailed), estimate parasitics from placement, and report design area, power and
   worst timing slack.

**v0.1 stops after placement + placement-based parasitics — deliberately.** Area is real placed
area (core utilization applied to real LEF cell footprints), power is OpenROAD's report over the
real liberty power tables at the given clock, timing is placement-estimate slack. Clock-tree
synthesis, global/detailed routing and routed parasitics are the named next fidelity step, not
silently skipped: `PpaReport.flow_depth` says exactly how far the flow went, and anything
consuming these numbers can see it.
"""

from __future__ import annotations

import os
import gzip
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import OpenRoadError

_PLATFORM = Path(__file__).resolve().parent / "platform" / "asap7"

# ASAP7's own reference placement density (platform config.mk: PLACE_DENSITY ?= 0.60) — cited,
# not invented. Utilization for the initial floorplan is set below it so the placer has slack.
_PLACE_DENSITY = 0.60
_CORE_UTILIZATION = 40  # percent

# ASAP7 liberty time unit is 1 ps. 2 ns default: the first real placement measured the 8-lane
# 32-bit datapath's critical path at ~1707 ps (worst slack -707 at a 1 ns clock), and a default
# that fails timing on the reference design would mark every default result invalid — the
# validity gate is for designs that miss a *stated* target, not for a default nobody chose.
# `worst_slack_ps` in the report says how much margin the clock actually had.
_DEFAULT_CLOCK_PERIOD_PS = 2000.0


def _merged_liberty_path(scratch: Path) -> Path:
    """Decompress the D92 merged liberty (RVT/TT NLDM) into the scratch dir."""
    import flux_codegen_rtl_harness

    src = (
        Path(flux_codegen_rtl_harness.__file__).resolve().parent
        / "asap7_pdk"
        / "asap7sc7p5t_simple_invbuf_seq_rvt_tt.lib.gz"
    )
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
    flow_depth: str  # "placement" (v0.1); routing rungs extend this vocabulary
    yosys_log_tail: str
    openroad_log_tail: str

    @property
    def area_mm2(self) -> float:
        return self.area_um2 * 1e-6

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


def _run(cmd: list[str], *, cwd: Path, timeout_s: float, what: str) -> str:
    from flux_profile import phase

    # Timed here rather than at each call site: this is the only place either tool is launched,
    # so the accounting cannot drift out of step with the code (docs/decisions.md D295).
    try:
        with phase(f"tool:{cmd[0].rsplit('/', 1)[-1]}"):
            proc = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s
            )
    except FileNotFoundError as exc:
        raise OpenRoadError(f"{what}: {cmd[0]!r} not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise OpenRoadError(f"{what}: timed out after {timeout_s}s") from exc
    if proc.returncode != 0:
        tail = (proc.stdout + "\n" + proc.stderr)[-3000:]
        raise OpenRoadError(f"{what} failed (exit={proc.returncode}):\n{tail}")
    return proc.stdout


def _yosys_synth(
    verilog_source: str, module_name: str, liberty: Path, scratch: Path,
    *, chparams: dict[str, int], yosys_bin: str, timeout_s: float,
    full_mapping: bool = False, macro_stubs: list[Path] | None = None,
    sv_frontend: str = "builtin", abc_delay_target_ps: float | None = None,
) -> tuple[Path, int]:
    """Synthesize to an ASAP7-mapped netlist; returns (netlist path, mapped cell count).
    `macro_stubs` (D254): black-box module declarations (e.g. generated SRAM macros) read
    before the design, so their instances survive synthesis as instances."""
    rtl = scratch / "design.sv"
    rtl.write_text(verilog_source)
    netlist = scratch / "netlist.v"
    # Parameters go on `hierarchy` itself: a separate `chparam -set` before elaboration is
    # silently ignored (measured — LANES 8 vs 32 produced near-identical netlists until this
    # moved).
    chparam_args = "".join(f" -chparam {n} {v}" for n, v in chparams.items())
    stub_reads = "".join(f"read_verilog -sv {s}\n" for s in (macro_stubs or ()))
    # Yosys's own SystemVerilog reader is a subset, and industrial IP sits outside it — type
    # parameters, `$bits` of a type, package imports on a module header, packed-array width
    # inference (docs/decisions.md D276). `read_slang` is a real front end; it elaborates the
    # top itself, so `hierarchy` follows without re-reading. Selected explicitly rather than
    # sniffed, so a design that synthesised one way keeps synthesising that way.
    slang_plugin = os.environ.get("YOSYS_SLANG_PLUGIN", "")
    if sv_frontend == "slang":
        if not slang_plugin or not Path(slang_plugin).exists():
            raise OpenRoadError(
                "sv_frontend='slang' needs the yosys-slang plugin: set YOSYS_SLANG_PLUGIN to "
                "its slang.so (the .#physical dev shell exports it)")
        read_block = (f"plugin -i {slang_plugin}\n"
                      f"read_slang --top {module_name} {rtl}\n")
    else:
        read_block = (f"read_verilog -sv {rtl}\n"
                      f"hierarchy -top {module_name}{chparam_args}\n")
    script = (
        f"{stub_reads}"
        f"{read_block}"
        "synth -flatten -noabc\n"
        f"dfflibmap -liberty {liberty}\n"
        # `-D` gives ABC the period to map AGAINST. Without it the mapper has no timing goal
        # and optimises for area, which is not what a frequency measurement is asking
        # (docs/decisions.md D278). `-fast` additionally cuts the effort level; it costs about
        # 40% of the frequency and 29% of the area, so it is a screening choice and named one.
        f"abc {'' if full_mapping else '-fast '}-liberty {liberty}"
        f"{f' -D {abc_delay_target_ps:.0f}' if abc_delay_target_ps else ''}\n"
        "opt_clean -purge\n"
        f"stat -liberty {liberty}\n"
        # setundef: ABC can leave x-constants; OpenROAD's verilog reader wants driven nets.
        "setundef -zero\n"
        f"write_verilog -noattr -noexpr -nohex -nodec {netlist}\n"
    )
    (scratch / "synth.ys").write_text(script)
    # `-l <file>`, never `-q`: quiet mode suppresses `stat`'s own report, which is the output
    # being parsed (found by running the real tool, not the docs).
    log_path = scratch / "yosys.log"
    _run([yosys_bin, "-l", str(log_path), "-s", str(scratch / "synth.ys")],
         cwd=scratch, timeout_s=timeout_s, what="yosys synthesis")
    log = log_path.read_text()

    # `stat -liberty` (yosys 0.66) prints an area-annotated table whose total row reads
    # `  1025  125.971 cells` — and, past ~1e3 um^2, `  19321 1.93E+03 cells`: the area switches
    # to scientific notation with size. Both observed from the real tool. This is D181's
    # defect class (an exponent a fixed-point pattern silently truncates), reproduced live here
    # after the repo spent a decision noting it had never seen a real instance.
    cells = re.search(r"^\s*(\d+)\s+[\d.eE+-]+\s+cells\s*$", log, re.MULTILINE)
    if not cells:
        raise OpenRoadError(f"yosys ran but its `stat` report is unparseable:\n{log[-1500:]}")

    # OpenROAD's structural netlist reader rejects `input signed [31:0] x;` (measured: STA-0171
    # syntax error at the first such line). On a technology-mapped netlist signedness carries no
    # information — every wire is just bits between cells — so it is stripped, not worked around.
    stripped = re.sub(r"^(\s*(?:input|output|wire))\s+signed\b", r"\1",
                      netlist.read_text(), flags=re.MULTILINE)
    netlist.write_text(stripped)
    return netlist, int(cells.group(1))


# ASAP7's own buffer family carries BUFx2..BUFx24; x4 is ORFS's customary CTS buffer and is
# present in the vendored liberty (checked against the real file, not assumed).
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
    macro_lefs: list[Path] | None = None, macro_libs: list[Path] | None = None,
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
        # A clocked design also needs I/O delays, or input->flop paths are UNCONSTRAINED and
        # report_worst_slack answers INF — measured on a crossbar block whose entire critical
        # path is input->mux->flop (docs/decisions.md D261). The clock port itself is excluded
        # from all_inputs, otherwise OpenSTA constrains the clock as data.
        f"create_clock -name core_clock -period {clock_period_ps} [get_ports {clock_port}]\n"
        # `all_inputs -no_clocks` rather than remove_from_collection: OpenSTA in this
        # OpenROAD has no such command (measured), and -no_clocks is its own idiom for
        # "every input port except clocks".
        f"set_input_delay 0 -clock core_clock [all_inputs -no_clocks]\n"
        "set_output_delay 0 -clock core_clock [all_outputs]\n"
        # A reset port is not a datapath. Driven from a pad it fans out to every flop in the
        # design — measured on a 256k-cell fabric: 18,568 sinks, 10.4 pF, 17.9 ns through one
        # weak NAND — and that path, not the interconnect, set every frequency the fabric
        # study reported until it was looked at (docs/decisions.md D274). Real flows build a
        # reset tree during CTS, which a placement-only flow does not run, so the honest
        # options are to except the path or to report a number that is about reset buffering.
        # Excepting it is stated here rather than hidden: reset recovery/removal remains
        # unchecked, and a real implementation still owes the tree.
        + (f"set_false_path -from [get_ports {reset_port}]\n" if reset_port else "")
        if clock_port
        else
        # A purely combinational design still needs a timing reference for power/slack: a
        # virtual clock constrains the ports without requiring a clock pin.
        f"create_clock -name core_clock -period {clock_period_ps}\n"
        f"set_input_delay 0 -clock core_clock [all_inputs]\n"
        f"set_output_delay 0 -clock core_clock [all_outputs]\n"
    )
    macro_lef_reads = "".join(f"read_lef {p}\n" for p in (macro_lefs or ()))
    macro_lib_reads = "".join(f"read_liberty {p}\n" for p in (macro_libs or ()))
    # A design with macros places them first (D254): global_placement treats unplaced macros
    # as movable soft blocks badly; macro_placement (mpl) fixes them, then std cells place
    # around. Halo keeps std-cell rows off the macro edge.
    # Hier-RTLMP is this OpenROAD's macro placer (`macro_placement` was its predecessor's
    # command and no longer exists — found by running the real tool, D254).
    macro_place = (
        "rtl_macro_placer -halo_width 2 -halo_height 2\n" if macro_lefs else "")
    tcl = (
        f"read_lef {tech_lef}\n"
        f"read_lef {cell_lef}\n"
        f"{macro_lef_reads}"
        f"read_liberty {liberty}\n"
        f"{macro_lib_reads}"
        f"read_verilog {netlist}\n"
        f"link_design {module_name}\n"
        f"initialize_floorplan -utilization {core_utilization} "
        "-aspect_ratio 1.0 -core_space 2.0 -site asap7sc7p5t\n"
        f"source {make_tracks}\n"
        f"{macro_place}"
        # Pin-limited blocks (a wide crossbar slice has thousands of ports) need both a
        # bigger die and more pin layers, or place_pins refuses with PPL-0024 (measured,
        # docs/decisions.md D261). The reported `Design area` stays CELL area either way, so
        # a lower utilization buys placement room without inflating the number being compared.
        f"place_pins -hor_layers {{{' '.join(pin_layers[0])}}} "
        f"-ver_layers {{{' '.join(pin_layers[1])}}}\n"
        f"global_placement -density {_PLACE_DENSITY}\n"
        # ORFS's own order: place, estimate RC, BUFFER, then legalize (docs/decisions.md
        # D261). Opt-in because every number this repo pinned before it (D225/D237/D254) was
        # measured without it — a high-fanout net is the case that needs it: a 5-bit select
        # driving ~3500 mux cells reported -127 ns of slack unbuffered.
        + (
            # `repair_design` fixes DESIGN RULE violations, and it can only fix what it has
            # been given a rule for. Without a fanout or transition target it left a net with
            # 33 sinks driven by a NOR2xp33 at 1.4 ns of slew — 771 ps through a single
            # inverter — sitting on the critical path of a fabric whose logic depth is small
            # (docs/decisions.md D275). `set_wire_rc` likewise gives parasitic estimation a
            # layer to work from instead of a default.
            # The PLATFORM's own RC setup, not a hand-picked layer. ASAP7's tech LEF carries
            # no RESISTANCE or CAPACITANCE at all, so `set_wire_rc -layer M3` set nothing —
            # which is why sweeping M3/M5/M7 gave byte-identical results and why
            # `repair_timing` refused with "could not find a resistance value for any corner"
            # (docs/decisions.md D278). `setRC.tcl` ships with the platform for exactly this.
            f"source {_PLATFORM / 'setRC.tcl'}\n"
            # Design-rule targets are OPT-IN, because imposing them made things worse
            # (docs/decisions.md D277). `set_max_fanout 16` makes `repair_design` split every
            # wide net into groups of sixteen, and on ASAP7 it reaches for weak `HB3xp67`
            # buffers to do it — measured, six of them in series at ~200 ps each, which became
            # the critical path itself. Left unset, the resizer buffers for TIMING instead of
            # to a fanout number, and the same design ran considerably faster.
            + (f"set_max_fanout {max_fanout} [current_design]\n" if max_fanout else "")
            + (f"set_max_transition {max_transition_ps} [current_design]\n"
               if max_transition_ps else "")
            + "estimate_parasitics -placement\n"
            "repair_design\n"
            # `repair_design` fixes DESIGN RULES. Setup violations are `repair_timing`'s job,
            # and it was simply never run (docs/decisions.md D278) — so every frequency this
            # repo has reported came from a netlist on which no timing optimisation had been
            # performed at all. Re-estimate first, because repair_design has moved things, and
            # legalise afterwards, because resizing leaves cells overlapping.
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
        'puts "FLUX_POWER_REPORT_BEGIN"\n'
        "report_power\n"
        'puts "FLUX_SLACK_REPORT_BEGIN"\n'
        "report_worst_slack\n"
        # The worst PATH, not just its slack: a number tells you a design is slow, the path
        # tells you which of your own mistakes made it slow. Three separate wrong guesses at a
        # fabric's critical path were made before this line existed (docs/decisions.md D274).
        'puts "FLUX_PATH_REPORT_BEGIN"\n'
        # Compact by design: the verbose per-gate form is long enough that a log TAIL loses
        # the startpoint, which is the one field that says what the path actually is.
        "report_checks -path_delay max -digits 3 -group_count 4 -format summary\n"
        "report_checks -path_delay max -digits 3 -fields {slew capacitance fanout} "
        "-format full_clock_expanded\n"
        'puts "FLUX_DONE"\n'
        "exit\n"
    )
    path = scratch / "flow.tcl"
    path.write_text(tcl)
    return path


# Real formats, captured from openroad 26Q2 rather than guessed (docs/decisions.md D225):
#   `Design area 1928 um^2 40% utilization.`
#   `Total   1.45e-02   2.51e-02   9.85e-07   3.97e-02 100.0%`
#   `worst slack max -707.03`
_AREA_RE = re.compile(r"Design area (\d+) um\^2 (\d+)% utilization")
_POWER_TOTAL_RE = re.compile(
    r"^Total\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)", re.MULTILINE
)
# `INF` is a legitimate report_worst_slack output (no constrained paths in that direction) —
# observed from the real tool on a clocked design, mapped to float("inf") rather than rejected.
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
    full_mapping: bool = False,
    flow_depth: str = "placement",
    macros: list | None = None,
    core_utilization: float = _CORE_UTILIZATION,
    reset_port: str | None = None,
    wire_rc_layer: str = "M3",
    max_fanout: int | None = None,
    max_transition_ps: float | None = None,
    pin_layers: tuple[tuple[str, ...], tuple[str, ...]] = (("M4",), ("M5",)),
    repair_design: bool = False,
    sv_frontend: str = "builtin",
) -> PpaReport:
    """Synthesize + place (and, at `flow_depth="routed"`, clock-tree + route + extract)
    `verilog_source` on ASAP7 and report real PPA (docs/decisions.md D225/D229).

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

        # Generated black-box macros (D254): each SramMacro's stub/LEF/liberty is written to
        # scratch and threaded through both tools — yosys keeps the instances, openroad
        # places the blocks.
        macro_stubs, macro_lefs, macro_libs = [], [], []
        for m in macros or ():
            stub = scratch / f"{m.name}_stub.v"
            stub.write_text(m.verilog_stub)
            lef = scratch / f"{m.name}.lef"
            lef.write_text(m.lef_text)
            lib = scratch / f"{m.name}.lib"
            lib.write_text(m.lib_text)
            macro_stubs.append(stub)
            macro_lefs.append(lef)
            macro_libs.append(lib)

        # `full_mapping=True` restores the exact liberty mapping at its measured cost (420 s vs
        # 0.7 s for 8 lanes, D228) for callers who want the ~15% tighter netlist; the default
        # -fast mapping applies identically to every candidate, so rankings never depend on it.
        netlist, cell_count = _yosys_synth(
            verilog_source, module_name, liberty, scratch,
            chparams=chparams or {}, yosys_bin=yosys_bin, timeout_s=timeout_s,
            full_mapping=full_mapping, macro_stubs=macro_stubs,
            sv_frontend=sv_frontend, abc_delay_target_ps=clock_period_ps,
        )
        tcl = _openroad_tcl(
            netlist, module_name, liberty, cell_lef, scratch,
            clock_period_ps=clock_period_ps, clock_port=clock_port, flow_depth=flow_depth,
            macro_lefs=macro_lefs, macro_libs=macro_libs,
            core_utilization=core_utilization, reset_port=reset_port,
            wire_rc_layer=wire_rc_layer, max_fanout=max_fanout,
            max_transition_ps=max_transition_ps,
            pin_layers=pin_layers,
            repair_design=repair_design,
        )
        log = _run([openroad_bin, "-no_init", "-no_splash", "-exit", str(tcl)],
                   cwd=scratch, timeout_s=timeout_s, what="openroad placement flow")

        if "FLUX_DONE" not in log:
            raise OpenRoadError(f"openroad flow did not reach completion:\n{log[-3000:]}")
        area = _AREA_RE.search(log)
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
            area_um2=float(area.group(1)),
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
            # Wide enough to hold a timing path WITH its startpoint. At 2000 characters the
        # verbose path report overflowed its own head, so the one field that identifies the
        # path — where it starts — was the field that never survived (docs/decisions.md D277).
        openroad_log_tail=log[-8000:],
        )
