"""Errors for `flux_protocols` (docs/decisions.md D174)."""

from __future__ import annotations


class ProtocolSpecError(Exception):
    """A protocol document is malformed, or claims something the schema won't let it claim."""


class UnsourcedFactError(ProtocolSpecError):
    """A protocol document carries content without the provenance that would justify it.

    This is the module's central guard rather than a validation nicety. The whole point of a
    "wisdom" store is that other code trusts what it says, so a fact whose source is unstated is
    worse than a missing fact — it is a plausible-looking claim with nothing behind it, which is
    exactly what this repo's "real, not fabricated" discipline exists to prevent.
    """


class LicenceViolationError(ProtocolSpecError):
    """A document reproduces content from a source that does not permit redistribution.

    docs/decisions.md D31 established that AMBA/AXI, JEDEC, PCIe and I2C are closed — Arm's own
    wording is "No part of the document may be reproduced in any form by any means without the
    express prior written permission of Arm." A store of protocol facts is precisely the sort of
    thing that would quietly accumulate such text, so `redistributable: false` makes quoted rule
    text and prose descriptions unrepresentable rather than merely discouraged.
    """


class UnknownProtocolError(ProtocolSpecError):
    """A lookup named a protocol (or a version of one) that the registry has no document for."""
