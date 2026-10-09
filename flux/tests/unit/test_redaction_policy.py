"""flux_redaction.policy (D94, D96): the raw ASAP7 synthesis path refuses a PDK registered
confidential. Uses a synthetic, clearly-labeled registry entry, not confidential data.
"""

from __future__ import annotations

import pytest
from flux_redaction import ConfidentialPdkError, PdkConfidentiality, UnknownPdkError, require_not_confidential
from flux_redaction import policy as policy_module


def _confidential(name: str, reason: str = "synthetic test registration, not a real PDK") -> PdkConfidentiality:
    return PdkConfidentiality(pdk_name=name, confidential=True, reason=reason)


def test_require_not_confidential_is_a_no_op_for_asap7():
    """ASAP7 is registered as BSD-3-Clause, non-confidential (D92)."""
    require_not_confidential("asap7")  # must not raise


def test_unknown_pdk_raises_not_silently_assumed_non_confidential():
    with pytest.raises(UnknownPdkError):
        require_not_confidential("some-pdk-nobody-registered")


def test_a_confidential_pdk_is_refused_and_the_error_names_the_reason(monkeypatch):
    monkeypatch.setitem(policy_module._REGISTRY, "test-confidential-pdk-d94",
                        _confidential("test-confidential-pdk-d94", "a specific, checkable reason"))
    with pytest.raises(ConfidentialPdkError, match="a specific, checkable reason"):
        require_not_confidential("test-confidential-pdk-d94")


def test_an_rtl_applications_measure_refuses_a_confidential_asap7(monkeypatch):
    """`rtl.py measure` asks the policy before Yosys runs (D94, D948): no tool is needed to see it refuse."""
    import rtl

    monkeypatch.setitem(policy_module._REGISTRY, "asap7", _confidential("asap7"))
    with pytest.raises(ConfidentialPdkError, match="asap7"):
        rtl.measure("module m(input a, output y); assign y = a; endmodule\n")
