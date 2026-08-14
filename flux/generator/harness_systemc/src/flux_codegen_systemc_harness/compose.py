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

from dataclasses import dataclass
from typing import Any

from .build import HarnessRunResult, compile_and_run
from .driver_gen import CLOCK_PORT, RESET_PORT
from .errors import InvalidSpecError
from .keywords import check_not_reserved
from .spec import DesignSpec, Port, TestVector, _parse_bits


@dataclass(frozen=True)
class Instance:
    module_name: str
    instance_name: str
    leaf_ports: tuple[Port, ...]
    is_clocked: bool = False


@dataclass(frozen=True)
class CompositionSpec:
    top_module_name: str
    instances: tuple[Instance, ...]
    nets: dict[str, dict[str, str]]  # instance_name -> {leaf port name: net name}
    ports: tuple[Port, ...]  # top-level ports; Port.name IS the net name it connects to
    test_vectors: tuple[TestVector, ...]

    @property
    def is_clocked(self) -> bool:
        return any(inst.is_clocked for inst in self.instances)


def composition_spec_from_dict(doc: dict[str, Any], *, leaf_specs: dict[str, DesignSpec]) -> CompositionSpec:
    """Validate and parse a composition doc. `leaf_specs` maps each leaf `module_name` used in
    `doc["instances"]` to its own real, already-parsed `DesignSpec` (the source of truth for that
    leaf's ports — never re-declared or guessed here). Same validation shape as
    `flux_codegen_rtl_harness.compose.composition_spec_from_dict`, checked against C++/SystemC
    reserved identifiers (`keywords.py`) rather than Verilog's.
    """
    top_module_name = doc.get("top_module_name")
    if not top_module_name or not str(top_module_name).isidentifier():
        raise InvalidSpecError(f"top_module_name={top_module_name!r} must be a non-empty C++/SystemC identifier")
    check_not_reserved(top_module_name, context="top_module_name")

    raw_instances = doc.get("instances") or []
    if not raw_instances:
        raise InvalidSpecError("instances must be non-empty — a composite with no leaf instances wires nothing")

    instances: list[Instance] = []
    seen_instance_names: set[str] = set()
    for inst_doc in raw_instances:
        module_name, instance_name = inst_doc.get("module_name"), inst_doc.get("instance_name")
        if module_name not in leaf_specs:
            raise InvalidSpecError(f"instance {instance_name!r}: module_name={module_name!r} not in leaf_specs")
        if not instance_name or not str(instance_name).isidentifier():
            raise InvalidSpecError(f"instance_name={instance_name!r} must be a non-empty identifier")
        check_not_reserved(instance_name, context="instance_name")
        if instance_name in seen_instance_names:
            raise InvalidSpecError(f"duplicate instance_name={instance_name!r}")
        seen_instance_names.add(instance_name)
        array_ports = [p.name for p in leaf_specs[module_name].ports if p.is_array]
        if array_ports:
            # Composition wires one scalar net per leaf port (docs/decisions.md D128). An array
            # port needs a net of matching shape, which this netlist format has no way to
            # express — and without this check the generated instantiation binds the array to a
            # scalar and hands the caller a tool error about code they never wrote.
            raise InvalidSpecError(
                f"instance {instance_name!r}: leaf {module_name!r} has array port(s) "
                f"{array_ports} — composition connects scalar nets only, so there is no net "
                "shape to bind them to. Array ports are a simulation-only capability "
                "(docs/decisions.md D120/D127): compose scalar-port leaves instead."
            )
        instances.append(Instance(
            module_name=module_name,
            instance_name=instance_name,
            leaf_ports=leaf_specs[module_name].ports,
            is_clocked=leaf_specs[module_name].is_clocked,
        ))

    raw_nets = doc.get("nets") or {}
    nets: dict[str, dict[str, str]] = {}
    net_dtype: dict[str, str] = {}
    net_width: dict[str, int] = {}
    net_cpp: dict[str, str] = {}

    top_ports_doc = doc.get("ports") or []
    if not top_ports_doc:
        raise InvalidSpecError("ports must be non-empty — a composite with no top-level ports can't be driven or checked")
    top_ports: list[Port] = []
    for p in top_ports_doc:
        name, dir_, dtype = p.get("name"), p.get("dir"), p.get("dtype")
        if dtype not in ("int", "bool"):
            raise InvalidSpecError(f"top-level port {name!r}: dtype={dtype!r} must be 'int' or 'bool'")
        if dir_ not in ("in", "out"):
            raise InvalidSpecError(f"top-level port {name!r}: dir={dir_!r} must be 'in' or 'out'")
        check_not_reserved(name, context="top-level port name")
        # `bits` is parsed here too, with the same validation the leaf spec applies. Constructing
        # `Port(...)` without it silently dropped a declared width, so a composite declaring a
        # 16-bit top port would emit a 32-bit one and bind it to a 16-bit leaf (docs/decisions.md
        # D203).
        bits = _parse_bits(name, p, dtype)
        top_ports.append(Port(name=name, dir=dir_, dtype=dtype, bits=bits))
        net_dtype[name] = dtype
        net_width[name] = bits if bits is not None else (1 if dtype == "bool" else 32)
        net_cpp[name] = top_ports[-1].cpp_type

    top_port_names = {p.name for p in top_ports}
    used_top_ports: set[str] = set()

    for inst in instances:
        leaf_port_names = {p.name for p in inst.leaf_ports}
        inst_nets = raw_nets.get(inst.instance_name) or {}
        missing = leaf_port_names - inst_nets.keys()
        if missing:
            raise InvalidSpecError(f"instance {inst.instance_name!r}: no net specified for ports {sorted(missing)}")
        extra = inst_nets.keys() - leaf_port_names
        if extra:
            raise InvalidSpecError(f"instance {inst.instance_name!r}: net specified for non-existent ports {sorted(extra)}")

        resolved: dict[str, str] = {}
        for leaf_port in inst.leaf_ports:
            net_name = inst_nets[leaf_port.name]
            check_not_reserved(net_name, context="net name")
            resolved[leaf_port.name] = net_name
            if net_name in top_port_names:
                used_top_ports.add(net_name)
            if net_name in net_dtype and net_dtype[net_name] != leaf_port.dtype:
                raise InvalidSpecError(
                    f"net {net_name!r} connects ports of conflicting dtypes "
                    f"({net_dtype[net_name]!r} vs {leaf_port.dtype!r} at {inst.instance_name}.{leaf_port.name})"
                )
            # Width conflicts matter for the same reason dtype ones do, and are easier to miss:
            # one net cannot be both `sc_int<16>` and `sc_int<32>`, and picking either silently
            # truncates or sign-extends every value crossing it (docs/decisions.md D203).
            if net_name in net_width and net_width[net_name] != leaf_port.width:
                raise InvalidSpecError(
                    f"net {net_name!r} connects ports of conflicting widths "
                    f"({net_width[net_name]} vs {leaf_port.width} bits at "
                    f"{inst.instance_name}.{leaf_port.name})"
                )
            net_dtype[net_name] = leaf_port.dtype
            net_width[net_name] = leaf_port.width
            net_cpp[net_name] = leaf_port.cpp_type
        nets[inst.instance_name] = resolved

    unused_top_ports = top_port_names - used_top_ports
    if unused_top_ports:
        raise InvalidSpecError(f"top-level ports {sorted(unused_top_ports)} aren't connected to any instance")

    raw_vectors = doc.get("test_vectors") or []
    if not raw_vectors:
        raise InvalidSpecError("test_vectors must be non-empty — a composite with no vectors can never be verified")
    vectors = tuple(TestVector(inputs=dict(v.get("inputs") or {}), expected=dict(v.get("expected") or {})) for v in raw_vectors)

    return CompositionSpec(
        top_module_name=top_module_name, instances=tuple(instances), nets=nets, ports=tuple(top_ports), test_vectors=vectors,
    )


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
    net_cpp: dict[str, str] = {p.name: p.cpp_type for p in comp_spec.ports}
    for inst in comp_spec.instances:
        for leaf_port in inst.leaf_ports:
            net_cpp[comp_spec.nets[inst.instance_name][leaf_port.name]] = leaf_port.cpp_type
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
        # `p.cpp_type`, not the raw dtype map: a sized port is `sc_int<N>` and dropping the width
        # here would bind a 16-bit leaf port to a 32-bit composite one (docs/decisions.md D203).
        lines.append(f"    {'sc_in' if p.dir == 'in' else 'sc_out'}<{p.cpp_type}> {p.name};")
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
