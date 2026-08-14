"""Unit tests for flux_calibration.apply_escalation_policy — synthetic Results, no real
evaluator involved. See tests/integration/test_calibration_live.py for the real-data version
(escalation applied to actually-calibrated ZigZag/Timeloop results).
"""

from __future__ import annotations

from flux_calibration import apply_escalation_policy
from flux_evaluator_abi import (
    Bottleneck,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)


def _estimate(value: float, ci_low: float | None = None, ci_high: float | None = None) -> Estimate:
    ci_low = value if ci_low is None else ci_low
    ci_high = value if ci_high is None else ci_high
    return Estimate(value=value, ci_low=ci_low, ci_high=ci_high, unit="cycles", method=Method.ANALYTIC)


def _result(metrics: dict[str, Estimate], domain: Domain) -> Result:
    return Result(
        metrics=metrics,
        validity=Validity(ok=True, checker_version="test"),
        domain=domain,
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator="test@0", inputs={}),
        escalation=Escalation(recommended=False),
    )


def test_in_domain_narrow_ci_does_not_escalate():
    result = _result(
        {"latency_cycles": _estimate(100.0, ci_low=95.0, ci_high=105.0)},
        domain=Domain(in_domain=True, distance=0.0),
    )
    escalated = apply_escalation_policy(result)
    assert escalated.escalation.recommended is False
    assert escalated.escalation.next_rung is None


def test_out_of_domain_triggers_escalation_even_with_narrow_ci():
    result = _result(
        {"latency_cycles": _estimate(100.0, ci_low=99.0, ci_high=101.0)},
        domain=Domain(in_domain=False, distance=1.0),
    )
    escalated = apply_escalation_policy(result)
    assert escalated.escalation.recommended is True
    assert "out of validated domain" in escalated.escalation.reason
    assert escalated.escalation.next_rung is not None


def test_wide_ci_triggers_escalation_even_when_in_domain():
    result = _result(
        {"latency_cycles": _estimate(100.0, ci_low=10.0, ci_high=300.0)},  # 290% relative width
        domain=Domain(in_domain=True, distance=0.0),
    )
    escalated = apply_escalation_policy(result)
    assert escalated.escalation.recommended is True
    assert "confidence interval exceeds" in escalated.escalation.reason
    assert "latency_cycles" in escalated.escalation.reason


def test_narrow_ci_within_custom_threshold_does_not_escalate():
    result = _result(
        {"latency_cycles": _estimate(100.0, ci_low=70.0, ci_high=130.0)},  # 60% relative width
        domain=Domain(in_domain=True, distance=0.0),
    )
    assert apply_escalation_policy(result, max_relative_ci_width=0.5).escalation.recommended is True
    assert apply_escalation_policy(result, max_relative_ci_width=0.9).escalation.recommended is False


def test_both_triggers_combine_into_one_reason_string():
    result = _result(
        {"latency_cycles": _estimate(100.0, ci_low=10.0, ci_high=300.0)},
        domain=Domain(in_domain=False, distance=1.0),
    )
    reason = apply_escalation_policy(result).escalation.reason
    assert "out of validated domain" in reason
    assert "confidence interval exceeds" in reason


def test_only_the_wide_metric_is_named_when_multiple_metrics_present():
    result = _result(
        {
            "latency_cycles": _estimate(100.0, ci_low=10.0, ci_high=300.0),  # wide
            "energy_pj": _estimate(50.0, ci_low=49.0, ci_high=51.0),  # narrow
        },
        domain=Domain(in_domain=True, distance=0.0),
    )
    reason = apply_escalation_policy(result).escalation.reason
    assert "latency_cycles" in reason
    assert "energy_pj" not in reason


def test_next_rung_names_the_real_rtl_evaluator():
    """`next_rung` used to be a placeholder string suffixed "(not implemented)" — evaluators/rtl/
    is real now (a real Verilator-simulation adapter, registered as "rtl" in
    flows/cli/registry.py), so this names it directly, not a promise-with-an-asterisk."""
    result = _result(
        {"latency_cycles": _estimate(100.0, ci_low=95.0, ci_high=105.0)},
        domain=Domain(in_domain=False, distance=1.0),
    )
    escalated = apply_escalation_policy(result)
    assert escalated.escalation.next_rung == "rtl"


def test_does_not_mutate_the_original_result():
    result = _result(
        {"latency_cycles": _estimate(100.0, ci_low=10.0, ci_high=300.0)},
        domain=Domain(in_domain=False, distance=1.0),
    )
    original_escalation = result.escalation
    apply_escalation_policy(result)
    assert result.escalation == original_escalation
    assert result.escalation.recommended is False
