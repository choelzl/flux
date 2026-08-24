"""The one failure mode a spec can have, for any target language (D453).

A DUT failing its test vectors is not this (that is a normal `HarnessRunResult`). This is a spec
the harness cannot proceed from, raised before any compiler runs. Each language harness
re-exports this same class, so one `except InvalidSpecError` catches both.
"""

from __future__ import annotations


class InvalidSpecError(ValueError):
    """A `DesignSpec` or `CompositionSpec` is structurally invalid (bad port dir/dtype, no
    ports, duplicate names, a net connecting two widths) -- caught before any tool runs."""


__all__ = ["InvalidSpecError"]
