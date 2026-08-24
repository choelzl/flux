"""Typed protocol specifications (docs/decisions.md D174): structured, machine-readable facts about
bus and stream protocols — signals, widths, drivers, parameters, numbered rules — that other Flux
modules consume programmatically.

**Not a second `knowledge/`.** `knowledge/` retrieves *prose* from spec documents (BM25 over
AsciiDoc chunks): the right shape for "what does the spec say about X", answered to a human or an
agent. This module answers "what signals does an OBI manager drive, how wide is `be`, and which
requirement says so" — to *code*. A codegen backend emitting a bus interface, or a checker
validating that an architecture's declared protocol matches its declared widths, cannot use a
paragraph. Both surfaces over the same standards are deliberate, not duplication.

**Every fact carries its source, and the schema enforces it.** `Provenance` is required, not
optional, and `licence`/`redistributable`/`normative` are required within it. Two guards follow
from that and are the reason this module is shaped the way it is:

- A source marked `redistributable: false` may not carry quoted rule text or prose descriptions.
  docs/decisions.md D31 checked five standards' redistribution terms and found AMBA/AXI, JEDEC,
  PCIe and I2C closed — Arm's wording is "No part of the document may be reproduced in any form by
  any means without the express prior written permission of Arm." A store of protocol facts is
  exactly the kind of thing that would accumulate such text by accident, so the schema makes it
  impossible rather than merely discouraged.
- A `normative: false` document — one describing an *implementation* of a standard rather than the
  standard itself — must say what it implements, and that reference must carry the standard's own
  (closed) document identity. This is how AXI is representable at all: its normative spec cannot be
  redistributed, but `pulp-platform/axi` is real, permissively-licensed RTL whose signal sets are
  quotable, and a consumer is told plainly that it is reading an implementation.

The two guards exist because the failure they prevent is silent. A fabricated signal width and a
sourced one look identical at the point of use; only the provenance distinguishes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import LicenceViolationError, ProtocolSpecError, UnsourcedFactError

SCHEMA_VERSION = "0.1.0"

_REQUIRED_PROVENANCE = ("document", "publisher", "licence", "redistributable", "normative")
_VALID_KINDS = ("bus", "stream", "definitions")
# Roles are per-document rather than a fixed enum: OBI says manager/subordinate, WISHBONE says
# master/slave, AXI says manager/subordinate too. Normalising them here would erase the vocabulary
# each spec actually uses, which is part of what a reader comes to this module for.
_GLOBAL_ROLE = "global"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a protocol document's content came from, and what may be done with it.

    `normative` distinguishes "this is the standard" from "this is an implementation of the
    standard". `implements` is required when `normative` is False — an implementation-sourced
    document that doesn't say what it implements is an unattributed claim.
    """

    document: str
    publisher: str
    licence: str
    redistributable: bool
    normative: bool
    url: str | None = None
    version_retrieved: str | None = None
    retrieved: str | None = None
    implements: dict[str, Any] | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": self.document,
            "publisher": self.publisher,
            "licence": self.licence,
            "redistributable": self.redistributable,
            "normative": self.normative,
            "url": self.url,
            "version_retrieved": self.version_retrieved,
            "retrieved": self.retrieved,
            "implements": self.implements,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Signal:
    """One protocol signal. `width` is an integer for fixed-width signals, or the name of a
    parameter (e.g. `"DATA_WIDTH"`) for parameterised ones, or an expression string the document
    itself states (e.g. `"DATA_WIDTH/8"`). Never silently defaulted: a signal whose width the
    source doesn't state carries `width: null` rather than a guess.
    """

    name: str
    driver: str
    receiver: str
    width: int | str | None
    required: bool = True
    description: str | None = None
    section: str | None = None

    @property
    def is_global(self) -> bool:
        return self.driver == _GLOBAL_ROLE or self.receiver == _GLOBAL_ROLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "driver": self.driver, "receiver": self.receiver,
            "width": self.width, "required": self.required,
            "description": self.description, "section": self.section,
        }


@dataclass(frozen=True, slots=True)
class Parameter:
    """A configuration parameter the protocol defines (OBI's `DATA_WIDTH`, AXI's ID width).
    `allowed` lists the values the source states are legal, when it states any.
    """

    name: str
    default: int | str | bool | None
    description: str | None = None
    allowed: tuple[int | str, ...] | None = None
    section: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "default": self.default, "description": self.description,
            "allowed": list(self.allowed) if self.allowed is not None else None,
            "section": self.section,
        }


