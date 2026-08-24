"""Emit a protocol-conformant SystemVerilog port list from a protocol document
(docs/decisions.md D177).

This is what makes `protocols/` a component rather than a reference shelf, and it is the reason
docs/decisions.md D31's bar is met: D31 declined to ingest bus specifications because nothing
consumed bus semantics, and said to ingest them "with that functional work". Resolving IR
references (D174) was one consumer; generating an interface that a real tool accepts is a stronger
one, because it can be wrong in ways a lookup cannot, and Verilator will say so.

**The direction of trust matters here.** The port list is generated *from* the protocol document, so
a wrong width or a reversed direction in the YAML becomes a wrong module, silently. Verilator
linting the result proves the output is well-formed SystemVerilog; it does not prove the protocol
document is right about the protocol. Only the provenance does that, which is why the emitted header
carries it — a reader of generated RTL should be able to see that its AXI ports came from an
implementation rather than from Arm's specification, without going back to the source.
"""

from __future__ import annotations

from .errors import ProtocolSpecError
from .spec import Protocol, Signal

_INDENT = "  "


def _resolve_width(signal: Signal, parameters: dict[str, int], widths: dict[str, int]) -> int:
    """Turn a signal's declared width into a concrete bit count.

    Per-signal `widths` win over anything derived, then widths naming a parameter are resolved from
    `parameters`, then simple `NAME/N` expressions (OBI's `DATA_WIDTH/8`) are evaluated. A width
    that cannot be resolved raises rather than defaulting to 1 — a silently 1-bit data bus is
    exactly the kind of plausible-looking wrong output that would lint clean and mean nothing.
    """
    if signal.name in widths:
        return int(widths[signal.name])
    width = signal.width
    if width is None:
        raise ProtocolSpecError(
            f"signal {signal.name!r} has no declared width in its source document, so it cannot be "
            "emitted with a concrete one. WISHBONE derives these from a core's own DATASHEET rather "
            f"than fixing them in the specification — pass widths={{{signal.name!r}: <bits>}} to "
            "supply it."
        )
    if isinstance(width, int):
        return width
    if width in parameters:
        return int(parameters[width])
    if "/" in width:
        name, _, divisor = width.partition("/")
        name, divisor = name.strip(), divisor.strip()
        if name in parameters and divisor.isdigit() and int(divisor) != 0:
            resolved, remainder = divmod(int(parameters[name]), int(divisor))
            if remainder:
                raise ProtocolSpecError(
                    f"signal {signal.name!r} has width {width!r}, and {name}={parameters[name]} is "
                    f"not divisible by {divisor} — the source document's own relationship between "
                    "these does not hold for the parameters given"
                )
            return resolved
    raise ProtocolSpecError(
        f"signal {signal.name!r} has width {width!r}, which needs a value for every parameter it "
        f"names; got {sorted(parameters)}"
    )


def _declaration(signal: Signal, *, is_input: bool, width: int) -> str:
    direction = "input " if is_input else "output"
    vector = "" if width == 1 else f"[{width - 1}:0] "
    return f"{direction} logic {vector}{signal.name}"


def emit_sv_ports(
    protocol: Protocol,
    *,
    role: str,
    parameters: dict[str, int] | None = None,
    widths: dict[str, int] | None = None,
    include_optional: bool = False,
    include_global: bool = True,
) -> list[str]:
    """Port declarations for one side of `protocol`, as seen from `role`.

    A signal this role drives is an `output`; one it receives is an `input`. Global signals (a clock,
    a reset) are inputs to every role. `include_optional` adds the signals the source marks
    optional — off by default, since a minimal conformant interface is the useful starting point and
    the optional set is large for OBI.
    """
    parameters = dict(parameters or {})
    widths = dict(widths or {})
    if protocol.roles and role not in protocol.roles:
        raise ProtocolSpecError(
            f"{protocol.ref} has no role {role!r}; it defines {list(protocol.roles)}"
        )

    declarations: list[str] = []
    for signal in protocol.signals:
        if signal.is_global:
            if include_global:
                declarations.append(_declaration(
                    signal, is_input=True, width=_resolve_width(signal, parameters, widths)
                ))
            continue
        if signal.driver != role and signal.receiver != role:
            continue
        if not signal.required and not include_optional:
            continue
        declarations.append(_declaration(
            signal, is_input=signal.receiver == role,
            width=_resolve_width(signal, parameters, widths),
        ))

    if not declarations:
        raise ProtocolSpecError(
            f"{protocol.ref} role {role!r} produced no ports — nothing to emit. With "
            "include_optional=False this can mean every signal for this role is optional."
        )
    return declarations


