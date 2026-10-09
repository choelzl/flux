"""PDK confidentiality policy enforcement (D94, D96).

An RTL application's `rtl.py measure` calls `require_not_confidential("asap7")` before any
synthesis; the guarantee is "the public path refuses", not "no code can obtain the number".

Only `asap7` (BSD-3-Clause, non-confidential) is registered; tests exercise enforcement with a
synthetic confidential entry (D92).
"""

from __future__ import annotations

from dataclasses import dataclass


class ConfidentialPdkError(PermissionError):
    """An unredacted call was attempted against a PDK registered as confidential."""


class UnknownPdkError(KeyError):
    """A PDK has no explicit confidentiality registration; none is assumed by omission."""


@dataclass(frozen=True, slots=True)
class PdkConfidentiality:
    pdk_name: str
    confidential: bool
    reason: str


_REGISTRY: dict[str, PdkConfidentiality] = {
    "asap7": PdkConfidentiality(
        pdk_name="asap7",
        confidential=False,
        reason=(
            "BSD 3-Clause (Lawrence T. Clark, Vinay Vashishtha, Arizona State University), "
            "verified directly against github.com/The-OpenROAD-Project/asap7sc7p5t_28's own "
            "LICENSE file and the identical header embedded in every real .lib file "
            "(docs/decisions.md D92) — a real, academic/predictive PDK, not a proprietary one."
        ),
    ),
}


def require_not_confidential(pdk_name: str) -> None:
    """Called by a raw (unredacted) synthesis path before returning any absolute value.
    Raises `ConfidentialPdkError` if confidential, `UnknownPdkError` if unregistered.
    """
    entry = _REGISTRY.get(pdk_name)
    if entry is None:
        raise UnknownPdkError(
            f"pdk_name={pdk_name!r} has no real, declared confidentiality registration — "
            f"refusing to assume either way. Known PDKs: {sorted(_REGISTRY)}."
        )
    if entry.confidential:
        raise ConfidentialPdkError(
            f"{pdk_name!r} is registered as confidential ({entry.reason}) — raw, unredacted "
            "synthesis results cannot be returned."
        )