@dataclass(frozen=True, slots=True)
class Rule:
    """A numbered requirement from the source document, kept with its own identifier so a consumer
    can cite it (`OBI R-3.1`) rather than paraphrase it. Only available from redistributable
    sources — see this module's docstring.
    """

    id: str
    text: str
    section: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "section": self.section}


@dataclass(frozen=True, slots=True)
class SignalObligation:
    """One signal under one cited obligation — low during reset, or stable until accept — with the
    id of the rule stating it."""

    signal: str
    rule_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"signal": self.signal, "rule_id": self.rule_id}


@dataclass(frozen=True, slots=True)
class HandshakePhase:
    """One two-way handshake (request/accept signal pair) from the source document.

    Every field that states a behavior carries the id of the rule stating it — `rule_id` for the
    handshake existing at all, `no_retract_rule_id` for the request-may-not-retract obligation,
    one per `reset_low` entry. The parser rejects ids the document does not quote, so this block
    can never claim semantics beyond what the vendored normative text actually says — the same
    discipline that keeps non-redistributable documents from carrying rules at all.
    """

    name: str
    requester_role: str
    request: str
    accept: str
    rule_id: str
    no_retract_rule_id: str | None = None
    reset_low: tuple[SignalObligation, ...] = ()
    stable_until_accept: tuple[SignalObligation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "requester_role": self.requester_role,
            "request": self.request,
            "accept": self.accept,
            "rule_id": self.rule_id,
            "no_retract_rule_id": self.no_retract_rule_id,
            "reset_low": [r.to_dict() for r in self.reset_low],
            "stable_until_accept": [s.to_dict() for s in self.stable_until_accept],
        }


@dataclass(frozen=True, slots=True)
class Handshaking:
    """The document's handshake semantics in machine-checkable form (docs/decisions.md D212) —
    what `emit_sv_assertions` derives SVA from. Structure (`signals`) says what a port list looks
    like; this says a slice of what the wires must *do*."""

    clock: str
    active_low_reset: str
    phases: tuple[HandshakePhase, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "clock": self.clock,
            "active_low_reset": self.active_low_reset,
            "phases": [p.to_dict() for p in self.phases],
        }


@dataclass(frozen=True, slots=True)
class Protocol:
    """One protocol at one version, as described by exactly one source document."""

    schema_version: str
    id: str
    version: str
    title: str
    kind: str
    provenance: Provenance
    signals: tuple[Signal, ...] = ()
    parameters: tuple[Parameter, ...] = ()
    rules: tuple[Rule, ...] = ()
    roles: tuple[str, ...] = ()
    summary: str | None = None
    coverage_note: str | None = None
    handshaking: Handshaking | None = None

    @property
    def ref(self) -> str:
        """The `id@version` string this protocol answers to — the same shape the Architecture IR
        already uses in `noc.model: "axi4@2.0"`."""
        return f"{self.id}@{self.version}"

    def signal(self, name: str) -> Signal:
        for s in self.signals:
            if s.name == name:
                return s
        raise ProtocolSpecError(
            f"protocol {self.ref} has no signal {name!r}; it defines "
            f"{sorted(s.name for s in self.signals)}"
        )

    def signals_driven_by(self, role: str) -> tuple[Signal, ...]:
        return tuple(s for s in self.signals if s.driver == role)

    def parameter(self, name: str) -> Parameter:
        for p in self.parameters:
            if p.name == name:
                return p
        raise ProtocolSpecError(
            f"protocol {self.ref} has no parameter {name!r}; it defines "
            f"{sorted(p.name for p in self.parameters)}"
        )

    def rule(self, rule_id: str) -> Rule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise ProtocolSpecError(
            f"protocol {self.ref} has no rule {rule_id!r}"
            + ("" if self.rules else " (this document carries no rules — see its coverage_note)")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "version": self.version,
            "ref": self.ref,
            "title": self.title,
            "kind": self.kind,
            "summary": self.summary,
            "coverage_note": self.coverage_note,
            "roles": list(self.roles),
            "provenance": self.provenance.to_dict(),
            "signals": [s.to_dict() for s in self.signals],
            "parameters": [p.to_dict() for p in self.parameters],
            "rules": [r.to_dict() for r in self.rules],
            "handshaking": self.handshaking.to_dict() if self.handshaking else None,
        }


