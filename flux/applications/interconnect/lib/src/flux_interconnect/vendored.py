"""Fabrics built from VENDORED IP rather than generated switches (docs/decisions.md D276).

`interconnect/vendor/` carries PULP's `xbar_varlat` (a logarithmic interconnect whose
`rr_arb_tree` muxes the data as it arbitrates) and an OBI-typed wrapper around it. This module
assembles those instances into the same multi-stage topologies `topology.py` describes, so a
generated fabric and a proven one can be measured through the same harness and compared on the
same table.

Why it exists is measured, not assumed: at the identical topology — seven 4x4 switches feeding
four 7x8 switches — this repo's generated switch closed several times slower than the vendored
IP, whose whole three-level request path is combinational. Tuning a hand-rolled arbiter against
a proven one is not the best use of anyone's time; holding the generator to the proven one's
number is.

**Scope, stated up front.** Address-slice routing needs every stage's fan-out to be a power of
two, and the fan-outs must multiply to the bank count. That covers the regular fabrics (the
staged and butterfly families) and excludes the irregular ones this repo can also express — a
Clos with seven middle switches has no bit-slice that names its port. Those keep the generated
switch, and `supports()` says which is which rather than failing at elaboration.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from .fabric import canonical_stages
from .topology import Topology

VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor"

# Compile order matters: macro headers and packages before anything that uses them, leaf cells
# before the modules that instantiate them.
VENDOR_SOURCES = (
    "common_cells/assertions.svh",
    "common_cells/cf_math_pkg.sv",
    "common_cells/lzc.sv",
    "common_cells/rr_arb_tree.sv",
    "cluster_interconnect/addr_dec_resp_mux_varlat.sv",
    "cluster_interconnect/xbar_varlat.sv",
    "obi_pkg.sv",
    "crossbar.sv",
)


class UnsupportedByVendoredIpError(ValueError):
    """The topology cannot be expressed with address-slice routing."""


def vendor_files() -> list[Path]:
    """The vendored sources, in compile order. Missing files raise here rather than inside a
    tool invocation, where the error would be a Verilator parse failure ten steps later."""
    paths = [VENDOR_DIR / name for name in VENDOR_SOURCES]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(
            f"vendored interconnect IP is not present: {[str(p) for p in missing]} — see "
            f"{VENDOR_DIR / 'PROVENANCE.md'}")
    return paths


# Yosys's SystemVerilog frontend is a subset, and the vendored IP uses three constructs
# outside it. Rather than edit the vendored files — they stay byte-identical to upstream, which
# is what makes the provenance checkable — the rewrites are applied at LOAD, the same shape as
# the upstream project's own `patches/` directory. Each is behaviour-preserving for the way
# THIS repo instantiates the IP, and each says why:
#
#   `unsigned'(x)`      -> `(x)`      the casts here are all in constant/elaboration
#                                     expressions where signedness cannot change the value
#   `parameter type T`  -> removed    Yosys has no type parameters. Both of them (`DataType`,
#                                     `idx_t`) are dependent parameters that `xbar_varlat`
#                                     never overrides — it passes only NumIn, DataWidth and
#                                     ExtPrio — so each is exactly its default here
#   `return expr;`      -> `f = expr` the Verilog-2001 function-return form
#
# If a vendored file changes so a rewrite no longer applies, loading RAISES rather than
# silently synthesising something else.
_TYPE_PARAMS: tuple[tuple[str, str, str], ...] = (
    ("rr_arb_tree.sv", "DataType", "logic [DataWidth-1:0]"),
    ("rr_arb_tree.sv", "idx_t", "logic [IdxWidth-1:0]"),
)

_LINE_PATCHES: tuple[tuple[str, str, str], ...] = (
    # Yosys cannot take `$bits` of a TYPE. obi_req_t is req + we + be + addr + wdata, so the
    # same width is written from the macros that define it — exact, and still correct if the
    # data width is overridden at elaboration.
    ("crossbar.sv", "$bits(obi_req_t)", "(2 + `OBI_BE_WIDTH + 32 + `OBI_DATA_WIDTH)"),
    ("cf_math_pkg.sv",
     "return (num_idx > 32'd1) ? ($clog2(num_idx)) : 32'd1;",
     "idx_width = (num_idx > 32'd1) ? ($clog2(num_idx)) : 32'd1;"),
    ("cf_math_pkg.sv",
     "return (value != 0) && (value & (value - 1)) == 0;",
     "is_power_of_2 = (value != 0) && ((value & (value - 1)) == 0);"),
)


def _yosys_compatible(name: str, body: str) -> str:
    """Rewrite one vendored file into Yosys's SystemVerilog subset."""
    body = body.replace("unsigned'(", "(")
    # Yosys takes neither a package-scoped `import` on a module header nor, reliably,
    # package-qualified types in ports. The OBI package holds nothing but two typedefs, so
    # flattening it to file scope and dropping the imports is equivalent and leaves every
    # type name unchanged.
    drop_package = name == "obi_pkg.sv"   # scoped: cf_math_pkg is a real package and stays
    body = "\n".join(
        line for line in body.splitlines()
        if not (drop_package and line.strip() in ("package obi_pkg;", "endpackage"))
        and "import obi_pkg::*;" not in line)
    for target, type_name, concrete in _TYPE_PARAMS:
        if name != target:
            continue
        declaration = re.search(rf"^\s*parameter type {type_name}\s*=.*$", body, re.M)
        if not declaration:
            raise ValueError(
                f"{name}: expected a `parameter type {type_name}` declaration to remove — the "
                "vendored file changed and this rewrite no longer describes it")
        body = body.replace(declaration.group(0),
                            f"    // `parameter type {type_name}` removed for Yosys; it is "
                            f"always {concrete} as instantiated here")
        # a type name only ever appears where a type may: a port, a signal, or a cast
        body = re.sub(rf"\b{type_name}'\(", "(", body)
        body = re.sub(rf"(?<![\w.]){type_name}(?=\s)", concrete, body)
    for target, before, after in _LINE_PATCHES:
        if name == target:
            if before not in body:
                raise ValueError(
                    f"{name}: rewrite {before!r} no longer applies — the vendored file changed")
            body = body.replace(before, after)
    return body


