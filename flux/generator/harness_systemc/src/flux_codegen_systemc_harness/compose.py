"""Multi-module composition for SystemC (docs/decisions.md D55) — the SystemC sibling of
`flux_codegen_rtl_harness.compose` (D48/D50): wires already-verified leaf `SC_MODULE`s together
into a top-level composite `SC_MODULE`, verified end-to-end against its own test vectors through
real g++/SystemC. Closes the one asymmetry D54 left standing after closing clocked-design parity:
RTL could compose modules (D48), including clocked ones (D50), and get a real gate-count synthesis
signal for composites (D52) — SystemC could do none of that.

**Same "verification owns structure, the LLM owns only leaf behavior" split as RTL's `compose.py`
(D48) and this package's own `driver_gen.py` (D39).** The composite's member declarations,
`SC_CTOR` initializer list, and constructor-body port binding (including clk/rst_n fan-out to
clocked leaves) are all deterministically generated here, never LLM-authored — reintroducing that
risk for multi-module wiring after already eliminating it for single-leaf port binding would be
exactly the class of bug both prior "verification owns structure" decisions exist to prevent.

**`CompositionSpec`/`Instance` intentionally mirror RTL's `compose.py` shape, not its code.** Same
netlist model (instances + a net name per (instance, port) pair), same validation rules, same
`is_clocked` derivation — but a genuinely different C++ emission step: SystemC composition is
member declarations plus an `SC_CTOR` initializer list and constructor-body port binding, not
Verilog's named-port-connection module instantiation. Kept as an independent module (not shared
code) for the same reason `codegen_rtl_harness` never imports `codegen_systemc_harness.compose`:
the two target languages' composition syntax has nothing in common past the netlist *idea*.

**No gate-level synthesis sibling, by design, not by omission.** RTL's D52 extended D47's real
Yosys ranking to composites because Yosys reads synthesizable Verilog. SystemC is a
behavioral/TLM modeling language — Yosys has no SystemC frontend, and this harness's own generated
designs (using plain C++ control flow the way `_SYNTAX_PRIMER` and `_CLOCKED_PRIMER` both actively
encourage) aren't written in synthesizable-subset style to begin with. There is no equivalent
"real Yosys over a SystemC composite" step to add here.
"""

from __future__ import annotations

from typing import Any

from flux_codegen_harness_spec import (CompositionSpec, DesignSpec,  # noqa: F401
                                       HarnessRunResult, Instance)
from flux_codegen_harness_spec import composition_spec_from_dict as _parse_composition

from .build import compile_and_run
from .driver_gen import CLOCK_PORT, RESET_PORT
from .keywords import check_not_reserved
from .spec import cpp_type


def composition_spec_from_dict(doc: dict[str, Any], *, leaf_specs: dict[str, DesignSpec]
                               ) -> CompositionSpec:
    """Validate and parse a composition doc, against C++/SystemC reserved identifiers.

    The netlist model and every structural check are `flux_codegen_harness_spec`'s (D453) --
    one parser for both harnesses, after the two copies drifted apart. What is supplied here is
    the language: its reserved words and its name in the messages.
    """
    return _parse_composition(doc, leaf_specs=leaf_specs, check_name=check_not_reserved,
                              language="C++/SystemC")


def generate_composite_module_cpp(comp_spec: CompositionSpec) -> str:
    """Deterministically emit `#include`s for every leaf plus a real `SC_MODULE(<top>) { ... };`:
    declares any internal-only nets as `sc_signal`s (net names used in wiring that aren't also
    top-level ports), one member per instance, then an `SC_CTOR` that names-and-binds every
    instance — real SystemC composition, never LLM-generated. Clocked leaves get the composite's
    own implicit `clk`/`rst_n` (same harness-owned convention as every clocked leaf, D54) fanned
    out to them in the constructor body, mirroring `flux_codegen_rtl_harness.compose`'s D50 fix.
    """
    top_port_names = {p.name for p in comp_spec.ports}
    # Track the C++ type, not just the dtype: a sized leaf port is `sc_int<N>`, and an internal net
    # must be declared at the same width as the ports it joins (docs/decisions.md D203). Width
    # conflicts on one net are refused in `composition_spec_from_dict`, so whichever port is seen
    # last here agrees with every other.
    net_cpp: dict[str, str] = {p.name: cpp_type(p) for p in comp_spec.ports}
    for inst in comp_spec.instances:
        for leaf_port in inst.leaf_ports:
            net_cpp[comp_spec.nets[inst.instance_name][leaf_port.name]] = cpp_type(leaf_port)
    internal_nets = sorted(n for n in net_cpp if n not in top_port_names)

    leaf_module_names = sorted({inst.module_name for inst in comp_spec.instances})

    lines: list[str] = []
    for name in leaf_module_names:
        lines.append(f'#include "{name}.h"')
    lines.append("")
    lines.append(f"SC_MODULE({comp_spec.top_module_name}) {{")
    if comp_spec.is_clocked:
        lines.append(f"    sc_in_clk {CLOCK_PORT};")
        lines.append(f"    sc_in<bool> {RESET_PORT};")
    for p in comp_spec.ports:
        # `cpp_type(p)`, not the raw dtype map: a sized port is `sc_int<N>` and dropping the width
        # here would bind a 16-bit leaf port to a 32-bit composite one (docs/decisions.md D203).
        lines.append(f"    {'sc_in' if p.dir == 'in' else 'sc_out'}<{cpp_type(p)}> {p.name};")
    for net_name in internal_nets:
        lines.append(f"    sc_signal<{net_cpp[net_name]}> {net_name};")
    lines.append("")
    for inst in comp_spec.instances:
        lines.append(f"    {inst.module_name} {inst.instance_name};")
    lines.append("")
    ctor_init = ", ".join(f'{inst.instance_name}("{inst.instance_name}")' for inst in comp_spec.instances)
    lines.append(f"    SC_CTOR({comp_spec.top_module_name}) : {ctor_init} {{")
    for inst in comp_spec.instances:
        conn = comp_spec.nets[inst.instance_name]
        if inst.is_clocked:
            lines.append(f"        {inst.instance_name}.{CLOCK_PORT}({CLOCK_PORT});")
            lines.append(f"        {inst.instance_name}.{RESET_PORT}({RESET_PORT});")
        for leaf_port in inst.leaf_ports:
            lines.append(f"        {inst.instance_name}.{leaf_port.name}({conn[leaf_port.name]});")
    lines.append("    }")
    lines.append("};")
    lines.append("")
    return "\n".join(lines)


def compile_and_run_composite(
    leaf_sources: dict[str, str],
    comp_spec: CompositionSpec,
    *,
    timeout_s: float = 120.0,
    keep_workdir: bool = False,
) -> HarnessRunResult:
    """Generate the composite top-level module, compile it against real g++/SystemC alongside
    every referenced leaf's already-verified source (`leaf_sources`, keyed by `module_name` — the
    exact strings `flux_generate_systemc_module` already verified, never re-derived), and run the
    same real end-to-end test-vector check every other harness entry point in this repo uses.
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
    composite_source = generate_composite_module_cpp(comp_spec)
    extra_sources = {inst.module_name: leaf_sources[inst.module_name] for inst in comp_spec.instances}
    return compile_and_run(composite_source, top_spec, timeout_s=timeout_s, keep_workdir=keep_workdir, extra_sources=extra_sources)