def _require(doc: dict[str, Any], key: str, where: str) -> Any:
    if key not in doc or doc[key] is None:
        raise ProtocolSpecError(f"{where}: missing required field {key!r}")
    return doc[key]


def _parse_provenance(raw: Any, where: str) -> Provenance:
    if not isinstance(raw, dict):
        raise UnsourcedFactError(
            f"{where}: no `provenance` block. Every protocol document must say where its content "
            "came from — an unsourced fact is a plausible-looking claim with nothing behind it."
        )
    missing = [k for k in _REQUIRED_PROVENANCE if raw.get(k) is None]
    if missing:
        raise UnsourcedFactError(
            f"{where}: provenance is missing {missing} — all of {list(_REQUIRED_PROVENANCE)} are "
            "required, including `licence` and `redistributable` (docs/decisions.md D31: four of "
            "the five standards checked there cannot be redistributed at all)."
        )
    provenance = Provenance(
        document=raw["document"], publisher=raw["publisher"], licence=raw["licence"],
        redistributable=bool(raw["redistributable"]), normative=bool(raw["normative"]),
        url=raw.get("url"), version_retrieved=raw.get("version_retrieved"),
        retrieved=raw.get("retrieved"), implements=raw.get("implements"), note=raw.get("note"),
    )
    if not provenance.normative:
        implements = provenance.implements
        if not isinstance(implements, dict) or not implements.get("standard"):
            raise UnsourcedFactError(
                f"{where}: provenance says normative=false (this document describes an "
                "implementation, not the standard) but doesn't say what it implements. Set "
                "`implements.standard` — an implementation-sourced fact that doesn't name the "
                "standard it implements is an unattributed claim."
            )
    return provenance


def _check_licence(protocol_id: str, provenance: Provenance, signals, rules, where: str) -> None:
    """A non-redistributable source may state *that* a signal exists, but not reproduce the
    document's prose about it. Structure (names, widths, directions) is what a consumer needs to
    generate or check an interface; prose is what the licence covers.
    """
    if provenance.redistributable:
        return
    if rules:
        raise LicenceViolationError(
            f"{where}: protocol {protocol_id!r} cites a source marked redistributable=false "
            f"({provenance.licence}) but carries {len(rules)} quoted rule(s). Rule text is "
            "reproduced content — either use a redistributable source, or drop the rules and keep "
            "only structure."
        )
    described = [s.name for s in signals if s.description]
    if described:
        raise LicenceViolationError(
            f"{where}: protocol {protocol_id!r} cites a source marked redistributable=false "
            f"({provenance.licence}) but carries prose descriptions for {described}. Signal "
            "names, widths and directions are structure; descriptions are reproduced text."
        )


