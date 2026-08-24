"""The one failure mode a spec can have, for any target language (D453).

A DUT failing its test vectors is NOT this: that is a normal `HarnessRunResult`. This is a
spec the harness cannot proceed from, raised before any compiler is invoked so a bad spec never
costs a compile. Each language harness re-exports it, so one `except InvalidSpecError` written
against either package catches both -- they are the same class now, where the RTL package used
to import the SystemC package's.
"""

from __future__ import annotations


class InvalidSpecError(ValueError):
    """A `DesignSpec` or `CompositionSpec` is structurally invalid (bad port dir/dtype, no
    ports, duplicate names, a net connecting two widths) -- caught before any tool runs."""


__all__ = ["InvalidSpecError"]