def emit_sv_module(
    protocol: Protocol,
    *,
    role: str,
    module_name: str,
    parameters: dict[str, int] | None = None,
    widths: dict[str, int] | None = None,
    include_optional: bool = False,
) -> str:
    """A complete, empty SystemVerilog module presenting `protocol`'s `role` interface.

    Empty on purpose: this generates the *interface*, which is what a protocol document can
    honestly determine. Behaviour behind it is the design's, not the protocol's, and a generated
    body would be inventing exactly the semantics the source documents don't state.
    """
    ports = emit_sv_ports(
        protocol, role=role, parameters=parameters, widths=widths,
        include_optional=include_optional,
    )
    provenance = protocol.provenance
    header = [
        f"// {module_name}: {protocol.title}",
        f"// role: {role}",
        f"// generated by flux_protocols from {protocol.ref} — do not edit by hand",
        f"// source: {provenance.document} ({provenance.licence})",
    ]
    if not provenance.normative:
        standard = (provenance.implements or {}).get("standard", "an unnamed standard")
        header.append(
            f"// NOTE: that source is an implementation of {standard}, not the specification "
            "itself."
        )
        header.append(
            "//       Where the two could differ, the specification governs and these ports are "
            "not evidence about it."
        )
    body = ",\n".join(f"{_INDENT}{p}" for p in ports)
    return "\n".join(header) + f"\nmodule {module_name} (\n{body}\n);\nendmodule\n"


def emit_sv_assertions(protocol: Protocol, *, module_name: str | None = None) -> str:
    """A SystemVerilog checker module asserting the protocol's handshake semantics
    (docs/decisions.md D212).

    Each concurrent assertion is derived from the document's `handshakes` block and carries the
    source rule's own number and text as a comment — possible precisely because the parser only
    admits a `handshakes` block whose every claim cites a quoted rule, and rules only exist on
    redistributable documents. Bind it beside a DUT (or instantiate it in a testbench) and any
    simulator that honours `assert property` reports violations by rule number.

    Three assertion shapes, deliberately few: request low during reset, request held until accept
    (no retraction), and nothing else — see the document's `coverage_note` for what the rules
    subset covers. Payload-stability and ordering rules are not represented in `handshakes` yet,
    so no assertion pretends to check them.
    """
    from .spec import _split_identifiers

    hs = protocol.handshaking
    if hs is None:
        raise ProtocolSpecError(
            f"protocol {protocol.ref} has no handshakes block — nothing to derive assertions from"
        )
    name = module_name or f"{protocol.id}_handshake_checker"

    ports = [hs.clock, hs.active_low_reset]
    for phase in hs.phases:
        for signal in (phase.request, phase.accept):
            if signal not in ports:
                ports.append(signal)
        # Payload signals under a stability obligation join the port list with their declared,
        # possibly parametric widths — the checker is emitted parameterized rather than at one
        # resolved size, so one module binds beside any legal configuration of the DUT.
        for obligation in phase.stable_until_accept:
            if obligation.signal not in ports:
                ports.append(obligation.signal)

    parameter_names: list[str] = []
    for port in ports:
        width = protocol.signal(port).width
        if isinstance(width, str):
            for token in _split_identifiers(width):
                if token and not token.isdigit() and token not in parameter_names:
                    parameter_names.append(token)

    def _port_declaration(port: str, *, last: bool) -> str:
        width = protocol.signal(port).width
        comma = "" if last else ","
        if width in (1, None):
            return f"{_INDENT}input logic {port}{comma}"
        return f"{_INDENT}input logic [{width}-1:0] {port}{comma}"

    clk, rst = hs.clock, hs.active_low_reset
    lines: list[str] = [
        f"// Generated by flux_protocols.emit_sv_assertions from {protocol.ref} — do not edit.",
        f"// Source: {protocol.provenance.document} ({protocol.provenance.publisher}, "
        f"licence {protocol.provenance.licence}).",
        "// Each assertion cites the source document's own rule number.",
        f"module {name}",
    ]
    if parameter_names:
        lines.append("#(")
        for i, param in enumerate(parameter_names):
            default = protocol.parameter(param).default
            comma = "," if i < len(parameter_names) - 1 else ""
            lines.append(f"{_INDENT}parameter int {param} = {default}{comma}")
        lines.append(")")
    lines += [
        "(",
        *[_port_declaration(p, last=(p == ports[-1])) for p in ports],
        ");",
    ]
    for phase in hs.phases:
        for req_low in phase.reset_low:
            rule = protocol.rule(req_low.rule_id)
            lines += [
                "",
                f"{_INDENT}// {rule.id}: {rule.text}",
                f"{_INDENT}a_{phase.name}_{req_low.signal}_low_in_reset: assert property (",
                f"{_INDENT}{_INDENT}@(posedge {clk}) !{rst} |-> !{req_low.signal}",
                f"{_INDENT});",
            ]
        for obligation in phase.stable_until_accept:
            rule = protocol.rule(obligation.rule_id)
            lines += [
                "",
                f"{_INDENT}// {rule.id}: {rule.text}",
                f"{_INDENT}a_{phase.name}_{obligation.signal}_stable: assert property (",
                f"{_INDENT}{_INDENT}@(posedge {clk}) disable iff (!{rst}) "
                f"{phase.request} && !{phase.accept} |=> $stable({obligation.signal})",
                f"{_INDENT});",
            ]
        if phase.no_retract_rule_id is not None:
            rule = protocol.rule(phase.no_retract_rule_id)
            lines += [
                "",
                f"{_INDENT}// {rule.id}: {rule.text}",
                f"{_INDENT}a_{phase.name}_no_retract: assert property (",
                f"{_INDENT}{_INDENT}@(posedge {clk}) disable iff (!{rst}) "
                f"{phase.request} && !{phase.accept} |=> {phase.request}",
                f"{_INDENT});",
            ]
    lines += ["", "endmodule", ""]
    return "\n".join(lines)
