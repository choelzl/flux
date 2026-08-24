"""Unit tests for flux_redaction.policy (docs/decisions.md D94): real confidentiality-policy
enforcement — the mechanism that makes D93's own redaction layer load-bearing, not just
available. No real confidential PDK exists in this sandbox (D92's own real finding) — these tests
exercise the real enforcement *code* against a synthetic, clearly-labeled test registration, not
fabricated confidential silicon data.
"""

from __future__ import annotations

import pytest
from flux_redaction import (
    ConfidentialPdkError,
    UnknownPdkError,
    is_confidential,
    register_pdk,
    require_not_confidential,
)


def test_asap7_is_registered_as_real_verified_non_confidential():
    """The one real registration this repo actually needs today — BSD-3-Clause, checked directly
    against upstream (docs/decisions.md D92)."""
    assert is_confidential("asap7") is False


def test_require_not_confidential_is_a_real_no_op_for_asap7():
    require_not_confidential("asap7")  # must not raise


def test_unknown_pdk_raises_not_silently_assumed_non_confidential():
    with pytest.raises(UnknownPdkError):
        is_confidential("some-pdk-nobody-registered")
    with pytest.raises(UnknownPdkError):
        require_not_confidential("some-pdk-nobody-registered")


def test_register_pdk_then_require_not_confidential_raises_for_a_real_confidential_registration():
    """The real, structural point of this whole module: a PDK registered as confidential is
    genuinely refused, not just discouraged. A synthetic, clearly-labeled test registration —
    real enforcement code, not fabricated confidential silicon data."""
    register_pdk("test-confidential-pdk-d94", confidential=True, reason="synthetic test registration, not a real PDK")
    try:
        assert is_confidential("test-confidential-pdk-d94") is True
        with pytest.raises(ConfidentialPdkError, match="test-confidential-pdk-d94"):
            require_not_confidential("test-confidential-pdk-d94")
    finally:
        # Real cleanup — the module-level registry is process-global; don't leak this test's own
        # synthetic entry into other tests that might run in the same process.
        from flux_redaction import policy as policy_module
        policy_module._REGISTRY.pop("test-confidential-pdk-d94", None)


def test_register_pdk_non_confidential_does_not_raise():
    register_pdk("test-open-pdk-d94", confidential=False, reason="synthetic test registration, not a real PDK")
    try:
        require_not_confidential("test-open-pdk-d94")  # must not raise
    finally:
        from flux_redaction import policy as policy_module
        policy_module._REGISTRY.pop("test-open-pdk-d94", None)


def test_confidential_pdk_error_message_names_the_real_reason():
    register_pdk("test-confidential-pdk-d94-reason", confidential=True, reason="a specific, real, checkable reason")
    try:
        with pytest.raises(ConfidentialPdkError, match="a specific, real, checkable reason"):
            require_not_confidential("test-confidential-pdk-d94-reason")
    finally:
        from flux_redaction import policy as policy_module
        policy_module._REGISTRY.pop("test-confidential-pdk-d94-reason", None)


# --- Review-driven fix (docs/decisions.md D96): enforcement in the engine itself ---


def test_the_raw_engine_entry_point_itself_refuses_for_a_confidential_pdk():
    """D94 guarded only the CHIA-node wrapper — a direct import of
    flux_codegen_rtl_harness.asap7.synthesize_with_asap7 was an unguarded path around the check,
    returning real absolute areas for a confidential-flagged PDK (review finding). The guard now
    runs before any real synthesis, so this test needs no Yosys at all: the refusal must happen
    before a work dir is even created."""
    from flux_codegen_rtl_harness.asap7 import synthesize_with_asap7
    from flux_redaction import policy as policy_module

    real_entry = policy_module._REGISTRY["asap7"]
    register_pdk("asap7", confidential=True, reason="synthetic test re-registration, not a real fact")
    try:
        with pytest.raises(ConfidentialPdkError, match="asap7"):
            synthesize_with_asap7("module m(); endmodule", "m")
    finally:
        policy_module._REGISTRY["asap7"] = real_entry
    # And restored: the real, verified non-confidential registration is intact afterwards.
    assert is_confidential("asap7") is False


def test_a_confidential_registration_cannot_be_silently_downgraded():
    """`register_pdk` was a bare dict assignment, so any later call could relax a confidential PDK
    to non-confidential and the raw, unredacted synthesis path would start working again — silently,
    in the one place where silence means proprietary numbers reaching a public model
    (docs/decisions.md D184).
    """
    register_pdk("d184-downgrade-target", confidential=True, reason="synthetic test registration")

    with pytest.raises(ConfidentialPdkError, match="refusing to silently re-register"):
        register_pdk("d184-downgrade-target", confidential=False, reason="oops")

    assert is_confidential("d184-downgrade-target") is True


def test_tightening_and_idempotent_re_registration_are_always_allowed():
    """The guard must only block the unsafe direction. Re-affirming the same terms, or tightening a
    PDK to confidential, are both safe and are exactly what an existing integration test does."""
    register_pdk("d184-tighten-target", confidential=False, reason="synthetic, public")
    register_pdk("d184-tighten-target", confidential=False, reason="re-affirmed, still public")
    register_pdk("d184-tighten-target", confidential=True, reason="tightened after review")

    assert is_confidential("d184-tighten-target") is True


def test_a_deliberate_downgrade_is_possible_when_stated_explicitly():
    """A registration made in error must be correctable — the guard is against silence, not against
    ever changing one's mind."""
    register_pdk("d184-correction-target", confidential=True, reason="registered in error")

    register_pdk(
        "d184-correction-target", confidential=False,
        reason="license re-checked: BSD-3-Clause", allow_downgrade=True,
    )

    assert is_confidential("d184-correction-target") is False