def parse_protocol(doc: dict[str, Any], *, where: str = "<protocol document>") -> Protocol:
    """Build a `Protocol` from a plain dict, enforcing the provenance and licence guards this
    module's docstring describes. `where` names the source for error messages."""
    if not isinstance(doc, dict):
        raise ProtocolSpecError(f"{where}: expected a mapping, got {type(doc).__name__}")

    schema_version = str(_require(doc, "schema_version", where))
    if schema_version != SCHEMA_VERSION:
        raise ProtocolSpecError(
            f"{where}: schema_version {schema_version!r}, this loader speaks {SCHEMA_VERSION!r}"
        )
    protocol_id = str(_require(doc, "id", where))
    kind = str(_require(doc, "kind", where))
    if kind not in _VALID_KINDS:
        raise ProtocolSpecError(f"{where}: kind {kind!r} is not one of {list(_VALID_KINDS)}")

    provenance = _parse_provenance(doc.get("provenance"), where)

    signals = tuple(
        Signal(
            name=str(_require(s, "name", f"{where}: signal")),
            driver=str(_require(s, "driver", f"{where}: signal {s.get('name')!r}")),
            receiver=str(_require(s, "receiver", f"{where}: signal {s.get('name')!r}")),
            width=s.get("width"),
            required=bool(s.get("required", True)),
            description=s.get("description"),
            section=s.get("section"),
        )
        for s in doc.get("signals") or ()
    )
    duplicate = _first_duplicate([s.name for s in signals])
    if duplicate is not None:
        raise ProtocolSpecError(f"{where}: signal {duplicate!r} is declared more than once")

    parameters = tuple(
        Parameter(
            name=str(_require(p, "name", f"{where}: parameter")),
            default=p.get("default"),
            description=p.get("description"),
            allowed=tuple(p["allowed"]) if p.get("allowed") is not None else None,
            section=p.get("section"),
        )
        for p in doc.get("parameters") or ()
    )
    duplicate = _first_duplicate([p.name for p in parameters])
    if duplicate is not None:
        raise ProtocolSpecError(f"{where}: parameter {duplicate!r} is declared more than once")

    rules = tuple(
        Rule(
            id=str(_require(r, "id", f"{where}: rule")),
            text=str(_require(r, "text", f"{where}: rule {r.get('id')!r}")),
            section=r.get("section"),
        )
        for r in doc.get("rules") or ()
    )
    duplicate = _first_duplicate([r.id for r in rules])
    if duplicate is not None:
        raise ProtocolSpecError(f"{where}: rule {duplicate!r} is declared more than once")

    _check_licence(protocol_id, provenance, signals, rules, where)

    # A width naming a parameter must name one this document actually defines, or the reference is
    # a dead end at exactly the moment a consumer tries to size a port.
    parameter_names = {p.name for p in parameters}
    for signal in signals:
        if isinstance(signal.width, str) and not _width_expression_is_resolvable(
            signal.width, parameter_names
        ):
            raise ProtocolSpecError(
                f"{where}: signal {signal.name!r} has width {signal.width!r}, which references a "
                f"parameter this document doesn't define (it defines {sorted(parameter_names)})"
            )

    roles = tuple(doc.get("roles") or ())
    handshaking = _parse_handshaking(
        doc.get("handshakes"), signals=signals, rules=rules, roles=roles, where=where
    )

    return Protocol(
        schema_version=schema_version,
        id=protocol_id,
        version=str(_require(doc, "version", where)),
        title=str(_require(doc, "title", where)),
        kind=kind,
        provenance=provenance,
        signals=signals,
        parameters=parameters,
        rules=rules,
        roles=roles,
        summary=doc.get("summary"),
        coverage_note=doc.get("coverage_note"),
        handshaking=handshaking,
    )