def vendored_sources_text(*, for_yosys_builtin: bool = False) -> str:
    """All vendored RTL as one string with `include` directives RESOLVED.

    With a real SystemVerilog front end (`yosys-slang`) the text goes through UNCHANGED apart
    from the include resolution that concatenation forces. `for_yosys_builtin=True` additionally
    applies the rewrites in `_yosys_compatible`, which is a fallback and says so: it got five of
    the six constructs the built-in reader cannot take, and the sixth is not a rewrite.

    The flow takes a single source blob, and an `include` inside a concatenation looks for a
    file that is no longer beside it. Since the include list here is exactly the vendored set,
    ordering the files correctly and dropping the directives is equivalent and keeps the
    vendored text otherwise byte-identical to upstream.
    """
    out = []
    for path in vendor_files():
        body = "\n".join(line for line in path.read_text().splitlines()
                          if not line.lstrip().startswith("`include"))
        if for_yosys_builtin:
            body = _yosys_compatible(path.name, body)
        out.append(f"// ---- vendored: {path.name} ----\n{body}")
    return "\n".join(out)


def supports(topo: Topology) -> tuple[bool, str]:
    """(can this be built from the vendored IP, why not). A predicate rather than an exception
    because the answer is routinely 'no' for perfectly good fabrics, and a search needs to ask
    cheaply."""
    try:
        stages = canonical_stages(topo)
    except ValueError as exc:
        return False, str(exc)
    reach = 1
    for index, stage in enumerate(stages):
        fan_out = stage["out"]
        if fan_out & (fan_out - 1):
            return False, (f"stage {index + 1} fans out to {fan_out}, not a power of two — "
                           "address-slice routing cannot name that port")
        reach *= fan_out
    if reach != topo.banks:
        return False, (f"per-stage fan-outs multiply to {reach}, not the {topo.banks} banks — "
                       "the bank index is not the concatenation of the stage digits")
    return True, ""


def _slices(stages: list[dict[str, int]], word_bytes: int) -> list[tuple[int, int]]:
    """(start, length) of the address slice each stage routes on.

    The bank index is the concatenation of the per-stage digits, FIRST stage most significant,
    because a fabric delivers bank `d0 * prod(out[1:]) + d1 * ... `. Below the digits sit the
    byte offset within a word, which no stage routes on.
    """
    out: list[tuple[int, int]] = []
    start = int(math.log2(word_bytes))
    for stage in reversed(stages):
        length = max(1, int(math.log2(stage["out"])))
        out.append((start, length))
        start += length
    return list(reversed(out))


