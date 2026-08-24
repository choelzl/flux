"""Deriving a stream protocol from a bus protocol by subtraction (docs/decisions.md D176).

A memory-mapped bus and a stream differ structurally rather than arbitrarily: a bus carries an
address phase, a data phase and a response phase; a stream is the data phase alone. So the stream
form of an AXI-family bus can be *constructed* from the bus document already loaded — keep the
write-data channel, drop the address and response channels, rename — instead of being sourced
separately.

**The mapping below is Flux's reasoning, not a quotation**, and that distinction is the whole
reason this module is a function rather than a shipped document. A YAML file in `specs/` asserts
"a source says this"; this asserts "we constructed this, here is the rule, and here is what it
agrees with". `derived_matches_reference` is the second half of that claim: the derived signal set
is checked against `axi4-stream@verilog-axis-*`, an independently-written MIT implementation that
shares no source with the pulp-platform documents the derivation reads.

Measured, and worth knowing before relying on it: deriving from `axi4` yields six of the eight
signals that implementation carries, deriving from `axi4-lite` yields four, and **neither produces
a signal the implementation lacks**. The derivation is sound and incomplete, not approximate — it
under-produces rather than inventing. What it cannot reach is `tid` and `tdest`: routing
identifiers with no write-data-channel analogue (a bus carries ids on its address and response
channels, and has nothing corresponding to a stream destination at all).
"""

from __future__ import annotations

from dataclasses import replace

from .errors import ProtocolSpecError
from .spec import Protocol, Provenance, Signal

# Which write-data-channel signal becomes which stream signal. Flux's mapping, validated against an
# independent implementation rather than asserted — see the module docstring.
_W_CHANNEL_TO_STREAM = {
    "w_data": "tdata",
    "w_strb": "tkeep",
    "w_valid": "tvalid",
    "w_ready": "tready",
    "w_last": "tlast",
    "w_user": "tuser",
}
# Channels a stream does not have: address (write and read) and response (write and read).
_DROPPED_CHANNEL_PREFIXES = ("aw_", "ar_", "b_", "r_")
# Stream signals no bus write-data channel can produce, with the reason. Recorded rather than
# silently absent, because a caller comparing a derived stream against a real one needs to know
# which gaps are expected.
UNDERIVABLE = {
    "tid": "a bus carries transaction ids on its address and response channels, not on write data",
    "tdest": "a bus has no analogue of a stream destination — routing is by address",
}


def derive_stream_from_bus(bus: Protocol, *, stream_id: str | None = None) -> Protocol:
    """Construct the stream form of an AXI-family bus protocol by keeping its write-data channel.

    The result is a real `Protocol` — same type, same accessors — but its provenance says plainly
    that it was derived, names the document it was derived from, and carries `normative: false`.
    It is deliberately not written to `specs/`: a file there claims a source states its contents,
    and no source states these.
    """
    if bus.kind != "bus":
        raise ProtocolSpecError(
            f"can only derive a stream from a bus protocol; {bus.ref} has kind {bus.kind!r}"
        )

    kept = [s for s in bus.signals if s.name in _W_CHANNEL_TO_STREAM]
    if not kept:
        raise ProtocolSpecError(
            f"{bus.ref} has no write-data channel signals ({sorted(_W_CHANNEL_TO_STREAM)}), so "
            "there is nothing to derive a stream from. This derivation is specific to the "
            "AXI-family channel naming, not a general bus-to-stream transform."
        )
    dropped = [s.name for s in bus.signals if s.name.startswith(_DROPPED_CHANNEL_PREFIXES)]

    signals: list[Signal] = []
    for signal in bus.signals:
        if signal.is_global:
            signals.append(signal)
            continue
        stream_name = _W_CHANNEL_TO_STREAM.get(signal.name)
        if stream_name is None:
            continue
        # A stream's source drives everything but tready; the bus already encodes that on its own
        # write-data channel (w_ready runs the other way), so direction carries over unchanged
        # apart from renaming the roles.
        signals.append(replace(
            signal,
            name=stream_name,
            driver=_stream_role(signal.driver, bus),
            receiver=_stream_role(signal.receiver, bus),
        ))

    parameters = tuple(
        p for p in bus.parameters
        if any(isinstance(s.width, str) and p.name in s.width for s in signals)
    )

    return Protocol(
        schema_version=bus.schema_version,
        id=stream_id or f"{bus.id}-stream-derived",
        version=f"derived-from-{bus.version}",
        title=f"Stream form of {bus.title}, derived by keeping the write-data channel",
        kind="stream",
        provenance=Provenance(
            document=f"derived from {bus.provenance.document}",
            publisher="Flux (derived, not quoted)",
            licence=bus.provenance.licence,
            redistributable=bus.provenance.redistributable,
            normative=False,
            url=bus.provenance.url,
            retrieved=bus.provenance.retrieved,
            implements=dict(bus.provenance.implements or {}) or None,
            note=(
                f"Constructed from {bus.ref} by keeping the write-data channel "
                f"({sorted(_W_CHANNEL_TO_STREAM)}) and dropping the address and response channels "
                f"({len(dropped)} signals). The mapping is Flux's reasoning, not a quotation from "
                "any specification, and the source document's licence is carried over because the "
                "structure came from it. Validated against an independently-written implementation "
                "— see flux_protocols.derive.derived_matches_reference — rather than trusted."
            ),
        ),
        signals=tuple(signals),
        parameters=parameters,
        rules=(),  # no source states rules about the derived protocol; inventing them would be fabrication
        roles=("source", "sink"),
        summary=f"Derived stream form of {bus.ref}: the write-data channel alone.",
        coverage_note=(
            "Derived, not sourced. Sound but incomplete: it produces no signal the reference "
            f"implementation lacks, and cannot produce {sorted(UNDERIVABLE)} — "
            + "; ".join(f"{name} ({reason})" for name, reason in sorted(UNDERIVABLE.items()))
            + ". Carries no rules: no document states requirements about a protocol Flux "
            "constructed."
        ),
    )


def _stream_role(role: str, bus: Protocol) -> str:
    """Bus roles become stream roles positionally: whichever role drives write data is the source."""
    if role == "global":
        return role
    driver_of_data = next((s.driver for s in bus.signals if s.name == "w_data"), None)
    return "source" if role == driver_of_data else "sink"


def derived_matches_reference(derived: Protocol, reference: Protocol) -> dict[str, list[str]]:
    """Compare a derived stream against an independently-sourced one.

    Returns `{"agreed": [...], "underived": [...], "invented": [...]}` over non-global signal
    names. `invented` is the one that must always be empty: a derived signal the reference lacks
    means the derivation is producing facts rather than restructuring them. `underived` is expected
    and informative — it is what the subtraction cannot reach.
    """
    derived_names = {s.name for s in derived.signals if not s.is_global}
    reference_names = {s.name for s in reference.signals if not s.is_global}
    return {
        "agreed": sorted(derived_names & reference_names),
        "underived": sorted(reference_names - derived_names),
        "invented": sorted(derived_names - reference_names),
    }