def _parse_handshaking(
    raw: Any,
    *,
    signals: tuple[Signal, ...],
    rules: tuple[Rule, ...],
    roles: tuple[str, ...],
    where: str,
) -> Handshaking | None:
    """Parse and cross-validate a `handshakes:` block. Everything it may reference — signals,
    roles, rule ids — must exist in this same document, checked here at parse time because the
    alternative is an SVA emitter producing assertions that cite rules nobody quoted or ports
    nobody defined."""
    if raw is None:
        return None
    w = f"{where}: handshakes"
    if not isinstance(raw, dict):
        raise ProtocolSpecError(f"{w}: expected a mapping with clock/active_low_reset/phases")

    signal_by_name = {s.name: s for s in signals}
    rule_ids = {r.id for r in rules}

    def _known_signal(name: Any, what: str) -> str:
        if name not in signal_by_name:
            raise ProtocolSpecError(
                f"{w}: {what} names signal {name!r}, which this document does not define "
                f"(it defines {sorted(signal_by_name)})"
            )
        return str(name)

    def _known_rule(rule_id: Any, what: str) -> str:
        if rule_id not in rule_ids:
            raise ProtocolSpecError(
                f"{w}: {what} cites rule {rule_id!r}, which this document does not quote — "
                "machine-checkable semantics may only encode what the vendored normative text "
                "states (it quotes " + (f"{sorted(rule_ids)})" if rule_ids else "no rules)")
            )
        return str(rule_id)

    def _control_signal(name: Any, what: str) -> str:
        signal = signal_by_name[_known_signal(name, what)]
        if signal.width not in (1, None):
            raise ProtocolSpecError(
                f"{w}: {what} names signal {name!r} with width {signal.width!r} — handshake "
                "control signals are single wires"
            )
        return str(name)

    clock = _control_signal(_require(raw, "clock", w), "clock")
    reset = _control_signal(_require(raw, "active_low_reset", w), "active_low_reset")

    phases: list[HandshakePhase] = []
    for entry in _require(raw, "phases", w) or ():
        name = str(_require(entry, "name", f"{w}: phase"))
        pw = f"phase {name!r}"
        requester_role = str(_require(entry, "requester_role", f"{w}: {pw}"))
        if requester_role not in roles:
            raise ProtocolSpecError(
                f"{w}: {pw} has requester_role {requester_role!r}, not one of {list(roles)}"
            )
        request = _control_signal(_require(entry, "request", f"{w}: {pw}"), f"{pw} request")
        accept = _control_signal(_require(entry, "accept", f"{w}: {pw}"), f"{pw} accept")
        # The requester drives the request wire and the *other* party drives the accept wire; a
        # phase claiming otherwise contradicts the document's own Table 1 driver column.
        if signal_by_name[request].driver != requester_role:
            raise ProtocolSpecError(
                f"{w}: {pw} says {requester_role!r} requests via {request!r}, but the document "
                f"says {request!r} is driven by {signal_by_name[request].driver!r}"
            )
        if signal_by_name[accept].driver == requester_role:
            raise ProtocolSpecError(
                f"{w}: {pw}: accept signal {accept!r} is driven by the requester "
                f"{requester_role!r} — a party cannot grant its own request"
            )

        no_retract = entry.get("no_retract")
        no_retract_rule_id = (
            _known_rule(_require(no_retract, "rule_id", f"{w}: {pw} no_retract"), f"{pw} no_retract")
            if no_retract is not None
            else None
        )
        reset_low = tuple(
            SignalObligation(
                signal=_control_signal(
                    _require(r, "signal", f"{w}: {pw} reset_low"), f"{pw} reset_low"
                ),
                rule_id=_known_rule(
                    _require(r, "rule_id", f"{w}: {pw} reset_low"), f"{pw} reset_low"
                ),
            )
            for r in entry.get("reset_low") or ()
        )
        stable_until_accept = []
        for s in entry.get("stable_until_accept") or ():
            signal_name = _known_signal(
                _require(s, "signal", f"{w}: {pw} stable_until_accept"), f"{pw} stable_until_accept"
            )
            # Stability is the requester's obligation over signals the requester drives; a stable
            # claim on the other party's wire would assert a rule the cited text does not state.
            if signal_by_name[signal_name].driver != requester_role:
                raise ProtocolSpecError(
                    f"{w}: {pw} stable_until_accept names {signal_name!r}, driven by "
                    f"{signal_by_name[signal_name].driver!r}, not by the requester "
                    f"{requester_role!r}"
                )
            stable_until_accept.append(
                SignalObligation(
                    signal=signal_name,
                    rule_id=_known_rule(
                        _require(s, "rule_id", f"{w}: {pw} stable_until_accept"),
                        f"{pw} stable_until_accept",
                    ),
                )
            )

        phases.append(
            HandshakePhase(
                name=name,
                requester_role=requester_role,
                request=request,
                accept=accept,
                rule_id=_known_rule(_require(entry, "rule_id", f"{w}: {pw}"), pw),
                no_retract_rule_id=no_retract_rule_id,
                reset_low=reset_low,
                stable_until_accept=tuple(stable_until_accept),
            )
        )

    duplicate = _first_duplicate([p.name for p in phases])
    if duplicate is not None:
        raise ProtocolSpecError(f"{w}: phase {duplicate!r} is declared more than once")
    if not phases:
        raise ProtocolSpecError(f"{w}: a handshakes block with no phases says nothing — omit it")
    return Handshaking(clock=clock, active_low_reset=reset, phases=tuple(phases))


def _first_duplicate(names: list[str]) -> str | None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            return name
        seen.add(name)
    return None


def _width_expression_is_resolvable(expression: str, parameter_names: set[str]) -> bool:
    """A width string is either a bare parameter name or a simple expression over them
    (`"DATA_WIDTH/8"`). Every identifier in it must be a parameter this document defines."""
    identifiers = {
        token for token in _split_identifiers(expression) if not token.isdigit() and token
    }
    return identifiers <= parameter_names


def _split_identifiers(expression: str) -> list[str]:
    out, current = [], ""
    for char in expression:
        if char.isalnum() or char == "_":
            current += char
        else:
            out.append(current)
            current = ""
    out.append(current)
    return out


def load_protocol(path: str | Path) -> Protocol:
    """Load and validate one protocol document from a YAML file."""
    path = Path(path)
    doc = yaml.safe_load(path.read_text())
    return parse_protocol(doc, where=str(path))
