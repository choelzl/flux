"""Multi-module composition (docs/decisions.md D48): wires already-verified leaf modules
together into a top-level composite module, verified end-to-end against its own test vectors —
the "many different and various designs," not just isolated toy modules, half of this framework's
original goal.

**The composite wiring is deterministically generated, never LLM-authored — the same
"verification owns structure, the LLM owns only leaf behavior" split D39 already established for
port binding.** An LLM already proved (D40/D44) it can reliably write one module's internal
behavior; asking it to also write correct multi-module instantiation and net wiring would
reintroduce exactly the class of bug D39's harness/driver split was built to eliminate (a
plausible-looking but wrong connection is much harder to spot by eye than a wrong `+` vs `-`).
So a `CompositionSpec` is a real, declarative netlist — instances plus a net name per
(instance, port) pair — and `generate_composite_module_sv` emits the top-level module
mechanically from it, the same role `driver_gen.py` already plays for testbenches.

**Clocked leaves, real support (D49 addendum) — found broken by direct empirical check, not
assumed to work just because D48 and D49 each worked in isolation.** A composite instantiating a
clocked leaf (e.g. a real D flip-flop) generated an instantiation missing that leaf's `clk`/
`rst_n` connections entirely (`Reg r1 (.d(din), .q(qout));` for a module that needs four ports,
not two) — real Verilator would reject that outright. `CompositionSpec.is_clocked` is now
computed from its own instances (true if *any* leaf is clocked — one piece of real state inside a
composite makes the whole system's behavior cycle-dependent from the outside, whether or not
every leaf is itself clocked); when true, the composite gets its own implicit `clk`/`rst_n` (the
same harness-owned convention every clocked module already uses) fanned out to every clocked
instance, and the composite's own top-level testbench becomes clock-synchronized too — the same
"one clock domain, driven top-down" shape a real, hand-composed clocked design would use.
"""

from __future__ import annotations

from typing import Any

from flux_codegen_harness_spec import (CompositionSpec, DesignSpec,  # noqa: F401
                                       HarnessRunResult, Instance, InvalidSpecError)
from flux_codegen_harness_spec import composition_spec_from_dict as _parse_composition

from .build import compile_and_run
from .cache import ToolResultCache
from .driver_gen import CLOCK_PORT, RESET_PORT, _verilog_type
from .keywords import check_not_reserved
from .synth import SynthesisResult, synthesize_and_measure


def composition_spec_from_dict(doc: dict[str, Any], *, leaf_specs: dict[str, DesignSpec]
                               ) -> CompositionSpec:
    """Validate and parse a composition doc, against Verilog/SystemVerilog reserved identifiers.

    The netlist model and every structural check are `flux_codegen_harness_spec`'s (D453) --
    one parser for both harnesses, after the two copies drifted apart: this one had gained a
    top-level port identifier check the SystemC one lacked, and lacked the top-port width
    parsing and net-width conflict check the SystemC one had gained (D203). What is supplied
    here is the language: its reserved words and its name in the messages.
    """
    return _parse_composition(doc, leaf_specs=leaf_specs, check_name=check_not_reserved,
                              language="Verilog/SystemVerilog")


def generate_composite_module_sv(comp_spec: CompositionSpec) -> str:
    """Deterministically emit `module <top>(...); ... endmodule`: declares any internal-only nets
    (net names used in wiring that aren't also top-level ports), then instantiates every named
    instance with named port connections — real Verilog instantiation, never LLM-generated.
    """
    top_port_names = {p.name for p in comp_spec.ports}
    net_dtype: dict[str, str] = {p.name: p.dtype for p in comp_spec.ports}
    for inst in comp_spec.instances:
        for leaf_port in inst.leaf_ports:
            net_dtype[comp_spec.nets[inst.instance_name][leaf_port.name]] = leaf_port.dtype
    internal_nets = sorted(n for n in net_dtype if n not in top_port_names)

    lines: list[str] = []
    data_port_decls = ", ".join(
        f"{'input' if p.dir == 'in' else 'output'} {_verilog_type(p.dtype, p.width)} {p.name}"
        for p in comp_spec.ports
    )
    if comp_spec.is_clocked:
        # Implicit, harness-owned — same convention as every clocked leaf's own module (D49),
        # never a spec-chosen or LLM-chosen name. Fanned out below to every clocked instance.
        port_decls = f"input logic {CLOCK_PORT}, input logic {RESET_PORT}, {data_port_decls}"
    else:
        port_decls = data_port_decls
    lines.append(f"module {comp_spec.top_module_name} ({port_decls});")
    for net_name in internal_nets:
        # At its REAL width, from the parser's own net bookkeeping (D453). Declared without it,
        # every internal net was 32 bits wide whatever it connected, so a 16-bit leaf-to-leaf
        # connection became a width mismatch Verilator reports against generated code.
        width = comp_spec.net_width.get(net_name) or 32
        lines.append(f"  {_verilog_type(net_dtype[net_name], width)} {net_name};")
    lines.append("")
    for inst in comp_spec.instances:
        conn = comp_spec.nets[inst.instance_name]
        data_port_map = ", ".join(f".{leaf_port.name}({conn[leaf_port.name]})" for leaf_port in inst.leaf_ports)
        if inst.is_clocked:
            port_map = f".{CLOCK_PORT}({CLOCK_PORT}), .{RESET_PORT}({RESET_PORT}), {data_port_map}"
        else:
            port_map = data_port_map
        lines.append(f"  {inst.module_name} {inst.instance_name} ({port_map});")
    lines.append("endmodule")
    lines.append("")
    return "\n".join(lines)


