"""Deterministic SystemVerilog testbench generation (D43/D49): verification code templated per
`DesignSpec`, never LLM-generated: VCD
tracing (`$dumpfile`/`$dumpvars`), then drives every `test_vectors` entry and self-checks every
output, printing the shared `RESULT PASS/FAIL vectors=<N> passed=<M>` line.

Sequential DUTs (`DesignSpec.is_clocked=True`, D49): `clk`/`rst_n` are implicit, harness-owned
ports -- the DUT declares them (it must, to use them in `always_ff`), but their names, timing
and reset sequencing are a fixed harness convention the spec never chooses. Each `test_vectors`
entry is one clock cycle: inputs are set, the harness waits `@(posedge clk)` plus a `#1` settle
for non-blocking outputs, then samples.
"""

from __future__ import annotations

from flux_codegen_harness_spec import DesignSpec

from .keywords import check_not_reserved

CLOCK_PORT = "clk"
RESET_PORT = "rst_n"  # active-low, the conventional default
CLOCK_PERIOD_NS = 10
RESET_CYCLES = 2

# Latency-measuring mode (D115): harness-owned names and protocol, like clk/rst_n. `start` is
# pulsed for exactly one cycle; the DUT raises `done` for one cycle when its outputs are valid,
# and the harness counts the clock edges between.
START_PORT = "start"
DONE_PORT = "done"
# Bound on waiting for `done`, so a DUT that never raises it is a reported per-vector failure
# rather than a hung simulator.
MAX_LATENCY_CYCLES = 10_000


def _verilog_type(dtype: str, bits: int = 32) -> str:
    """SystemVerilog type for a port. `bits` is the port's declared width (D202), 32 by default."""
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

    An unsized decimal is 32 bits, so a wider value (e.g. a wide accumulator) triggers
    WIDTHEXPAND under strict `-Wall` and the testbench fails to compile (D202). At 32 bits a
    sized literal is equivalent.
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
    # `[%0d][%0d]`, matching how the index is written in source.
    fmt = "".join("[%0d]" for _ in ivars)
    lines.append(
        f'{body}  if (__flux_arr_errs <= 5) $display("  MISMATCH {port.name}{fmt} got=%0d '
        f'expected=%0d", {", ".join(ivars)}, {port.name}{idx}, __flux_exp_{port.name}{idx});'
    )
    lines.append(f"{body}end")
    return lines


def generate_testbench_sv(spec: DesignSpec, *, vcd_path: str) -> str:
    """Return a complete, compilable `testbench.sv` for `spec`: instantiates a DUT module named
    `spec.module_name` (the caller writes it to a separate `dut.sv`, see `build.py`) and
    drives/checks every test vector. `vcd_path` is where Verilator dumps the `.vcd`. Branches
    on `spec.is_clocked` (clock + reset + edge-synchronised driving vs `#1`-settle driving).
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
    # `logic` is the whole type (no `reg`/`wire` prefix). An array port is one unpacked array,
    # not N separate nets (D120/D121), so operand memories like `i_mem[B][C]` are expressible.
    for p in in_ports + out_ports:
        lines.append(f"  {_port_type(p)} {p.name}{_array_suffix(p)};")
    for p in out_ports:
        if p.is_array:
            # A separate golden array, so the comparison is element-wise.
            lines.append(f"  {_port_type(p)} __flux_exp_{p.name}{_array_suffix(p)};")
    if any(p.is_array for p in out_ports):
        lines.append("  integer __flux_arr_errs;")
        lines.append("  integer __flux_i0;")
        lines.append("  integer __flux_i1;")
    lines.append("")
    # `__flux_`-prefixed names for every harness-internal identifier (and the DUT instance), so
    # no spec-chosen port or net name can collide with them (D48).
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
        # Deassert reset `#1` after the last reset-hold edge, never on it: toggling on the edge
        # left it ambiguous which value the DUT's always_ff saw, and every sampled value came
        # out one cycle early (D49).
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
            # The count excludes the start pulse, so a DUT finishing in one cycle reports 1.
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
            # Element-wise in a loop, so a failure says which elements disagree.
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
            # A timed-out vector fails even if the outputs hold the right value: the DUT never
            # claimed the result was ready.
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
