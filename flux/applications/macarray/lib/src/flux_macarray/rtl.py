"""SystemVerilog for one MAC processing element, generated from a `PeConfig` and a `Shape`.

Plain, tool-portable constructs only: `wire`/`reg`, `assign`, `always @(posedge clk)`, signed
arithmetic. Every design here is read by three tools -- Verilator (verification), Yosys's own
SystemVerilog front end (synthesis) and OpenSTA (timing) -- and the intersection of what they
accept is Verilog-2001 with `logic`. Nothing clever in the language; the structure is the point.

Port contract (what the golden vectors drive, D365):

    a0..a{L-1}   signed [IN-1:0]     activations
    w0..w{L-1}   signed [W-1:0]      weights
    acc_in       signed [ACC-1:0]    the running sum (only when the shape accumulates)
    acc          signed [ACC-1:0]    acc_in + sum_i a_i * w_i
    clk, rst_n, start, done          only when pipelined -- the harness's own names (D49/D115)

A pipelined PE raises `done` exactly `pipeline` cycles after `start`, which is also the cycle
its output is valid; the harness measures that latency and the study checks it equals the
number of stages the configuration claims, the D118 discipline.

An INVENTED multiplier is a module named by the caller with ports `a`, `w`, `p`; the PE
instantiates it once per lane and its source travels beside the PE's as an extra source.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import MULTIPLIERS, REDUCERS, PeConfig, Shape


@dataclass(frozen=True)
class Design:
    """A generated PE: the module, its source, and any leaf modules it instantiates."""

    module_name: str
    source: str
    extra_sources: dict[str, str]
    config: PeConfig
    shape: Shape

    @property
    def all_sources(self) -> str:
        """One text with every module, for tools that read a single file."""
        return "".join(f"{src}\n" for src in self.extra_sources.values()) + self.source


# ---- multipliers -------------------------------------------------------------------------------
# Each returns (declarations, assignments) producing `wire signed [P-1:0] p{i}` from a{i}, w{i}.
# Every width is made explicit: Verilator runs with warnings fatal, and an implicit extension
# is a WIDTHEXPAND it refuses. Yosys is happier for it too.

def _sx(name: str, width: int, to: int) -> str:
    """`name`, a signed `width`-bit value, sign-extended to `to` bits (signed)."""
    if to == width:
        return name
    return f"$signed({{{{{to - width}{{{name}[{width - 1}]}}}}, {name}}})"


def _mul_behavioral(i: int, s: Shape) -> list[str]:
    P, IN, W = s.product_bits, s.in_bits, s.w_bits
    return [f"  assign p{i} = {_sx(f'a{i}', IN, P)} * {_sx(f'w{i}', W, P)};"]


def _mul_array(i: int, s: Shape) -> list[str]:
    """Sign-magnitude shift-and-add: one partial-product row per weight bit, a ripple of adds."""
    P, IN, W = s.product_bits, s.in_bits, s.w_bits
    lines = [
        f"  wire neg{i} = a{i}[{IN - 1}] ^ w{i}[{W - 1}];",
        f"  wire [{IN - 1}:0] am{i} = a{i}[{IN - 1}] ? (~a{i} + {IN}'d1) : a{i};",
        f"  wire [{W - 1}:0] wm{i} = w{i}[{W - 1}] ? (~w{i} + {W}'d1) : w{i};",
    ]
    rows = []
    for j in range(W):
        lines.append(f"  wire [{P - 1}:0] pp{i}_{j} = wm{i}[{j}] ? ({{{{{P - IN}{{1'b0}}}}, am{i}}} << {j}) : {P}'d0;")
        rows.append(f"pp{i}_{j}")
    acc = rows[0]
    for j, r in enumerate(rows[1:], 1):
        lines.append(f"  wire [{P - 1}:0] ps{i}_{j} = {acc} + {r};")
        acc = f"ps{i}_{j}"
    lines.append(f"  assign p{i} = neg{i} ? $signed(~{acc} + {P}'d1) : $signed({acc});")
    return lines


def _mul_booth4(i: int, s: Shape) -> list[str]:
    """Radix-4 Booth: recode the weight three bits at a time into digits -2..2, sum the rows."""
    P, W = s.product_bits, s.w_bits
    lines = [f"  wire signed [{P - 1}:0] ax{i} = {_sx(f'a{i}', s.in_bits, P)};",
             f"  wire signed [{P - 1}:0] ax2{i} = ax{i} <<< 1;",
             f"  wire signed [{P - 1}:0] nax{i} = -ax{i};",
             f"  wire signed [{P - 1}:0] nax2{i} = -ax2{i};"]
    # Weight extended to an even width with a sign bit; bit -1 is zero.
    We = W + (W % 2)
    ext = f"w{i}[{W - 1}], " if We > W else ""
    lines.append(f"  wire [{We}:0] wb{i} = {{{ext}w{i}, 1'b0}};")
    rows = []
    for k in range(We // 2):
        lo = 2 * k
        lines += [
            f"  reg signed [{P - 1}:0] bd{i}_{k};",
            f"  always @(*) begin",
            f"    case (wb{i}[{lo + 2}:{lo}])",
            f"      3'b001, 3'b010: bd{i}_{k} = ax{i};",
            f"      3'b011: bd{i}_{k} = ax2{i};",
            f"      3'b100: bd{i}_{k} = nax2{i};",
            f"      3'b101, 3'b110: bd{i}_{k} = nax{i};",
            f"      default: bd{i}_{k} = {P}'sd0;",
            f"    endcase",
            f"  end",
            f"  wire signed [{P - 1}:0] br{i}_{k} = bd{i}_{k} <<< {lo};",
        ]
        rows.append(f"br{i}_{k}")
    lines.append(f"  assign p{i} = " + " + ".join(rows) + ";")
    return lines


def _csa_layers(rows: list[str], width: int, prefix: str) -> tuple[list[str], list[str]]:
    """3:2 compress `rows` (unsigned `width`-bit wires) until two remain. Returns (lines, rows)."""
    lines: list[str] = []
    level = 0
    while len(rows) > 2:
        nxt: list[str] = []
        for g in range(0, len(rows) - 2, 3):
            x, y, z = rows[g], rows[g + 1], rows[g + 2]
            sname, cname = f"{prefix}s{level}_{g // 3}", f"{prefix}c{level}_{g // 3}"
            lines.append(f"  wire [{width - 1}:0] {sname} = {x} ^ {y} ^ {z};")
            lines.append(f"  wire [{width - 1}:0] {cname} = (({x} & {y}) | ({x} & {z}) | ({y} & {z})) << 1;")
            nxt += [sname, cname]
        nxt += rows[(len(rows) // 3) * 3:]
        rows, level = nxt, level + 1
    return lines, rows


def _mul_wallace(i: int, s: Shape) -> list[str]:
    """Sign-magnitude partial products compressed by a carry-save tree, then one final adder."""
    P, IN, W = s.product_bits, s.in_bits, s.w_bits
    lines = [
        f"  wire neg{i} = a{i}[{IN - 1}] ^ w{i}[{W - 1}];",
        f"  wire [{IN - 1}:0] am{i} = a{i}[{IN - 1}] ? (~a{i} + {IN}'d1) : a{i};",
        f"  wire [{W - 1}:0] wm{i} = w{i}[{W - 1}] ? (~w{i} + {W}'d1) : w{i};",
    ]
    rows = []
    for j in range(W):
        lines.append(f"  wire [{P - 1}:0] pp{i}_{j} = wm{i}[{j}] ? ({{{{{P - IN}{{1'b0}}}}, am{i}}} << {j}) : {P}'d0;")
        rows.append(f"pp{i}_{j}")
    csa, (r0, r1) = _csa_layers(rows, P, f"m{i}")
    lines += csa
    lines.append(f"  wire [{P - 1}:0] mag{i} = {r0} + {r1};")
    lines.append(f"  assign p{i} = neg{i} ? $signed(~mag{i} + {P}'d1) : $signed(mag{i});")
    return lines


def _mul_invented(name: str):
    def gen(i: int, s: Shape) -> list[str]:
        return [f"  {name} u_mul{i} (.a(a{i}), .w(w{i}), .p(p{i}));"]
    return gen


_MULTIPLIER_GEN = {"behavioral": _mul_behavioral, "array": _mul_array,
                   "booth4": _mul_booth4, "wallace": _mul_wallace}


# ---- reduction ----------------------------------------------------------------------------------

def _sext(name: str, width: int, to: int) -> str:
    """`name` (a signed `width`-bit value) as a `to`-bit two's-complement bit vector."""
    if to == width:
        return name
    return f"{{{{{to - width}{{{name}[{width - 1}]}}}}, {name}}}"