def vendored_fabric_rtl(topo: Topology, module_name: str = "vfabric") -> str:
    """The topology assembled from vendored `crossbar` instances, one per switch.

    Every switch is a real `xbar_varlat` behind the OBI wrapper, wired stage to stage by the
    same shuffle the generated fabric uses, so the two are the same NETWORK built from
    different switches — which is what makes comparing them meaningful.
    """
    ok, why = supports(topo)
    if not ok:
        raise UnsupportedByVendoredIpError(why)
    stages = canonical_stages(topo)
    width, banks, clients = topo.width_bits, topo.banks, topo.clients
    word_bytes = max(1, width // 8)
    slices = _slices(stages, word_bytes)

    lines: list[str] = [
        f"// {topo.kind}: " + " -> ".join(
            f"{st['switches']}x({st['in']}x{st['out']})" for st in stages),
        "// assembled from vendored PULP xbar_varlat via the OBI crossbar wrapper",
        "",
        f"module {module_name}",
        "    import obi_pkg::*;",
        "(",
        "    input logic clk_i,",
        "    input logic rst_ni,",
        f"    input  obi_req_t  [{clients - 1}:0] client_req_i,",
        f"    output obi_resp_t [{clients - 1}:0] client_resp_o,",
        f"    output obi_req_t  [{banks - 1}:0] bank_req_o,",
        f"    input  obi_resp_t [{banks - 1}:0] bank_resp_i",
        ");",
    ]

    for index, stage in enumerate(stages):
        n_in = stage["switches"] * stage["in"]
        n_out = stage["switches"] * stage["out"]
        lines += [
            f"    obi_req_t  [{n_in - 1}:0] s{index}_req;",
            f"    obi_resp_t [{n_in - 1}:0] s{index}_resp;",
            f"    obi_req_t  [{n_out - 1}:0] s{index}_oreq;",
            f"    obi_resp_t [{n_out - 1}:0] s{index}_oresp;",
        ]

    first_ports = stages[0]["switches"] * stages[0]["in"]
    lines.append("    always_comb begin")
    lines.append(f"        s0_req = '0;")
    lines.append(f"        for (int c = 0; c < {clients}; c++) s0_req[c] = client_req_i[c];")
    lines.append("    end")
    lines.append(f"    for (genvar c = 0; c < {clients}; c++) begin : gen_client_resp")
    lines.append("        assign client_resp_o[c] = s0_resp[c];")
    lines.append("    end")
    if first_ports > clients:
        lines.append(f"    // stage-1 inputs beyond the client count are tied off by the '0 above")

    for index, stage in enumerate(stages):
        start, length = slices[index]
        for switch in range(stage["switches"]):
            base_in = switch * stage["in"]
            base_out = switch * stage["out"]
            lines += [
                f"    crossbar #(",
                f"        .XBAR_NMASTER    ({stage['in']}),",
                f"        .XBAR_NSLAVE     ({stage['out']}),",
                f"        .SEL_SLICE_START ({start}),",
                f"        .SEL_SLICE_LENGTH({length})",
                f"    ) u_s{index}_w{switch} (",
                "        .clk_i, .rst_ni,",
                f"        .master_req_i (s{index}_req [{base_in + stage['in'] - 1}:{base_in}]),",
                f"        .master_resp_o(s{index}_resp[{base_in + stage['in'] - 1}:{base_in}]),",
                f"        .slave_req_o  (s{index}_oreq [{base_out + stage['out'] - 1}:{base_out}]),",
                f"        .slave_resp_i (s{index}_oresp[{base_out + stage['out'] - 1}:{base_out}])",
                "    );",
            ]

    from .fabric import _link_map

    for index in range(len(stages) - 1):
        stage, nxt = stages[index], stages[index + 1]
        landing = _link_map(stage["switches"] * stage["out"], nxt["switches"], nxt["in"])
        for link, (down_switch, down_in) in enumerate(landing):
            target = down_switch * nxt["in"] + down_in
            lines += [
                f"    assign s{index + 1}_req[{target}] = s{index}_oreq[{link}];",
                f"    assign s{index}_oresp[{link}] = s{index + 1}_resp[{target}];",
            ]

    last = len(stages) - 1
    lines += [
        f"    for (genvar b = 0; b < {banks}; b++) begin : gen_bank",
        f"        assign bank_req_o[b] = s{last}_oreq[b];",
        f"        assign s{last}_oresp[b] = bank_resp_i[b];",
        "    end",
        "endmodule",
    ]
    return "\n".join(lines) + "\n"


def measure_vendored_fabric(topo: Topology, *, target_period_ps: float = 1667.0,
                            timeout_s: int = 3600,
                            core_utilization: float = 65.0) -> dict[str, Any]:
    """Place the vendored fabric through the same flow the generated one goes through, so the
    two numbers are comparable rather than merely both real."""
    from flux_evaluator_openroad.flow import run_ppa_flow

    import os

    # A real front end where one is available, and the rewrite fallback where it is not — with
    # the difference reported, because the two are not guaranteed to synthesise the same thing.
    slang = bool(os.environ.get("YOSYS_SLANG_PLUGIN"))
    sources = vendored_sources_text(for_yosys_builtin=not slang)
    harness = _vendored_harness(topo)
    report = run_ppa_flow(
        sources + "\n" + vendored_fabric_rtl(topo) + "\n" + harness, "vfabric_harness",
        clock_port="clk_i", reset_port="rst_ni", clock_period_ps=target_period_ps,
        repair_design=True, timeout_s=timeout_s, core_utilization=core_utilization,
        full_mapping=True,
        pin_layers=(("M4", "M6"), ("M5", "M7")),
        sv_frontend="slang" if slang else "builtin",
    )
    delay_ps = report.clock_period_ps - report.worst_slack_ps
    return {
        "kind": topo.kind,
        "source": "vendored xbar_varlat",
        "sv_frontend": "slang" if slang else "yosys builtin (rewritten)",
        "area_mm2": report.area_um2 * 1e-6,
        "fmax_mhz": 1e6 / delay_ps if delay_ps > 0 else 0.0,
        "cell_count": report.cell_count,
        "utilization_pct": report.utilization_pct,
        "power_total_w": report.power_total_w,
        "flow_depth": report.flow_depth,
    }


def _vendored_harness(topo: Topology) -> str:
    """Generator and checker around the vendored fabric, for the same reason the generated one
    has one: placed standalone its OBI buses are thousands of pins, and synthesis prunes any
    datapath bit that is neither driven nor observed."""
    clients, banks, width = topo.clients, topo.banks, topo.width_bits
    lanes = max(1, width // 32)
    return f"""
module vfabric_harness
    import obi_pkg::*;
(
    input  logic clk_i,
    input  logic rst_ni,
    output logic [31:0] signature
);
    localparam int CLIENTS = {clients};
    localparam int BANKS   = {banks};
    localparam int LANES   = {lanes};

    obi_req_t  [CLIENTS-1:0] creq;
    obi_resp_t [CLIENTS-1:0] cresp;
    obi_req_t  [BANKS-1:0]   breq;
    obi_resp_t [BANKS-1:0]   bresp;

    logic [31:0] lfsr [0:CLIENTS*LANES-1];
    logic [31:0] acc  [0:BANKS*LANES-1];

    vfabric u_fab (.clk_i, .rst_ni, .client_req_i(creq), .client_resp_o(cresp),
                   .bank_req_o(breq), .bank_resp_i(bresp));

    // banks answer immediately and return what they were asked for, so every datapath bit is
    // both driven and observed and nothing is optimised away
    for (genvar b = 0; b < BANKS; b++) begin : gen_bank_model
        assign bresp[b].gnt = 1'b1;
        always_ff @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                bresp[b].rvalid <= 1'b0;
                bresp[b].rdata  <= '0;
            end else begin
                bresp[b].rvalid <= breq[b].req;
                bresp[b].rdata  <= breq[b].wdata;
            end
        end
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin : stim
        automatic logic [31:0] x;
        if (!rst_ni) begin
            for (int i = 0; i < CLIENTS*LANES; i++) lfsr[i] <= 32'h1234_5678 + i * 32'h9E37_79B9;
            for (int i = 0; i < BANKS*LANES; i++) acc[i] <= '0;
            for (int c = 0; c < CLIENTS; c++) creq[c] <= '0;
            signature <= '0;
        end else begin
            for (int c = 0; c < CLIENTS; c++) begin
                creq[c].req <= 1'b1;
                creq[c].we  <= 1'b0;
                creq[c].be  <= '1;
                for (int k = 0; k < LANES; k++) begin
                    x = lfsr[c*LANES + k];
                    x = x ^ (x << 13);
                    x = x ^ (x >> 17);
                    x = x ^ (x << 5);
                    lfsr[c*LANES + k] <= x;
                    creq[c].wdata[k*32 +: 32] <= x;
                    if (k == 0) creq[c].addr <= x;
                end
            end
            // A signature CHAIN, not a readout mux (docs/decisions.md D277). Reading
            // `acc[rd]` is a 128:1 mux of 32-bit words with the read pointer driving all of
            // its select logic, and it became the critical path of the whole measurement —
            // every one of the four worst paths started at that one register. Chaining each
            // accumulator into the next keeps every bank observed (nothing is pruned, because
            // each feeds the next) while the per-cycle path is two XOR levels.
            for (int b = 0; b < BANKS; b++)
                for (int k = 0; k < LANES; k++)
                    if (bresp[b].rvalid)
                        acc[b*LANES + k] <= acc[b*LANES + k] ^ bresp[b].rdata[k*32 +: 32]
                                          ^ ((b*LANES + k == 0) ? 32'd0 : acc[b*LANES + k - 1]);
            signature <= acc[BANKS*LANES - 1];
        end
    end
endmodule
"""
