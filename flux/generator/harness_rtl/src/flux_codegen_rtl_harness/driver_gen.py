"""Deterministic SystemVerilog testbench generation (docs/decisions.md D43/D49) — the actual
verification code, hand-written here and templated per `DesignSpec`, never LLM-generated. Mirrors
`flux_codegen_systemc_harness.driver_gen`'s role exactly: real VCD tracing (`$dumpfile`/
`$dumpvars`, real Verilator waveform output, not skipped or mocked), then drives every
`test_vectors` entry and self-checks every output, printing the same `RESULT PASS/FAIL
vectors=<N> passed=<M>` convention every other adapter/harness in this repo uses.

**Sequential (`DesignSpec.is_clocked=True`) DUTs, real support (D49) — not the "not built yet"
placeholder D43 originally shipped.** `clk`/`rst_n` are *implicit*, harness-owned ports, exactly
like the "verification owns structure" split every other real finding in this repo's generation
framework already established (D39's port binding, D48's deterministic composite wiring): the
DUT module still declares them itself (SystemVerilog gives no other way to reference a clock
inside `always_ff`), but their *names*, *timing*, and *reset sequencing* are a fixed harness
convention the spec never chooses and the LLM is only ever told about, never free to invent. Each
`test_vectors` entry represents one clock cycle: inputs are set combinationally, then the harness
waits a real `@(posedge clk)` edge (plus a `#1` settle delay for non-blocking-assignment outputs
to propagate) before sampling outputs — the standard cycle-accurate testbench idiom, not
something novel invented here.
"""

from __future__ import annotations

from flux_codegen_systemc_harness import DesignSpec

from .keywords import check_not_reserved

CLOCK_PORT = "clk"
RESET_PORT = "rst_n"  # active-low, the conventional default
CLOCK_PERIOD_NS = 10
RESET_CYCLES = 2

# Latency-measuring mode (docs/decisions.md D115). Same "verification owns structure" split as
# clk/rst_n: harness-owned names, fixed protocol, never chosen by the spec. `start` is pulsed for
# exactly one cycle; the DUT raises `done` for one cycle when its outputs are valid, and the
# harness counts the clock edges between.
START_PORT = "start"
DONE_PORT = "done"
# A generated design that never raises `done` would hang the simulator forever — the harness
# would look like an infinite test rather than a failed one. Bounded, and a timeout is reported
# as a real per-vector failure with the bound named, not as a hang.
MAX_LATENCY_CYCLES = 10_000


def _verilog_type(dtype: str, bits: int = 32) -> str:
    """SystemVerilog type for a port. `bits` is the port's own declared width (docs/decisions.md
    D202) — 32 when a spec does not say, which is every spec written before widths existed."""
    return f"logic signed [{bits - 1}:0]" if dtype == "int" else "logic"


def _port_type(port) -> str:
    return _verilog_type(port.dtype, port.width)


def _array_suffix(port) -> str:
    return "".join(f" [0:{d - 1}]" for d in (port.dims or ()))


def _array_elements(dims: tuple[int, ...], value) -> list[tuple[tuple[int, ...], object]]:
    """Flatten a nested list into `((i, j...), element)` pairs in declaration order. The spec layer
    has already shape-checked `value` against `dims`, so this never has to guess."""
    if len(dims) == 1:
        return [((i,), value[i]) for i in range(dims[0])]
    out: list[tuple[tuple[int, ...], object]] = []
    for i in range(dims[0]):
        for idx, element in _array_elements(dims[1:], value[i]):
            out.append(((i, *idx), element))
    return out


def _index(name: str, idx: tuple[int, ...]) -> str:
    return name + "".join(f"[{i}]" for i in idx)


def _verilog_literal(dtype: str, value: object, bits: int = 32) -> str:
    """A golden value as a SystemVerilog literal, sized to the port it is assigned to.

    An unsized decimal is 32 bits wide in SystemVerilog, so a value needing more — which is exactly
    what a wide accumulator holds — triggers WIDTHEXPAND under this repo's strict `-Wall` and the
    testbench fails to compile. Caught by Verilator on a 38-bit `acc` from a 64-lane 16-bit
    derivation (docs/decisions.md D202); the 32-bit path is unchanged, since `bits` defaults to 32
    and a sized literal there is equivalent.
    """
    if dtype == "bool":
        return "1'b1" if value else "1'b0"
    number = int(value)
    if bits <= 32:
        return str(number)  # signed decimal — Verilog handles negatives on a signed reg
    # Sized *signed* literals cannot carry a leading minus in all positions, so negate the sized
    # magnitude instead of writing a sized negative.
    magnitude = f"{bits}'sd{abs(number)}"
    return f"-{magnitude}" if number < 0 else magnitude