def _resolve_leaf_sources(leaf_sources: dict[str, str], comp_spec: CompositionSpec) -> dict[str, str]:
    """Cross-check `leaf_sources` against the spec's instances before use — a missing key (e.g. a
    `"adder2"` vs `"Adder2"` case slip through the MCP surface) previously surfaced as a bare
    `KeyError` here instead of the `InvalidSpecError` every other spec mistake in this file
    produces (review finding). `composition_spec_from_dict` validates instances against
    `leaf_specs` only; `leaf_sources` arrives separately at this later call, so it needs its own
    check at this boundary.
    """
    needed = {inst.module_name for inst in comp_spec.instances}
    missing = needed - leaf_sources.keys()
    if missing:
        raise InvalidSpecError(
            f"leaf_sources is missing source for instantiated module(s) {sorted(missing)}; "
            f"got keys {sorted(leaf_sources)}"
        )
    return {name: leaf_sources[name] for name in sorted(needed)}


def compile_and_run_composite(
    leaf_sources: dict[str, str],
    comp_spec: CompositionSpec,
    *,
    timeout_s: float = 120.0,
    keep_workdir: bool = False,
) -> HarnessRunResult:
    """Generate the composite top-level module, compile it against real Verilator alongside every
    referenced leaf's already-verified source (`leaf_sources`, keyed by `module_name` — the exact
    strings `flux_generate_rtl_module` already verified, never re-derived), and run the same
    real end-to-end test-vector check every other harness entry point in this repo uses.
    """
    top_spec = DesignSpec(
        schema_version="0.1.0",
        id=comp_spec.top_module_name,
        module_name=comp_spec.top_module_name,
        ports=comp_spec.ports,
        behavior=f"composite of {[i.module_name for i in comp_spec.instances]}",
        test_vectors=comp_spec.test_vectors,
        is_clocked=comp_spec.is_clocked,
    )
    composite_source = generate_composite_module_sv(comp_spec)
    extra_sources = _resolve_leaf_sources(leaf_sources, comp_spec)
    return compile_and_run(composite_source, top_spec, timeout_s=timeout_s, keep_workdir=keep_workdir, extra_sources=extra_sources)


def synthesize_composite(
    leaf_sources: dict[str, str], comp_spec: CompositionSpec, *, timeout_s: float = 60.0,
    cache: ToolResultCache | None = None,
) -> SynthesisResult:
    """Real gate-level synthesis of the *whole* composite (docs/decisions.md D52) — the same real
    Yosys flow `synth.synthesize_and_measure` already gives single modules (D47), now closing the
    gap D47/D51 both named directly: composed designs had no ranking signal at all. Synthesizes
    the generated top-level module together with every real leaf source it instantiates (`Yosys`
    flattens the hierarchy during `synth`, so `total_cells` genuinely reflects the whole design,
    not just the top-level wrapper's own wiring logic).

    `cache` (docs/decisions.md D89) passes straight through to `synthesize_and_measure` — no
    separate cache-key derivation needed here: this function's own real content-hash key is
    exactly `(composite_source, top_module_name, extra_sources)`, the same real inputs
    `synthesize_and_measure` already keys on, since this is a thin, deterministic wrapper around
    it (`generate_composite_module_sv` is itself pure — the same `comp_spec` always generates the
    same real source text).
    """
    composite_source = generate_composite_module_sv(comp_spec)
    extra_sources = _resolve_leaf_sources(leaf_sources, comp_spec)
    return synthesize_and_measure(
        composite_source, comp_spec.top_module_name, timeout_s=timeout_s, extra_sources=extra_sources,
        cache=cache,
    )
