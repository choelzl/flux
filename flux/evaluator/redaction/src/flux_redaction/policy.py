"""Real confidentiality-policy enforcement (docs/decisions.md D94, tightened in D96) — G15's own
real remaining piece after D93: a redacted surface existed, but nothing stopped a caller from
reaching for the raw, unredacted one instead — the redaction layer was *available*, not
*load-bearing*. Enforcement now lives in the raw engine entry point itself
(`flux_codegen_rtl_harness.asap7.synthesize_with_asap7` calls `require_not_confidential` before
any real synthesis — D94 originally guarded only the CHIA-node wrapper, and a review found the
direct engine import was an unguarded path around it). The one sanctioned bypass is the
underscore-private `_synthesize_with_asap7_unchecked`, used solely by the redacted surface to
compute deltas that never leave unredacted — reaching for an underscore-private API is
out-of-contract by Python convention, so the honest claim is "the public raw path refuses",
not "no Python code could ever obtain the number."

**No real confidential PDK exists in this sandbox to register as `confidential=True` for real**
(D92's own real finding, unchanged) — `asap7` is registered here as `confidential=False`, a real,
verified fact (BSD-3-Clause, checked directly against its own upstream `LICENSE`), not a
placeholder. This module's own real tests exercise the enforcement mechanism itself against a
synthetic, clearly-labeled test registration — real policy-enforcement code, tested for real,
without fabricating confidential silicon data that doesn't exist here.
"""

from __future__ import annotations

from dataclasses import dataclass


class ConfidentialPdkError(PermissionError):
    """Raised when a real, unredacted call is attempted against a PDK registered as confidential
    — the real enforcement point that makes `redaction/`'s own mechanism load-bearing, not just
    available for a well-behaved caller to opt into.
    """


class UnknownPdkError(KeyError):
    """Raised for a PDK with no real, explicit confidentiality registration — no PDK is ever
    assumed non-confidential by omission; every real consumer must register one before its own
    raw synthesis path can be checked.
    """


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


def register_pdk(
    pdk_name: str, *, confidential: bool, reason: str, allow_downgrade: bool = False
) -> None:
    """Real, explicit registration for a new PDK — every real consumer must call this (or have
    it called on its behalf) before `require_not_confidential`/`is_confidential` will recognize
    it; there is no default. `reason` should name what was actually checked (a license file, an
    NDA's own real terms), the same "state what was verified, not just the conclusion" discipline
    every other real ingestion in this repo follows.

    **A second call cannot silently relax the first** (docs/decisions.md D184). Re-registering an
    already-confidential PDK as non-confidential is refused unless `allow_downgrade=True` is passed
    explicitly, because a bare dict assignment made this whole module's guarantee depend on no
    other code ever calling it again with looser terms — and the failure would be silent, in the
    one place where silence means proprietary numbers reaching a public model. The safe direction
    is unrestricted: tightening a PDK to confidential, or re-registering identical terms, always
    works.
    """
    existing = _REGISTRY.get(pdk_name)
    if (
        existing is not None
        and existing.confidential
        and not confidential
        and not allow_downgrade
    ):
        raise ConfidentialPdkError(
            f"{pdk_name!r} is already registered as confidential ({existing.reason}) — refusing to "
            "silently re-register it as non-confidential. If this is a deliberate correction, pass "
            "allow_downgrade=True and say why in `reason`."
        )
    _REGISTRY[pdk_name] = PdkConfidentiality(pdk_name=pdk_name, confidential=confidential, reason=reason)


def is_confidential(pdk_name: str) -> bool:
    """Raises `UnknownPdkError` if `pdk_name` has no real, explicit registration."""
    entry = _REGISTRY.get(pdk_name)
    if entry is None:
        raise UnknownPdkError(
            f"pdk_name={pdk_name!r} has no real, declared confidentiality registration — "
            f"refusing to assume either way. Known PDKs: {sorted(_REGISTRY)}."
        )
    return entry.confidential


def require_not_confidential(pdk_name: str) -> None:
    """The real enforcement call a raw (unredacted) synthesis path makes before returning any
    real absolute value. Raises `ConfidentialPdkError` if `pdk_name` is registered as
    confidential, `UnknownPdkError` if it has no registration at all — a real refusal either way,
    never a silent pass-through for a PDK this module doesn't have an explicit, checked answer
    for.
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
            "synthesis results cannot be returned; use the redacted surface instead."
        )