def _element_loop_sv(port, *, indent: str) -> list[str]:
    """Nested `for` loops comparing every element of an array output against its golden array,
    counting mismatches and printing the first few with their real indices."""
    dims = port.dims or ()
    ivars = ["__flux_i0", "__flux_i1"][: len(dims)]
    idx = "".join(f"[{v}]" for v in ivars)
    lines = []
    for depth, (var, size) in enumerate(zip(ivars, dims)):
        lines.append(f"{indent}{'  ' * depth}for ({var} = 0; {var} < {size}; {var} = {var} + 1)")
    body = indent + "  " * len(dims)
    lines.append(f"{body}if ({port.name}{idx} !== __flux_exp_{port.name}{idx}) begin")
    lines.append(f"{body}  __flux_arr_errs = __flux_arr_errs + 1;")
    # `[%0d][%0d]`, matching how the index is actually written — a mismatch report a reader can
    # paste back into the source beats one they have to re-punctuate.
    fmt = "".join("[%0d]" for _ in ivars)
    lines.append(
        f'{body}  if (__flux_arr_errs <= 5) $display("  MISMATCH {port.name}{fmt} got=%0d '
        f'expected=%0d", {", ".join(ivars)}, {port.name}{idx}, __flux_exp_{port.name}{idx});'
    )
    lines.append(f"{body}end")
    return lines