def _reduce(cfg: PeConfig, s: Shape, terms: list[tuple[str, int]], mid_register: bool
            ) -> tuple[list[str], str, list[str]]:
    """Sum signed `terms` ((name, width) each) into one `acc_bits`-wide signed value.

    Returns (lines, the final sum's wire, wires to register at the mid cut). `mid_register`
    is pipeline stage 3: the reduction is emitted in two halves and the wires between them
    are named so the caller can register them; the second half then reads the `_r` copies.
    """
    A = s.acc_bits
    lines: list[str] = []
    names = [t for t, _ in terms]
    widths = {t: w for t, w in terms}

    def ext(name: str) -> str:
        base = name[:-2] if name.endswith("_r") and name not in widths else name
        return _sx(name, widths.get(name, widths.get(base, A)), A)

    if cfg.reducer == "chain":
        counter = [0]

        def chain(items: list[str]) -> str:
            cur = items[0]
            for t in items[1:]:
                counter[0] += 1
                nxt = f"ch{counter[0]}"
                lines.append(f"  wire signed [{A - 1}:0] {nxt} = {ext(cur)} + {ext(t)};")
                cur = nxt
            return cur

        half = names[: max(2, (len(names) + 1) // 2)]
        rest = names[len(half):]
        cur = chain(half)
        mids: list[str] = []
        if mid_register and rest:
            mids = [cur] + rest
            cur, rest = f"{cur}_r", [f"{r}_r" for r in rest]
        return lines, chain([cur] + rest), mids
    if cfg.reducer == "tree":
        level, cur, mids = 0, list(names), []
        total_levels = max(1, (len(names) - 1).bit_length())
        cut_level = (total_levels + 1) // 2
        while len(cur) > 1:
            nxt = []
            for g in range(0, len(cur) - 1, 2):
                name = f"t{level}_{g // 2}"
                lines.append(f"  wire signed [{A - 1}:0] {name} = {ext(cur[g])} + {ext(cur[g + 1])};")
                nxt.append(name)
            if len(cur) % 2:
                nxt.append(cur[-1])
            level += 1
            if mid_register and level == cut_level and len(nxt) > 1:
                mids = list(nxt)
                nxt = [f"{n}_r" for n in nxt]
            cur = nxt
        return lines, cur[0], mids
    if cfg.reducer == "csa":
        # Two's-complement rows at the accumulator width, compressed modulo 2^A: exact for the
        # final sum whenever that sum fits, which the width guarantees.
        rows = []
        for j, (t, w) in enumerate(terms):
            lines.append(f"  wire [{A - 1}:0] cr{j} = {_sext(t, w, A)};")
            rows.append(f"cr{j}")
        csa, (r0, r1) = _csa_layers(rows, A, "r")
        lines += csa
        mids = [r0, r1] if mid_register else []
        if mid_register:
            r0, r1 = f"{r0}_r", f"{r1}_r"
        lines.append(f"  wire signed [{A - 1}:0] csum = $signed({r0} + {r1});")
        return lines, "csum", mids
    raise ValueError(cfg.reducer)


# ---- the PE --------------------------------------------------------------------------------------

def generate(cfg: PeConfig, shape: Shape, *, module_name: str = "mac_pe",
             invented: dict[str, str] | None = None) -> Design:
    """The PE for `cfg` at `shape`. `invented` maps a multiplier name to its module source."""
    invented = invented or {}
    if cfg.multiplier not in MULTIPLIERS and cfg.multiplier not in invented:
        raise ValueError(f"unknown multiplier {cfg.multiplier!r}")
    if cfg.reducer not in REDUCERS:
        raise ValueError(f"unknown reducer {cfg.reducer!r}")
    L, IN, W, P, A = shape.lanes, shape.in_bits, shape.w_bits, shape.product_bits, shape.acc_bits
    mul_gen = _MULTIPLIER_GEN.get(cfg.multiplier) or _mul_invented(cfg.multiplier)

    ports = [f"  input  logic signed [{IN - 1}:0] a{i}" for i in range(L)]
    ports += [f"  input  logic signed [{W - 1}:0] w{i}" for i in range(L)]
    if shape.accumulate:
        ports.append(f"  input  logic signed [{A - 1}:0] acc_in")
    ports.append(f"  output logic signed [{A - 1}:0] acc")
    if cfg.clocked:
        ports = ["  input  logic clk", "  input  logic rst_n", "  input  logic start",
                 "  output logic done"] + ports

    body: list[str] = []
    for i in range(L):
        body.append(f"  wire signed [{P - 1}:0] p{i};")
    for i in range(L):
        body += mul_gen(i, shape)

    stage_products = cfg.pipeline >= 1
    stage_output = cfg.pipeline >= 2
    stage_mid = cfg.pipeline >= 3
    terms: list[tuple[str, int]] = [(f"p{i}", P) for i in range(L)]
    if shape.accumulate:
        terms.append(("acc_in", A))
    regs: list[tuple[str, str, int, bool]] = []       # (reg name, source, width, signed)
    if stage_products:
        for name, width in terms:
            regs.append((f"{name}_r", name, width, True))
        terms = [(f"{name}_r", width) for name, width in terms]

    red_lines, total, mids = _reduce(cfg, shape, terms, stage_mid)
    body += red_lines
    # A mid-cut wire is one of the reduction's own: a signed partial sum (chain, tree), an
    # unsigned carry-save row (csa), or, in the chain, a not-yet-added input term.
    widths = {name: width for name, width in terms}
    for m in mids:
        signed = cfg.reducer != "csa"
        regs.append((f"{m}_r", m, widths.get(m, A), signed))

    if stage_output:
        regs.append(("acc_r", total, A, True))
        body.append("  assign acc = acc_r;")
    else:
        body.append(f"  assign acc = {total};")

    if cfg.clocked:
        decls = [f"  reg {'signed ' if sg else ''}[{w - 1}:0] {name};" for name, _, w, sg in regs]
        n = cfg.pipeline
        decls.append(f"  reg [{n - 1}:0] busy;" if n > 1 else "  reg busy;")
        assigns = [f"      {name} <= {src};" for name, src, _, _ in regs]
        resets = [f"      {name} <= {w}'d0;" for name, _, w, _ in regs]
        if n > 1:
            shift = f"      busy <= {{busy[{n - 2}:0], start}};"
            done = f"  assign done = busy[{n - 1}];"
        else:
            shift = "      busy <= start;"
            done = "  assign done = busy;"
        body += decls + [
            "  always @(posedge clk or negedge rst_n) begin",
            "    if (!rst_n) begin",
            f"      busy <= {n}'d0;",
            *resets,
            "    end else begin",
            shift,
            *assigns,
            "    end",
            "  end",
            done,
        ]

    header = (f"// {cfg.rtl_label}: {shape.describe()} -- generated by flux_macarray.rtl\n"
              f"module {module_name} (\n" + ",\n".join(ports) + "\n);\n")
    source = header + "\n".join(body) + "\nendmodule\n"
    extra = {cfg.multiplier: invented[cfg.multiplier]} if cfg.multiplier in invented else {}
    return Design(module_name=module_name, source=source, extra_sources=extra, config=cfg,
                  shape=shape)


__all__ = ["Design", "generate"]
