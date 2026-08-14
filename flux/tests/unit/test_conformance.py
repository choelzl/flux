"""Unit tests for flux_calibration.conformance: pure comparison logic against synthetic Results,
no real evaluator or CHIA involved. See tests/integration/test_chia_flux_calibrate_and_conformance_live.py
for the real end-to-end version (real ZigZag as the declared model, real Verilator RTL as the
reference).
"""

from __future__ import annotations

from flux_calibration import check_conformance
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
    return Estimate(
        value=value,
        ci_low=value if ci_low is None else ci_low,
        ci_high=value if ci_high is None else ci_high,
        unit="cycles",
        method=Method.ANALYTIC,
    )


def _result(evaluator: str, metrics: dict[str, Estimate]) -> Result:
    return Result(
        metrics=metrics,
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


def test_conformant_when_reference_falls_inside_the_calibrated_ci():
    declared = _result("zigzag@1", {"latency_cycles": _estimate(150.0, ci_low=100.0, ci_high=200.0)})
    reference = _result("rtl@1", {"latency_cycles": _estimate(180.0)})

    report = check_conformance(declared, reference)

    assert report.ok is True
    assert report.per_metric["latency_cycles"].within_calibrated_ci is True
    assert report.per_metric["latency_cycles"].reference_value == 180.0


def test_not_conformant_when_reference_falls_outside_the_calibrated_ci():
    declared = _result("zigzag@1", {"latency_cycles": _estimate(150.0, ci_low=100.0, ci_high=200.0)})
    reference = _result("rtl@1", {"latency_cycles": _estimate(500.0)})

    report = check_conformance(declared, reference)

    assert report.ok is False
    assert report.per_metric["latency_cycles"].within_calibrated_ci is False


def test_one_bad_metric_fails_the_whole_report_even_if_others_agree():
    declared = _result(
        "zigzag@1",
        {
            "latency_cycles": _estimate(150.0, ci_low=100.0, ci_high=200.0),
            "energy_pj": _estimate(1000.0, ci_low=900.0, ci_high=1100.0),
        },
    )
    reference = _result(
        "rtl@1", {"latency_cycles": _estimate(180.0), "energy_pj": _estimate(5000.0)}
    )

    report = check_conformance(declared, reference)

    assert report.ok is False
    assert report.per_metric["latency_cycles"].within_calibrated_ci is True
    assert report.per_metric["energy_pj"].within_calibrated_ci is False


def test_only_shared_metrics_are_compared():
    declared = _result(
        "zigzag@1",
        {
            "latency_cycles": _estimate(150.0, ci_low=100.0, ci_high=200.0),
            "energy_pj": _estimate(1000.0, ci_low=900.0, ci_high=1100.0),
        },
    )
    reference = _result("rtl@1", {"latency_cycles": _estimate(180.0)})  # no energy_pj at all

    report = check_conformance(declared, reference)

    assert set(report.per_metric) == {"latency_cycles"}
    assert report.ok is True  # the one comparable metric agrees


def test_no_shared_metrics_is_not_conformant_not_vacuously_true():
    declared = _result("zigzag@1", {"latency_cycles": _estimate(150.0, ci_low=100.0, ci_high=200.0)})
    reference = _result("rtl@1", {"area_mm2": _estimate(2.0)})

    report = check_conformance(declared, reference)

    assert report.per_metric == {}
    assert report.ok is False


def test_to_dict_is_json_safe():
    import json

    declared = _result("zigzag@1", {"latency_cycles": _estimate(150.0, ci_low=100.0, ci_high=200.0)})
    reference = _result("rtl@1", {"latency_cycles": _estimate(180.0)})
    report = check_conformance(declared, reference)

    d = report.to_dict()
    json.dumps(d)  # raises if a Method enum / dataclass leaked through
    assert d["ok"] is True
    assert d["per_metric"]["latency_cycles"]["reference_value"] == 180.0
    assert d["declared_result"]["provenance"]["evaluator"] == "zigzag@1"
    assert d["reference_result"]["provenance"]["evaluator"] == "rtl@1"
