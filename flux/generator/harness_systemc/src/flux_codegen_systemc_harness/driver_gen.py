"""Deterministic SystemC driver generation (docs/decisions.md D39/D54) — the actual verification
code, hand-written here and templated per `DesignSpec`, never LLM-generated. Emits a real driver:
binds `sc_signal`s to every declared port, enables real VCD tracing (`sc_create_vcd_trace_file` /
`sc_trace`, one signal per port — not a mocked or skipped step), then drives every `test_vectors`
entry and self-checks every output, printing the same `RESULT PASS/FAIL vectors=<N> passed=<M>`
convention `evaluators/rtl`/`evaluators/systemc` already use so downstream tooling can parse it
identically.

**Sequential (`DesignSpec.is_clocked=True`) DUTs, real support (D54) — not the "not built yet"
placeholder D39 originally shipped, closing the asymmetry every RTL clocked-design decision since
D49 left standing.** `clk`/`rst_n` are implicit, harness-owned ports, same convention as the RTL
harness's D49. A real, structural difference from the RTL/Verilator harness, not a cosmetic one:
SystemC's `wait()` (used to synchronize to a clock edge) can only be called inside an `SC_THREAD`
process, never in a flat procedural driver — so the clocked path emits a real `Testbench`
`SC_MODULE` with an `SC_THREAD` sensitive to `clk.pos()`, not the combinational path's flat
`sc_main()`. A real `sc_clock` (SystemC's own built-in clock generator, not hand-rolled) drives
the clock; `wait(RESET_CYCLES)` (SystemC's own "wait for the Nth sensitivity-list event" idiom)
holds reset for a fixed number of real edges, then each vector applies inputs and calls a bare
`wait()` for the next edge before sampling outputs — the SystemC-idiomatic equivalent of the RTL
harness's `@(posedge clk)`.
"""

from __future__ import annotations

from typing import Any

from .errors import InvalidSpecError
from .spec import DesignSpec

RESET_CYCLES = 2
CLOCK_PERIOD_NS = 10
# Named for `compose.py` (docs/decisions.md D55) to reuse rather than re-hardcode — the literal
# generated identifiers below still spell "clk"/"rst_n" directly (unchanged, already verified);
# these constants exist so composition's own clk/rst_n fan-out can't silently drift from them.
CLOCK_PORT = "clk"
RESET_PORT = "rst_n"


def _cpp_literal(dtype: str, value: Any, bits: int = 32) -> str:
    """A golden value as a C++ literal, sized to the port it is compared against.

    A bare integer literal is `int` in C++, so a value past 32 bits is a compile error (or worse,
    an implementation-defined truncation) — the same failure the SystemVerilog side hit with unsized
    decimals (docs/decisions.md D202/D203). `LL` past 32 bits, unchanged below it.
    """
    if dtype == "bool":
        return "true" if value else "false"
    number = int(value)
    return f"{number}LL" if bits > 32 else str(number)


def generate_driver_cpp(spec: DesignSpec, *, vcd_stem: str) -> str:
    """Return a complete, compilable `driver.cpp` for `spec` — `#include`s a DUT header named
    `"dut.h"` (the caller writes the LLM-generated module there, see `build.py`) and defines the
    real driver. `vcd_stem` becomes `<vcd_stem>.vcd` on disk via SystemC's own VCD writer.
    Dispatches on `spec.is_clocked` — see module docstring for why the clocked path is a
    structurally different `SC_MODULE`/`SC_THREAD`, not just more statements in `sc_main()`.
    """
    arrays = [p.name for p in spec.ports if p.is_array]
    if arrays:
        # Refused outright rather than generating `sc_signal<int>` for something that is not an
        # int (docs/decisions.md D120): array ports are an RTL-harness capability today, and a
        # SystemC driver would need `sc_vector`/array-of-signal binding to mean anything. Silently
        # dropping the depth would compile and then verify the wrong design.
        raise InvalidSpecError(
            f"array ports {sorted(arrays)} are not supported by the SystemC harness "
            "(docs/decisions.md D120) — they are RTL-only in v0.1."
        )
    if spec.is_clocked:
        return _generate_clocked_driver_cpp(spec, vcd_stem=vcd_stem)
    return _generate_combinational_driver_cpp(spec, vcd_stem=vcd_stem)


