"""Protocol specifications as structured data (docs/decisions.md D174)."""

from __future__ import annotations

from .conform import (
    ConformanceReport,
    Finding,
    ParsedPort,
    check_module_conformance,
    parse_module_ports,
)
from .derive import UNDERIVABLE, derive_stream_from_bus, derived_matches_reference
from .emit import emit_sv_module, emit_sv_ports
from .errors import (
    LicenceViolationError,
    ProtocolSpecError,
    UnknownProtocolError,
    UnsourcedFactError,
)
from .registry import (
    ProtocolRegistry,
    ReferenceResolution,
    load_all,
    protocol_references_in,
    resolve_ir_reference,
    specs_dir,
)
from .spec import (
    SCHEMA_VERSION,
    Parameter,
    Protocol,
    Provenance,
    Rule,
    Signal,
    load_protocol,
    parse_protocol,
)

__all__ = [
    "SCHEMA_VERSION",
    "ConformanceReport",
    "Finding",
    "ParsedPort",
    "UNDERIVABLE",
    "LicenceViolationError",
    "Parameter",
    "Protocol",
    "ProtocolRegistry",
    "ProtocolSpecError",
    "Provenance",
    "ReferenceResolution",
    "Rule",
    "Signal",
    "UnknownProtocolError",
    "UnsourcedFactError",
    "check_module_conformance",
    "derive_stream_from_bus",
    "emit_sv_module",
    "emit_sv_ports",
    "derived_matches_reference",
    "load_all",
    "parse_module_ports",
    "load_protocol",
    "parse_protocol",
    "protocol_references_in",
    "resolve_ir_reference",
    "specs_dir",
]
