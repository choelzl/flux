"""Loading and lookup over the bundled protocol documents (docs/decisions.md D174).

`resolve_ir_reference` is the reason this module is more than a data directory. The Architecture
and Workload IRs already name protocols — `noc: {model: "axi4@2.0"}` in
`ir/architecture/examples/generic-riscv-soc-v1.yaml`, `protocol: axi4` in
`ir/workload/examples/soc-dma-desc-fetch.yaml` — and D31 recorded that *nothing parsed either*:
"grep -rn 'axi4' across every .py file in flux/ returns nothing: no translator or evaluator parses
that string, it's a free-text label". D31 declined to ingest bus specs for exactly that reason, and
named the condition for revisiting: ingest them "with that functional work", when something
actually consumes bus semantics. This function is that consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import UnknownProtocolError
from .spec import Protocol, load_protocol

_SPECS_DIR = Path(__file__).resolve().parents[2] / "specs"


def specs_dir() -> Path:
    return _SPECS_DIR


def load_all(specs_root: str | Path | None = None) -> list[Protocol]:
    """Load every protocol document under `specs_root` (the bundled `protocols/specs/` by default).

    Raises rather than returning an empty list when the directory is missing — the same lesson
    docs/decisions.md D172 recorded for `load_corpus`, where `Path.glob` on a nonexistent directory
    yielded nothing and a typo'd root became indistinguishable from a genuinely empty one.
    """
    root = Path(specs_root) if specs_root is not None else _SPECS_DIR
    if not root.is_dir():
        raise FileNotFoundError(
            f"protocol specs directory {str(root)!r} does not exist — an empty result here would "
            "be indistinguishable from 'this build ships no protocols'"
        )
    return [load_protocol(p) for p in sorted(root.glob("*.yaml"))]


class ProtocolRegistry:
    """Every bundled protocol document, indexed for lookup by id and version."""

    def __init__(self, specs_root: str | Path | None = None) -> None:
        self._protocols = load_all(specs_root)
        self._by_ref: dict[str, Protocol] = {}
        for protocol in self._protocols:
            if protocol.ref in self._by_ref:
                raise UnknownProtocolError(
                    f"two documents both describe {protocol.ref!r} — ambiguous which is authoritative"
                )
            self._by_ref[protocol.ref] = protocol

    def __len__(self) -> int:
        return len(self._protocols)

    def all(self) -> list[Protocol]:
        return list(self._protocols)

    def ids(self) -> list[str]:
        return sorted({p.id for p in self._protocols})

    def versions_of(self, protocol_id: str) -> list[str]:
        return sorted(p.version for p in self._protocols if p.id == protocol_id)

    def get(self, protocol_id: str, version: str | None = None) -> Protocol:
        """Look up one protocol. With no `version`, returns the only one if there is exactly one,
        and refuses to guess otherwise — silently picking "the latest" would answer a question the
        caller didn't ask, and version differences are the whole reason versions are recorded.
        """
        candidates = [p for p in self._protocols if p.id == protocol_id]
        if not candidates:
            raise UnknownProtocolError(
                f"no protocol {protocol_id!r}; this build ships {self.ids()}"
            )
        if version is not None:
            for protocol in candidates:
                if protocol.version == version:
                    return protocol
            raise UnknownProtocolError(
                f"protocol {protocol_id!r} has no version {version!r}; it ships "
                f"{self.versions_of(protocol_id)}"
            )
        if len(candidates) > 1:
            raise UnknownProtocolError(
                f"protocol {protocol_id!r} ships several versions "
                f"({self.versions_of(protocol_id)}) — name one rather than relying on a default"
            )
        return candidates[0]


@dataclass(frozen=True, slots=True)
class ReferenceResolution:
    """What an IR protocol reference (`"axi4@2.0"`, `"obi"`) resolved to, or why it didn't.

    A resolution failure is reported, never raised, because the callers are IR validators walking a
    whole document: one unknown protocol string should be a finding about that field, not an
    exception that abandons the rest of the check.
    """

    reference: str
    protocol: Protocol | None
    reason: str | None

    @property
    def resolved(self) -> bool:
        return self.protocol is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "resolved": self.resolved,
            "protocol": self.protocol.to_dict() if self.protocol is not None else None,
            "reason": self.reason,
        }


def resolve_ir_reference(
    reference: str, *, registry: ProtocolRegistry | None = None
) -> ReferenceResolution:
    """Resolve an IR protocol string to a `Protocol`.

    Accepts both shapes the IR already uses: `"axi4@2.0"` (with version, as
    `generic-riscv-soc-v1.yaml`'s `noc.model`) and `"axi4"` (bare, as `soc-dma-desc-fetch.yaml`'s
    `protocol`). A bare reference resolves only when the id ships exactly one version — see
    `ProtocolRegistry.get` for why that isn't defaulted.
    """
    registry = registry if registry is not None else ProtocolRegistry()
    raw = (reference or "").strip()
    if not raw:
        return ReferenceResolution(reference, None, "empty protocol reference")

    protocol_id, _, version = raw.partition("@")
    try:
        protocol = registry.get(protocol_id, version or None)
    except UnknownProtocolError as exc:
        return ReferenceResolution(raw, None, str(exc))
    return ReferenceResolution(raw, protocol, None)


def protocol_references_in(document: dict[str, Any]) -> list[str]:
    """Every protocol reference in an IR document, in document order.

    Deliberately shape-driven rather than path-driven: it collects the values of any `protocol` or
    `model` key anywhere in the tree, because the two IRs put them in different places already
    (`noc.model`, an interface's `protocol`) and a hardcoded path list would silently miss the
    third place someone adds next.
    """
    found: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("protocol", "model") and isinstance(value, str):
                    found.append(value)
                else:
                    _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(document)
    return found