def generate_testbench_sv(spec: DesignSpec, *, vcd_path: str) -> str:
    """Return a complete, compilable `testbench.sv` for `spec` — instantiates a DUT module named
    `spec.module_name` (the caller writes it to a separate `dut.sv` file, see `build.py`) and
    drives/checks every test vector. `vcd_path` becomes the real Verilator-dumped `.vcd` on disk.
    Branches on `spec.is_clocked` — see module docstring for the real difference in what gets
    generated (a free-running clock + reset sequence + edge-synchronized vector driving, vs. the
    original combinational-only `#1`-settle driving).
    """
    check_not_reserved(spec.module_name, context="module_name")
    for p in spec.ports:
        check_not_reserved(p.name, context="port name")

    in_ports = [p for p in spec.ports if p.dir == "in"]
    out_ports = [p for p in spec.ports if p.dir == "out"]

    lines: list[str] = []
    lines.append("`timescale 1ns/1ps")
    lines.append("module testbench;")
    if spec.is_clocked:
        lines.append(f"  logic {CLOCK_PORT};")
        lines.append(f"  logic {RESET_PORT};")
    if spec.measures_latency:
        lines.append(f"  logic {START_PORT};")
        lines.append(f"  logic {DONE_PORT};")
        lines.append("  int __flux_cycles;")
    # `logic` (SystemVerilog) is the whole type — no `reg`/`wire` prefix needed or valid alongside it.
    # An array port is one unpacked array, not N separate nets (docs/decisions.md D120/D121) — so
    # a realistic reduction length costs one port instead of thousands, and a real operand memory
    # (`i_mem[B][C]`) is expressible at all.
    for p in in_ports + out_ports:
        lines.append(f"  {_port_type(p)} {p.name}{_array_suffix(p)};")
    for p in out_ports:
        if p.is_array:
            # A separate golden array, so the comparison is element-wise against real data rather
            # than a thousand-term boolean expression.
            lines.append(f"  {_port_type(p)} __flux_exp_{p.name}{_array_suffix(p)};")
    if any(p.is_array for p in out_ports):
        lines.append("  integer __flux_arr_errs;")
        lines.append("  integer __flux_i0;")
        lines.append("  integer __flux_i1;")
    lines.append("")
    # `__flux_`-prefixed names throughout: a real bug found via composition testing (docs/
    # decisions.md D48), not hypothetical — a composite whose own top-level port happened to be
    # named "total" collided with this harness's own bookkeeping variable of the same name (both
    # declared in the same `testbench` module scope), a real Verilator "Duplicate declaration"
    # error. Prefixed every harness-internal identifier so no legitimate spec-chosen port/net
    # name can collide with it, applied to the DUT instance name too for the same reason.
    data_port_map = ", ".join(f".{p.name}({p.name})" for p in spec.ports)
    if spec.measures_latency:
        port_map = (
            f".{CLOCK_PORT}({CLOCK_PORT}), .{RESET_PORT}({RESET_PORT}), "
            f".{START_PORT}({START_PORT}), .{DONE_PORT}({DONE_PORT}), {data_port_map}"
        )
    elif spec.is_clocked:
        port_map = f".{CLOCK_PORT}({CLOCK_PORT}), .{RESET_PORT}({RESET_PORT}), {data_port_map}"
    else:
        port_map = data_port_map
    lines.append(f"  {spec.module_name} __flux_dut ({port_map});")
    lines.append("")

    if spec.is_clocked:
        lines.append(f"  initial {CLOCK_PORT} = 0;")
        lines.append(f"  always #{CLOCK_PERIOD_NS / 2:g} {CLOCK_PORT} = ~{CLOCK_PORT};")
        lines.append("")

    lines.append("  integer __flux_total;")
    lines.append("  integer __flux_passed;")
    lines.append("")
    lines.append("  initial begin")
    lines.append("    __flux_total = 0;")
    if spec.measures_latency:
        lines.append(f"    {START_PORT} = 1'b0;")
    lines.append("    __flux_passed = 0;")
    lines.append(f'    $dumpfile("{vcd_path}");')
    lines.append("    $dumpvars(0, testbench);")
    lines.append("")

    if spec.is_clocked:
        lines.append(f"    {RESET_PORT} = 0;")
        for _ in range(RESET_CYCLES):
            lines.append(f"    @(posedge {CLOCK_PORT});")
        # A real, found race (docs/decisions.md D49): deasserting reset at the exact same
        # simulation instant as the last reset-hold edge left it ambiguous which value the DUT's
        # own always_ff block observed for that edge — a real, consistently-reproduced off-by-one
        # (every sampled value one real clock cycle ahead of what the test vectors expected), not
        # a flaky/nondeterministic one. A `#1` settle delay puts the deassertion strictly between
        # edges, the standard verification practice of never toggling a synchronous control
        # signal coincident with the edge meant to observe its old value.
        lines.append("    #1;")
        lines.append(f"    {RESET_PORT} = 1;")
        lines.append("")

    for i, vec in enumerate(spec.test_vectors):
        for p in in_ports:
            if p.is_array:
                for idx, element in _array_elements(p.dims, vec.inputs[p.name]):
                    lines.append(f"    {_index(p.name, idx)} = {_verilog_literal(p.dtype, element, p.width)};")
            else:
                lines.append(f"    {p.name} = {_verilog_literal(p.dtype, vec.inputs[p.name], p.width)};")
        for p in out_ports:
            if p.is_array:
                for idx, element in _array_elements(p.dims, vec.expected[p.name]):
                    lines.append(
                        f"    {_index('__flux_exp_' + p.name, idx)} = "
                        f"{_verilog_literal(p.dtype, element, p.width)};"
                    )
        if spec.measures_latency:
            # Pulse `start` for exactly one cycle, then count edges until the DUT raises `done`.
            # The count excludes the start pulse, so a DUT finishing in one cycle reports 1 —
            # the smallest honest answer, not 0.
            lines.append(f"    {START_PORT} = 1'b1;")
            lines.append(f"    @(posedge {CLOCK_PORT});")
            lines.append("    #1;")
            lines.append(f"    {START_PORT} = 1'b0;")
            lines.append("    __flux_cycles = 0;")
            lines.append(f"    while (({DONE_PORT} !== 1'b1) && (__flux_cycles < {MAX_LATENCY_CYCLES})) begin")
            lines.append(f"      @(posedge {CLOCK_PORT});")
            lines.append("      #1;")
            lines.append("      __flux_cycles = __flux_cycles + 1;")
            lines.append("    end")
            lines.append(f'    $display("CYCLES vector={i} n=%0d", __flux_cycles);')
            lines.append(f"    if (__flux_cycles >= {MAX_LATENCY_CYCLES})")
            lines.append(
                f'      $display("VECTOR {i} FAIL never asserted {DONE_PORT} within '
                f'{MAX_LATENCY_CYCLES} cycles");'
            )
        elif spec.is_clocked:
            lines.append(f"    @(posedge {CLOCK_PORT});")
            lines.append("    #1;")  # let non-blocking (`<=`) output assignments settle
        else:
            lines.append("    #1;")
        lines.append("    __flux_total = __flux_total + 1;")
        array_outs = [p for p in out_ports if p.is_array]
        scalar_outs = [p for p in out_ports if not p.is_array]
        if array_outs:
            # Element-wise, in a real loop rather than an unrolled boolean: the failure a caller
            # needs is *which* elements disagree, not that the array as a whole did.
            lines.append("    __flux_arr_errs = 0;")
            for p in array_outs:
                loops = _element_loop_sv(p, indent="    ")
                lines.extend(loops)
        checks = " && ".join(
            f"({p.name} === {_verilog_literal(p.dtype, vec.expected[p.name], p.width)})" for p in scalar_outs
        )
        if array_outs:
            checks = f"({checks}) && (__flux_arr_errs == 0)" if checks else "(__flux_arr_errs == 0)"
        if spec.measures_latency:
            # A timed-out vector cannot pass even if the outputs happen to hold the right value —
            # the DUT never claimed the result was ready.
            checks = f"({checks}) && (__flux_cycles < {MAX_LATENCY_CYCLES})"
        lines.append(f"    if ({checks}) begin")
        lines.append("      __flux_passed = __flux_passed + 1;")
        lines.append("    end else begin")
        fail_fmt = " ".join(f"{p.name}=%0d" for p in scalar_outs)
        fail_args = ", ".join(p.name for p in scalar_outs)
        if array_outs:
            fail_fmt = (fail_fmt + " " if fail_fmt else "") + "array_mismatches=%0d"
            fail_args = (fail_args + ", " if fail_args else "") + "__flux_arr_errs"
        lines.append(f'      $display("VECTOR {i} FAIL {fail_fmt}", {fail_args});')
        lines.append("    end")
        lines.append("")

    lines.append("    if (__flux_passed == __flux_total)")
    lines.append('      $display("RESULT PASS vectors=%0d passed=%0d", __flux_total, __flux_passed);')
    lines.append("    else")
    lines.append('      $display("RESULT FAIL vectors=%0d passed=%0d", __flux_total, __flux_passed);')
    lines.append("    $finish;")
    lines.append("  end")
    lines.append("endmodule")
    lines.append("")
    return "\n".join(lines)
