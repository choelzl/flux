"""Multi-module composition (D48): wires verified leaf modules into a top-level composite,
verified end-to-end against its own test vectors.

The wiring is generated deterministically, never by a model: the model owns leaf behavior,
the harness owns structure (a wrong connection is hard to spot by eye). A `CompositionSpec` is
a declarative netlist (instances plus a net per (instance, port)), and
`generate_composite_module_sv` emits the top module from it.

Clocked leaves: `CompositionSpec.is_clocked` is true if any leaf is clocked; the composite then
gets an implicit `clk`/`rst_n` fanned out to every clocked instance, and its testbench is
clock-synchronized (one clock domain, driven top-down).
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

    The netlist model and structural checks are `flux_codegen_harness_spec`'s, shared by both
    harnesses (D453); this supplies the language's reserved words and name.
    """
    return _parse_composition(doc, leaf_specs=leaf_specs, check_name=check_not_reserved,
                              language="Verilog/SystemVerilog")


def generate_composite_module_sv(comp_spec: CompositionSpec) -> str:
    """Deterministically emit `module <top>(...); ... endmodule`: declares any internal-only nets
    (net names used in wiring that aren't also top-level ports), then instantiates every named
    instance with named port connections.
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
        # Implicit, harness-owned names, as in every clocked leaf; fanned out to clocked instances.
        port_decls = f"input logic {CLOCK_PORT}, input logic {RESET_PORT}, {data_port_decls}"
    else:
        port_decls = data_port_decls
    lines.append(f"module {comp_spec.top_module_name} ({port_decls});")
    for net_name in internal_nets:
        # At its real width from the parser's net bookkeeping; undeclared widths would default
        # to 32 bits and cause width mismatches.
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
    """Cross-check `leaf_sources` against the spec's instances, raising `InvalidSpecError`
    for a missing key. `leaf_sources` arrives separately from `leaf_specs`, so it needs its
    own check here.
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
    """Generate the composite top module, compile it with Verilator alongside every leaf's
    verified source (`leaf_sources`, keyed by `module_name`), and run the end-to-end
    test-vector check.
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
    """Gate-level Yosys synthesis of the whole composite with every leaf source it
    instantiates (D52); Yosys flattens, so `total_cells` covers the whole design.

    `cache` passes straight to `synthesize_and_measure`, whose key covers all inputs since
    `generate_composite_module_sv` is pure.
    """
    composite_source = generate_composite_module_sv(comp_spec)
    extra_sources = _resolve_leaf_sources(leaf_sources, comp_spec)
    return synthesize_and_measure(
        composite_source, comp_spec.top_module_name, timeout_s=timeout_s, extra_sources=extra_sources,
        cache=cache,
    )
