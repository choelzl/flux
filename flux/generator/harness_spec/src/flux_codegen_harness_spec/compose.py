"""The netlist a composite is wired from, and the one parser that validates it (D48/D55, shared
in D453).

WHAT A COMPOSITION IS, in either language: instances of already-verified leaf designs, one net
per (instance, leaf port), top-level ports whose names ARE net names, and the composite's own
test vectors. The emission is where the languages part company -- Verilog named-port
instantiation against SystemC member declarations plus an `SC_CTOR` initializer list -- and
that stays in each harness (`generate_composite_module_sv`, `generate_composite_module_cpp`).

WHY THIS IS SHARED NOW. The two parsers were written to "mirror in shape, not in code", and
they drifted exactly as that invites: the SystemC one had gained top-port width parsing and a
net-width conflict check (D203), the RTL one a top-port identifier check -- each missing the
other's fix, so an RTL composite declaring a 16-bit top port silently emitted a 32-bit one and
an RTL net joining a 16-bit and a 32-bit port was not refused. This parser is the union: every
check either side had, applied to both, with the language's reserved-word check injected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .errors import InvalidSpecError
from .spec import DesignSpec, Port, TestVector, parse_bits


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
    #: Every net's bit width, top-level ports included -- the parser knows it (it refuses a net
    #: that joins two widths), so an emitter declaring an internal net need not guess 32 (D453).
    net_width: dict[str, int] = field(default_factory=dict)

    @property
    def is_clocked(self) -> bool:
        return any(inst.is_clocked for inst in self.instances)


def _no_check(name: str, *, context: str) -> None:
    """The default reserved-word check: none. A language harness passes its own."""


def composition_spec_from_dict(doc: dict[str, Any], *, leaf_specs: dict[str, DesignSpec],
                               check_name: Callable[..., None] = _no_check,
                               language: str = "target-language") -> CompositionSpec:
    """Validate and parse a composition doc.

    `leaf_specs` maps each leaf `module_name` used in `doc["instances"]` to its own real,
    already-parsed `DesignSpec` -- the source of truth for that leaf's ports, never re-declared
    or guessed here. `check_name` is the language's reserved-identifier check (applied to the
    top module name, every instance name, every top-level port name and every net name);
    `language` names the language in the identifier messages.
    """
    top_module_name = doc.get("top_module_name")
    if not top_module_name or not str(top_module_name).isidentifier():
        raise InvalidSpecError(
            f"top_module_name={top_module_name!r} must be a non-empty {language} identifier")
    check_name(top_module_name, context="top_module_name")

    raw_instances = doc.get("instances") or []
    if not raw_instances:
        raise InvalidSpecError(
            "instances must be non-empty -- a composite with no leaf instances wires nothing")

    instances: list[Instance] = []
    seen_instance_names: set[str] = set()
    for inst_doc in raw_instances:
        module_name, instance_name = inst_doc.get("module_name"), inst_doc.get("instance_name")
        if module_name not in leaf_specs:
            raise InvalidSpecError(
                f"instance {instance_name!r}: module_name={module_name!r} not in leaf_specs")
        if not instance_name or not str(instance_name).isidentifier():
            raise InvalidSpecError(f"instance_name={instance_name!r} must be a non-empty identifier")
        check_name(instance_name, context="instance_name")
        if instance_name in seen_instance_names:
            raise InvalidSpecError(f"duplicate instance_name={instance_name!r}")
        seen_instance_names.add(instance_name)
        array_ports = [p.name for p in leaf_specs[module_name].ports if p.is_array]
        if array_ports:
            # Composition wires one scalar net per leaf port (docs/decisions.md D128). An array
            # port needs a net of matching shape, which this netlist format has no way to
            # express -- and without this check the generated instantiation binds the array to
            # a scalar and hands the caller a tool error about code they never wrote.
            raise InvalidSpecError(
                f"instance {instance_name!r}: leaf {module_name!r} has array port(s) "
                f"{array_ports} -- composition connects scalar nets only, so there is no net "
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

    top_ports_doc = doc.get("ports") or []
    if not top_ports_doc:
        raise InvalidSpecError(
            "ports must be non-empty -- a composite with no top-level ports can't be driven or "
            "checked")
    top_ports: list[Port] = []
    for p in top_ports_doc:
        name, dir_, dtype = p.get("name"), p.get("dir"), p.get("dtype")
        if dtype not in ("int", "bool"):
            raise InvalidSpecError(
                f"top-level port {name!r}: dtype={dtype!r} must be 'int' or 'bool'")
        if dir_ not in ("in", "out"):
            raise InvalidSpecError(f"top-level port {name!r}: dir={dir_!r} must be 'in' or 'out'")
        # The same identifier check instance_name/top_module_name get -- without it a name like
        # "2bad" (or None) sailed through to the emitter and surfaced as a raw syntax error in a
        # file the caller never wrote, exactly the failure mode this validation layer exists to
        # catch (a review finding on the RTL side, which the SystemC side did not have).
        if not name or not str(name).isidentifier():
            raise InvalidSpecError(f"top-level port name={name!r} must be a non-empty identifier")
        check_name(name, context="top-level port name")
        # `bits` is parsed here too, with the same validation the leaf spec applies. Constructing
        # `Port(...)` without it silently dropped a declared width, so a composite declaring a
        # 16-bit top port emitted a 32-bit one and bound it to a 16-bit leaf (docs/decisions.md
        # D203 -- fixed on the SystemC side there, and on the RTL side only in D453).
        bits = parse_bits(name, p, dtype)
        top_ports.append(Port(name=name, dir=dir_, dtype=dtype, bits=bits))
        net_dtype[name] = dtype
        net_width[name] = top_ports[-1].width

    top_port_names = {p.name for p in top_ports}
    used_top_ports: set[str] = set()

    for inst in instances:
        leaf_port_names = {p.name for p in inst.leaf_ports}
        inst_nets = raw_nets.get(inst.instance_name) or {}
        missing = leaf_port_names - inst_nets.keys()
        if missing:
            raise InvalidSpecError(
                f"instance {inst.instance_name!r}: no net specified for ports {sorted(missing)}")
        extra = inst_nets.keys() - leaf_port_names
        if extra:
            raise InvalidSpecError(
                f"instance {inst.instance_name!r}: net specified for non-existent ports "
                f"{sorted(extra)}")

        resolved: dict[str, str] = {}
        for leaf_port in inst.leaf_ports:
            net_name = inst_nets[leaf_port.name]
            check_name(net_name, context="net name")
            resolved[leaf_port.name] = net_name
            if net_name in top_port_names:
                used_top_ports.add(net_name)
            if net_name in net_dtype and net_dtype[net_name] != leaf_port.dtype:
                raise InvalidSpecError(
                    f"net {net_name!r} connects ports of conflicting dtypes "
                    f"({net_dtype[net_name]!r} vs {leaf_port.dtype!r} at "
                    f"{inst.instance_name}.{leaf_port.name})"
                )
            # Width conflicts matter for the same reason dtype ones do, and are easier to miss:
            # one net cannot be both 16 and 32 bits wide, and picking either silently truncates
            # or sign-extends every value crossing it (docs/decisions.md D203).
            if net_name in net_width and net_width[net_name] != leaf_port.width:
                raise InvalidSpecError(
                    f"net {net_name!r} connects ports of conflicting widths "
                    f"({net_width[net_name]} vs {leaf_port.width} bits at "
                    f"{inst.instance_name}.{leaf_port.name})"
                )
            net_dtype[net_name] = leaf_port.dtype
            net_width[net_name] = leaf_port.width
        nets[inst.instance_name] = resolved

    unused_top_ports = top_port_names - used_top_ports
    if unused_top_ports:
        raise InvalidSpecError(
            f"top-level ports {sorted(unused_top_ports)} aren't connected to any instance")

    raw_vectors = doc.get("test_vectors") or []
    if not raw_vectors:
        raise InvalidSpecError(
            "test_vectors must be non-empty -- a composite with no vectors can never be verified")
    vectors = tuple(TestVector(inputs=dict(v.get("inputs") or {}),
                               expected=dict(v.get("expected") or {})) for v in raw_vectors)

    return CompositionSpec(
        top_module_name=top_module_name, instances=tuple(instances), nets=nets,
        ports=tuple(top_ports), test_vectors=vectors, net_width=net_width,
    )


__all__ = ["CompositionSpec", "Instance", "composition_spec_from_dict"]