def _generate_combinational_driver_cpp(spec: DesignSpec, *, vcd_stem: str) -> str:
    in_ports = [p for p in spec.ports if p.dir == "in"]
    out_ports = [p for p in spec.ports if p.dir == "out"]

    lines: list[str] = []
    lines.append("#include <systemc.h>")
    lines.append("#include <cstdio>")
    lines.append('#include "dut.h"')
    lines.append("")
    lines.append("int sc_main(int argc, char* argv[]) {")
    for p in spec.ports:
        lines.append(f"    sc_signal<{p.cpp_type}> sig_{p.name};")
    lines.append(f"    {spec.module_name} dut(\"dut\");")
    for p in spec.ports:
        lines.append(f"    dut.{p.name}(sig_{p.name});")
    lines.append("")
    lines.append(f'    sc_trace_file* tf = sc_create_vcd_trace_file("{vcd_stem}");')
    for p in spec.ports:
        lines.append(f'    sc_trace(tf, sig_{p.name}, "{p.name}");')
    lines.append("")
    lines.append("    int total = 0, passed = 0;")
    lines.append("")

    for i, vec in enumerate(spec.test_vectors):
        for p in in_ports:
            lines.append(f"    sig_{p.name}.write({_cpp_literal(p.dtype, vec.inputs[p.name], p.width)});")
        lines.append("    sc_start(1, SC_NS);")
        lines.append("    total++;")
        checks = " && ".join(
            f"sig_{p.name}.read() == {_cpp_literal(p.dtype, vec.expected[p.name], p.width)}" for p in out_ports
        )
        lines.append(f"    if ({checks}) {{")
        lines.append("        passed++;")
        lines.append("    } else {")
        lines.append(f'        std::cout << "VECTOR {i} FAIL ";')
        for p in out_ports:
            lines.append(f'        std::cout << "{p.name}=" << sig_{p.name}.read() << " ";')
        lines.append(f'        std::cout << std::endl;')
        lines.append("    }")
        lines.append("")

    lines.append('    sc_close_vcd_trace_file(tf);')
    lines.append('    std::printf("RESULT %s vectors=%d passed=%d\\n", (passed == total) ? "PASS" : "FAIL", total, passed);')
    lines.append("    return (passed == total) ? 0 : 1;")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _generate_clocked_driver_cpp(spec: DesignSpec, *, vcd_stem: str) -> str:
    in_ports = [p for p in spec.ports if p.dir == "in"]
    out_ports = [p for p in spec.ports if p.dir == "out"]

    lines: list[str] = []
    lines.append("#include <systemc.h>")
    lines.append("#include <cstdio>")
    lines.append('#include "dut.h"')
    lines.append("")
    lines.append("SC_MODULE(Testbench) {")
    lines.append("    sc_in_clk clk;")
    lines.append("    sc_signal<bool> rst_n;")
    for p in spec.ports:
        lines.append(f"    sc_signal<{p.cpp_type}> sig_{p.name};")
    lines.append(f"    {spec.module_name} dut;")
    lines.append("    int total = 0, passed = 0;")
    lines.append("")
    lines.append("    void drive() {")
    lines.append("        rst_n.write(false);")
    lines.append(f"        wait({RESET_CYCLES});")
    lines.append("        rst_n.write(true);")
    # Settle the reset deassertion's own delta before any vector's inputs are written. A DUT
    # process level-sensitive to rst_n re-evaluates in that delta and would otherwise already see
    # the first vector — harmless for a plain flip-flop, one extra silent increment for any
    # accumulating register. Reproduced: an async-sensitive counter failed 0/4, each off by +1
    # (D54 addendum).
    lines.append("        wait(SC_ZERO_TIME);")
    lines.append("")

    for i, vec in enumerate(spec.test_vectors):
        for p in in_ports:
            lines.append(f"        sig_{p.name}.write({_cpp_literal(p.dtype, vec.inputs[p.name], p.width)});")
        lines.append("        wait();")
        # A second real race found the same way (docs/decisions.md D54): the testbench thread
        # resumes on the same clk.pos() event the DUT's own clocked process does — reading an
        # output signal in that same delta cycle sees its value *before* the DUT's write commits
        # (`sc_signal::write()` only takes effect after the delta settles), a real, consistently
        # reproduced one-vector-behind lag, not the reset-timing issue this looked like at first.
        # `wait(SC_ZERO_TIME)` settles the delta (zero real time, unlike the reset fix's `wait(1,
        # SC_NS)`) before any output is sampled.
        lines.append("        wait(SC_ZERO_TIME);")
        lines.append("        total++;")
        checks = " && ".join(
            f"sig_{p.name}.read() == {_cpp_literal(p.dtype, vec.expected[p.name], p.width)}" for p in out_ports
        )
        lines.append(f"        if ({checks}) {{")
        lines.append("            passed++;")
        lines.append("        } else {")
        lines.append(f'            std::cout << "VECTOR {i} FAIL ";')
        for p in out_ports:
            lines.append(f'            std::cout << "{p.name}=" << sig_{p.name}.read() << " ";')
        lines.append('            std::cout << std::endl;')
        lines.append("        }")
        lines.append("")

    lines.append('        std::printf("RESULT %s vectors=%d passed=%d\\n", (passed == total) ? "PASS" : "FAIL", total, passed);')
    lines.append("        sc_stop();")
    lines.append("    }")
    lines.append("")
    lines.append(f'    SC_CTOR(Testbench) : dut("dut") {{')
    lines.append("        dut.clk(clk);")
    lines.append("        dut.rst_n(rst_n);")
    for p in spec.ports:
        lines.append(f"        dut.{p.name}(sig_{p.name});")
    lines.append("        SC_THREAD(drive);")
    lines.append("        sensitive << clk.pos();")
    lines.append("    }")
    lines.append("};")
    lines.append("")
    lines.append("int sc_main(int argc, char* argv[]) {")
    lines.append(f'    sc_clock clk_sig("clk", {CLOCK_PERIOD_NS}, SC_NS);')
    lines.append('    Testbench tb("tb");')
    lines.append("    tb.clk(clk_sig);")
    lines.append("")
    lines.append(f'    sc_trace_file* tf = sc_create_vcd_trace_file("{vcd_stem}");')
    lines.append('    sc_trace(tf, clk_sig, "clk");')
    lines.append('    sc_trace(tf, tb.rst_n, "rst_n");')
    for p in spec.ports:
        lines.append(f'    sc_trace(tf, tb.sig_{p.name}, "{p.name}");')
    lines.append("")
    lines.append("    sc_start();")
    lines.append("")
    lines.append("    sc_close_vcd_trace_file(tf);")
    lines.append("    return (tb.passed == tb.total) ? 0 : 1;")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)
