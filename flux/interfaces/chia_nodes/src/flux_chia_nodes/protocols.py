"""`flux_protocol_lookup` and `flux_check_ir_protocols` — the CHIA-node surface over
`flux_protocols` (docs/decisions.md D174).

The lookup node exists for the same reason `flux_explain_candidate` (D157) does: an agent asked to
wire up or evaluate an interface otherwise has to guess signal names and widths from its own
training data, and a guess that looks right is indistinguishable from a fact. Every answer here
carries the provenance of the document it came from, including whether that document is the
standard or an implementation of it.

The check node is what makes this more than a reference shelf. `ir/architecture/examples/
generic-riscv-soc-v1.yaml` declares `noc: {model: "axi4@2.0"}` and `soc-dma-desc-fetch.yaml`
declares `protocol: axi4`, and docs/decisions.md D31 recorded that nothing in the repo parsed
either — they were free-text labels no code could be wrong about, because no code read them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_protocols import (
    ProtocolRegistry,
    check_module_conformance,
    protocol_references_in,
    resolve_ir_reference,
)


@dataclass(frozen=True, slots=True)
class ProtocolCheck:
    """One IR protocol reference and what it resolved to."""

    reference: str
    resolved: bool
    protocol_ref: str | None
    title: str | None
    normative: bool | None
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "resolved": self.resolved,
            "protocol_ref": self.protocol_ref,
            "title": self.title,
            "normative": self.normative,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ProtocolCheckReport:
    checks: list[ProtocolCheck]
    all_resolved: bool
    known_protocols: list[str]
    checked: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "all_resolved": self.all_resolved,
            "known_protocols": self.known_protocols,
            "checked": self.checked,
        }


@ChiaFunction()
def flux_protocol_lookup(
    protocol_id: str, version: str | None = None, signal: str | None = None
) -> dict[str, Any]:
    """Look up a protocol's structured facts: signals with widths and directions, parameters, and
    the numbered rules of its source document.

    `protocol_id` is e.g. `"obi"`, `"axi4"`, `"axi4-lite"`, `"wishbone"`. `version` picks one when
    several ship — required in that case rather than defaulting, since version differences are the
    reason versions are recorded. `signal` narrows the answer to one signal.

    Every result carries `provenance`, and reading it matters: `normative: false` means the facts
    were read from an *implementation* of the standard rather than the standard itself, which is
    how AXI is representable at all (Arm's specification cannot be redistributed — D31).
    """
    registry = ProtocolRegistry()
    protocol = registry.get(protocol_id, version)
    if signal is None:
        return protocol.to_dict()
    return {
        "protocol_ref": protocol.ref,
        "provenance": protocol.provenance.to_dict(),
        "signal": protocol.signal(signal).to_dict(),
    }


@ChiaFunction()
def flux_list_protocols() -> dict[str, Any]:
    """Every protocol this build ships, with its source and whether that source is normative."""
    registry = ProtocolRegistry()
    return {
        "protocols": [
            {
                "ref": p.ref,
                "id": p.id,
                "version": p.version,
                "title": p.title,
                "kind": p.kind,
                "summary": p.summary,
                "normative": p.provenance.normative,
                "licence": p.provenance.licence,
                "signals": len(p.signals),
                "rules": len(p.rules),
            }
            for p in registry.all()
        ],
        "count": len(registry),
    }


@ChiaFunction()
def flux_check_ir_protocols(document: dict[str, Any]) -> dict[str, Any]:
    """Resolve every protocol reference in an IR document against the shipped protocol specs.

    Reports rather than raises: one unknown protocol string is a finding about that field, not a
    reason to abandon the rest of the document. `all_resolved` is `True` for a document with *no*
    protocol references at all — vacuously, and `checks` being empty is how a caller tells that
    apart from "everything checked out".
    """
    registry = ProtocolRegistry()
    references = protocol_references_in(document)
    checks = []
    for reference in references:
        resolution = resolve_ir_reference(reference, registry=registry)
        protocol = resolution.protocol
        checks.append(ProtocolCheck(
            reference=reference,
            resolved=resolution.resolved,
            protocol_ref=protocol.ref if protocol is not None else None,
            title=protocol.title if protocol is not None else None,
            normative=protocol.provenance.normative if protocol is not None else None,
            reason=resolution.reason,
        ))
    report = ProtocolCheckReport(
        checks=checks,
        all_resolved=all(c.resolved for c in checks),
        known_protocols=sorted(p.ref for p in registry.all()),
        checked=(
            "Resolves `protocol`/`model` string fields against the protocol documents this build "
            "ships. An unresolved reference means Flux has no sourced description of that "
            "protocol at that version — not that the protocol is wrong or does not exist."
        ),
    )
    return report.to_dict()


@ChiaFunction()
def flux_check_protocol_conformance(
    source: str,
    protocol_id: str,
    role: str,
    version: str | None = None,
    module_name: str | None = None,
    parameters: dict[str, int] | None = None,
    prefix: str = "",
) -> dict[str, Any]:
    """Does this SystemVerilog module present a conformant interface for `protocol_id` as `role`?

    The point of this node is generated RTL (docs/decisions.md D178). docs/decisions.md D39/D43
    split responsibility as "verification owns structure, LLM owns behaviour", and a model-written
    bus interface with a reversed `req`/`gnt` pair passes Verilator without complaint. This checks
    the structure against a sourced protocol document instead of against the model's memory.

    `prefix` strips a per-interface naming prefix before matching (`s_axis_tdata` against `tdata`);
    global signals like a clock are never prefixed. `parameters` supplies values for parameterised
    widths — without them, widths are reported as unchecked notes rather than guessed at.

    `conforms=True` means the interface is *shaped* right: names, directions, widths. It says
    nothing about handshake ordering or timing, which the source documents mostly do not state in a
    form this schema carries. Read `checked` on the result.
    """
    registry = ProtocolRegistry()
    protocol = registry.get(protocol_id, version)
    report = check_module_conformance(
        source, protocol, role=role, module_name=module_name,
        parameters=parameters, prefix=prefix,
    )
    return report.to_dict()
