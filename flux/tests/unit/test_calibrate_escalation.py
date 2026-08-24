"""Unit tests for actionable escalation (docs/decisions.md D99):
`flux_calibrate(escalate_if_recommended=True)` — the escalation advisory becomes a real,
budget-disciplined action: buy one reference measurement only when the policy recommends it,
feed the D98 flywheel, re-calibrate. Stub evaluators throughout (this is orchestration/budget
logic); the real backends stay covered by the live calibration/conformance integration tests.
"""

from __future__ import annotations

import pytest
from flux_chia_nodes import flux_calibrate
import flux_chia_nodes.calibrate as calibrate_module
from flux_evaluator_abi import (
    Bottleneck,
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)

_WORKLOAD = {"schema_version": "0.1.0", "id": "test/w", "ops": []}
_ARCH = {"schema_version": "0.1.0", "id": "test/a", "hierarchy": []}


def _result(evaluator: str, value: float) -> Result:
    return Result(
        metrics={"latency_cycles": Estimate(value=value, ci_low=value, ci_high=value, unit="cycles", method=Method.ANALYTIC)},
        validity=Validity(ok=True, checker_version="test"),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


class _StubEvaluator:
    def __init__(self, evaluator_name: str, value: float, *, not_expressible: bool = False) -> None:
        self.evaluator_name = evaluator_name
        self.value = value
        self.not_expressible = not_expressible
        self.calls = 0

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        self.calls += 1
        if self.not_expressible:
            raise ValueError("stub NotExpressibleError")
        return _result(self.evaluator_name, self.value)


@pytest.fixture
def stubs(monkeypatch):
    declared = _StubEvaluator("stub-declared@1", 110.0)
    reference = _StubEvaluator("stub-reference@1", 100.0)

    def _make_evaluator(backend: str):
        return declared if backend == "stub-declared" else reference

    monkeypatch.setattr(calibrate_module, "make_evaluator", _make_evaluator)
    return declared, reference


def _db(tmp_path) -> str:
    return str(tmp_path / "cal.db")


def test_default_never_touches_the_reference_backend(stubs, tmp_path):
    declared, reference = stubs
    result = flux_calibrate("stub-declared", _WORKLOAD, _ARCH, calibration_db_path=_db(tmp_path))
    assert reference.calls == 0
    # Empty store: bare point estimate, out of domain — advisory only.
    assert result.escalation.recommended is True
    assert result.domain.in_domain is False


def test_escalation_buys_exactly_one_reference_measurement_and_recalibrates(stubs, tmp_path):
    declared, reference = stubs
    db = _db(tmp_path)
    result = flux_calibrate(
        "stub-declared", _WORKLOAD, _ARCH, calibration_db_path=db,
        escalate_if_recommended=True, reference_backend="stub-reference",
    )
    assert reference.calls == 1
    est = result.metrics["latency_cycles"]
    # The re-calibrated CI reflects the real 10% residual just bought — a genuine interval
    # (not the bare point estimate) that now contains the reference value the model missed.
    assert est.ci_low < 110.0 < est.ci_high
    assert est.ci_low <= 100.0
    assert result.domain.distance == 0.0  # exact calibration match, recorded this call


def test_budget_already_spent_is_not_spent_again(stubs, tmp_path):
    declared, reference = stubs
    db = _db(tmp_path)
    flux_calibrate(
        "stub-declared", _WORKLOAD, _ARCH, calibration_db_path=db,
        escalate_if_recommended=True, reference_backend="stub-reference",
    )
    assert reference.calls == 1
    # Identical call again: this exact candidate already has its record — even if escalation is
    # still recommended (n=1 < the trusted-n threshold), the reference is NOT re-run.
    second = flux_calibrate(
        "stub-declared", _WORKLOAD, _ARCH, calibration_db_path=db,
        escalate_if_recommended=True, reference_backend="stub-reference",
    )
    assert reference.calls == 1  # unchanged — the budget was spent once
    assert second.domain.distance == 0.0


def test_not_expressible_reference_returns_calibrated_result_unchanged(stubs, tmp_path, monkeypatch):
    declared, _ = stubs
    not_expressible = _StubEvaluator("stub-reference@1", 0.0, not_expressible=True)
    monkeypatch.setattr(
        calibrate_module, "make_evaluator",
        lambda backend: declared if backend == "stub-declared" else not_expressible,
    )
    result = flux_calibrate(
        "stub-declared", _WORKLOAD, _ARCH, calibration_db_path=_db(tmp_path),
        escalate_if_recommended=True, reference_backend="stub-reference",
    )
    assert not_expressible.calls == 1  # the buy was attempted...
    # ...but honestly couldn't complete: bare estimate, still out of domain, no crash.
    est = result.metrics["latency_cycles"]
    assert (est.ci_low, est.ci_high) == (110.0, 110.0)
    assert result.domain.in_domain is False


# --- The budget gate must latch for real multi-metric backends (docs/decisions.md D111) ---


class _MultiMetricEvaluator:
    """Real backends return more metrics than were requested — ZigZag emits energy_pj alongside
    latency_cycles regardless of the requested set. The reference (RTL) can only ever produce a
    latency residual, so energy never gets a record. D99's original gate used `all(...)` over the
    declared result's metrics and therefore never latched; its own test missed it because the
    stub was single-metric, making `all` and `any` indistinguishable."""

    def __init__(self, evaluator_name: str, values: dict[str, float]) -> None:
        self.evaluator_name = evaluator_name
        self.values = values
        self.calls = 0

    def evaluate(self, candidate, budget, metrics):
        self.calls += 1
        m = {}
        for name, v in self.values.items():
            m[name] = Estimate(value=v, ci_low=v, ci_high=v, unit="u", method=Method.ANALYTIC)
        return Result(
            metrics=m, validity=Validity(ok=True, checker_version="t"),
            domain=Domain(in_domain=True), bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
            provenance=Provenance(evaluator=self.evaluator_name, inputs={}),
            escalation=Escalation(recommended=False),
        )


def test_the_budget_gate_latches_even_when_a_metric_can_never_get_a_residual(tmp_path, monkeypatch):
    declared = _MultiMetricEvaluator("multi@1", {"latency_cycles": 110.0, "energy_pj": 5.0})
    reference = _MultiMetricEvaluator("ref@1", {"latency_cycles": 100.0})  # no energy, ever

    monkeypatch.setattr(
        calibrate_module, "make_evaluator",
        lambda backend: declared if backend == "multi" else reference,
    )
    db = str(tmp_path / "cal.db")
    for _ in range(3):
        flux_calibrate("multi", _WORKLOAD, _ARCH, calibration_db_path=db,
                       escalate_if_recommended=True, reference_backend="ref")

    assert reference.calls == 1, (
        f"expected one real reference measurement across three calls, got {reference.calls} — "
        "the budget gate stopped latching for multi-metric declared results"
    )


# --- Attempts vs measurements (docs/decisions.md D114) ---


def test_the_gate_latches_even_when_the_reference_shares_no_metric_at_all(tmp_path, monkeypatch):
    """The leak D111 could not close: a reference producing NONE of the declared metrics writes
    no residual record, so a measurement-based gate had nothing to latch on and re-ran a real
    simulator forever. The attempts log records the purchase regardless of what it yielded."""
    declared = _MultiMetricEvaluator("multi@1", {"energy_pj": 5.0})
    reference = _MultiMetricEvaluator("ref@1", {"latency_cycles": 100.0})  # disjoint metrics

    monkeypatch.setattr(
        calibrate_module, "make_evaluator",
        lambda backend: declared if backend == "multi" else reference,
    )
    db = str(tmp_path / "cal.db")
    for _ in range(3):
        flux_calibrate("multi", _WORKLOAD, _ARCH, calibration_db_path=db,
                       escalate_if_recommended=True, reference_backend="ref")
    assert reference.calls == 1, f"expected one purchase, got {reference.calls}"


def test_buying_one_reference_does_not_mask_never_having_bought_another(tmp_path, monkeypatch):
    """Second leak: the gate keyed on nothing about the reference, so a first call with `ref_a`
    silently suppressed a later call with `ref_b` whose ground truth was never bought."""
    declared = _MultiMetricEvaluator("multi@1", {"latency_cycles": 110.0})
    ref_a = _MultiMetricEvaluator("ref_a@1", {"latency_cycles": 100.0})
    ref_b = _MultiMetricEvaluator("ref_b@1", {"latency_cycles": 90.0})
    monkeypatch.setattr(
        calibrate_module, "make_evaluator",
        lambda b: {"multi": declared, "ref_a": ref_a, "ref_b": ref_b}[b],
    )
    db = str(tmp_path / "cal.db")
    flux_calibrate("multi", _WORKLOAD, _ARCH, calibration_db_path=db,
                   escalate_if_recommended=True, reference_backend="ref_a")
    flux_calibrate("multi", _WORKLOAD, _ARCH, calibration_db_path=db,
                   escalate_if_recommended=True, reference_backend="ref_b")
    assert ref_a.calls == 1
    assert ref_b.calls == 1, "a different reference must still be bought once"
