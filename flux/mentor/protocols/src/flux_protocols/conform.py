"""Check an existing SystemVerilog module against a protocol document (docs/decisions.md D178).

`emit.py` goes document -> RTL. This goes RTL -> verdict: does this module actually present a
conformant OBI subordinate, or an AXI4-Lite slave? The two directions catch different things, and
this is the one that matters for RTL Flux did not generate itself.

**Why this belongs in this repo specifically.** docs/decisions.md D39/D43 split responsibility as
"verification owns structure, LLM owns behaviour". `generation/` produces LLM-written RTL today, and
nothing checked that a generated module's bus interface was structurally right — a reversed `req`/
`gnt` pair lints perfectly under Verilator (D177 named exactly this gap). Checking ports against a
sourced protocol document is structure being owned by verification: the model proposes a design, the
document decides whether the interface conforms, and nothing rests on the model having remembered
AXI correctly.

**Deliberately structural, and it says so.** This compares names, directions and widths. It cannot
see ordering, handshake timing, or whether `gnt` is ever actually asserted — the protocol documents
mostly do not state those in a form this schema carries (D176's closing note), and a checker that
implied otherwise would be worse than none. A `conforms=True` here means "the interface is shaped
right", never "this design speaks the protocol".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from flux_codegen_rtl_harness.sv_parse import module_headers

from .spec import Protocol

# Two bugs D127 records about an earlier port scanner are avoided by construction: scanning the
# header rather than lines that *start* with a direction keyword (so `module M (input ...)` is
# seen), and finditer rather than search (so several ports on one line are all found).
#
# The header is found by balanced-paren scanning rather than a regex. The regex version used
# `#\s*\([^)]*\)` for the parameter block and could not survive a parameter whose default contains
# parentheses — `parameter KEEP_ENABLE = (DATA_WIDTH>8)` in alexforencich/verilog-axis, which is
# real, ordinary Verilog. It matched nothing there and so reported every signal missing: a
# confident, completely wrong verdict (docs/decisions.md D178).
_PORT_RE = re.compile(
    r"\b(input|output|inout)\b"          # direction
    r"(?:\s+(?:wire|logic|reg|bit))?"     # optional net/variable type
    r"(?:\s+signed|\s+unsigned)?"
    r"(?:\s*\[([^\]]*)\])?"               # optional packed range
    r"\s+(\w+)"                           # identifier
)
_SIMPLE_RANGE_RE = re.compile(r"^\s*(\d+)\s*:\s*(\d+)\s*$")


@dataclass(frozen=True, slots=True)
class ParsedPort:
    name: str
    direction: str
    width: int | None  # None when the range isn't a literal `N:M` this parser can evaluate

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "direction": self.direction, "width": self.width}


def parse_module_ports(source: str, *, module_name: str | None = None) -> list[ParsedPort]:
    """Ports declared in a module header, in declaration order.

    Handles ANSI-style headers (`module M (input logic [7:0] a, output b);`), which is what this
    repo generates and what the protocol emitter produces. A width whose range isn't a literal
    `N:M` — a parameterised `[WIDTH-1:0]` — parses as `None` rather than a guess, and the checker
    treats that as unknown rather than as a mismatch.
    """
    ports: list[ParsedPort] = []
    for name, header in module_headers(source):
        if module_name is not None and name != module_name:
            continue
        for direction, packed_range, port_name in _PORT_RE.findall(header):
            ports.append(ParsedPort(
                name=port_name, direction=direction, width=_range_width(packed_range)
            ))
    return ports


def _range_width(packed_range: str) -> int | None:
    if not packed_range:
        return 1
    match = _SIMPLE_RANGE_RE.match(packed_range)
    if match is None:
        return None
    high, low = int(match.group(1)), int(match.group(2))
    return abs(high - low) + 1


@dataclass(frozen=True, slots=True)
class Finding:
    """One way the module departs from the protocol. `severity` is `"error"` for something that
    makes the interface non-conformant and `"note"` for something a caller may legitimately intend.
    """

    signal: str
    severity: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"signal": self.signal, "severity": self.severity, "message": self.message}


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    protocol_ref: str
    role: str
    module_name: str | None
    conforms: bool
    findings: list[Finding] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    extra_ports: list[str] = field(default_factory=list)
    checked: str = (
        "Structural only: signal presence, direction and width. Says nothing about handshake "
        "ordering or timing — the source documents mostly do not state those in a form this "
        "schema carries, so conforms=True means the interface is shaped right, never that the "
        "design speaks the protocol."
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_ref": self.protocol_ref,
            "role": self.role,
            "module_name": self.module_name,
            "conforms": self.conforms,
            "findings": [f.to_dict() for f in self.findings],
            "matched": self.matched,
            "extra_ports": self.extra_ports,
            "checked": self.checked,
        }


def check_module_conformance(
    source: str,
    protocol: Protocol,
    *,
    role: str,
    module_name: str | None = None,
    parameters: dict[str, int] | None = None,
    prefix: str = "",
) -> ConformanceReport:
    """Check a module's ports against `protocol` as seen from `role`.

    `prefix` strips a naming prefix before matching (`s_axis_tdata` against `tdata`), since real
    designs prefix per-interface. `parameters` lets width checking happen for parameterised signals;
    without values, a width mismatch cannot be distinguished from a parameter this call didn't
    resolve, so those are reported as notes rather than errors.

    Optional signals are never required — their absence is not a finding at all — but a *present*
    optional signal is still checked for direction and width, since getting an optional signal
    backwards is as wrong as getting a required one backwards.
    """
    from .emit import _resolve_width  # local import: emit imports nothing from here

    parameters = dict(parameters or {})
    ports = {p.name: p for p in parse_module_ports(source, module_name=module_name)}
    parsed_module = next(iter(module_headers(source)), (None, None))[0]

    findings: list[Finding] = []
    matched: list[str] = []
    consumed: set[str] = set()

    for signal in protocol.signals:
        # Globals are not per-interface prefixed in real RTL: alexforencich/verilog-axis declares a
        # bare `clk` alongside `s_axis_tdata`/`m_axis_tdata`, because one clock serves both
        # interfaces. Prefixing them made every global read as missing (docs/decisions.md D178).
        port_name = signal.name if signal.is_global else f"{prefix}{signal.name}"
        port = ports.get(port_name)
        if port is None:
            if signal.required:
                findings.append(Finding(
                    signal.name, "error",
                    f"required signal {port_name!r} is missing from the module header",
                ))
            continue
        consumed.add(port_name)

        expected_direction = _expected_direction(signal, role)
        if port.direction != expected_direction:
            findings.append(Finding(
                signal.name, "error",
                f"{port_name!r} is declared {port.direction} but a {role} must "
                f"{'drive' if expected_direction == 'output' else 'receive'} it "
                f"({expected_direction})",
            ))
            continue

        try:
            expected_width = _resolve_width(signal, parameters, {})
        except Exception:
            expected_width = None
        if expected_width is not None and port.width is not None and port.width != expected_width:
            findings.append(Finding(
                signal.name, "error",
                f"{port_name!r} is {port.width} bits, but {protocol.ref} makes it "
                f"{expected_width} ({signal.width})",
            ))
            continue
        if expected_width is None and port.width is not None and isinstance(signal.width, str):
            findings.append(Finding(
                signal.name, "note",
                f"{port_name!r} is {port.width} bits; {protocol.ref} states {signal.width!r}, "
                "which this call supplied no value for — width unchecked",
            ))
        matched.append(signal.name)

    extra = sorted(set(ports) - consumed)
    return ConformanceReport(
        protocol_ref=protocol.ref,
        role=role,
        module_name=module_name or parsed_module,
        conforms=not any(f.severity == "error" for f in findings),
        findings=findings,
        matched=sorted(matched),
        extra_ports=extra,
    )


def _expected_direction(signal, role: str) -> str:
    """A global signal (clock, reset) is an input to every role; otherwise the role drives what it
    is the driver of."""
    if signal.is_global:
        return "input"
    return "output" if signal.driver == role else "input"
