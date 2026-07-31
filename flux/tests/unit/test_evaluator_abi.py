"""Evaluator ABI v0.1 types and protocol (docs/04.md §4)."""

from __future__ import annotations

import flux_ir
import pytest
from flux_evaluator_abi import (
    Bottleneck,
    Budget,
    Candidate,
    Domain,
    Escalation,
    Estimate,
    Evaluator,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)


def _sample_result() -> Result:
    return Result(
        metrics={
            "latency_cycles": Estimate(
                value=1000, ci_low=900, ci_high=1100, unit="cycles", method=Method.ANALYTIC
            )
        },
        validity=Validity(ok=True, checker_version="0.1.0"),
        domain=Domain(in_domain=True, distance=0.02, nearest_calibration="cal-2026-07-a"),
        bottleneck=Bottleneck(limiter=Limiter.MEMORY, per_level_utilisation={"gbuf": 0.87}),
        provenance=Provenance(
            evaluator="flux-native@0.1.0", inputs={"workload_hash": "abc", "arch_hash": "def"}
        ),
        escalation=Escalation(recommended=False),
    )


def test_estimate_rejects_value_outside_its_own_confidence_interval():
    with pytest.raises(ValueError):
        Estimate(value=5, ci_low=10, ci_high=20, unit="x", method=Method.ANALYTIC)


def test_estimate_accepts_value_on_ci_boundary():
    Estimate(value=10, ci_low=10, ci_high=20, unit="x", method=Method.ANALYTIC)
    Estimate(value=20, ci_low=10, ci_high=20, unit="x", method=Method.ANALYTIC)


def test_result_to_dict_round_trips_enum_values_as_plain_strings():
    d = _sample_result().to_dict()
    assert d["metrics"]["latency_cycles"]["method"] == "analytic"
    assert d["bottleneck"]["limiter"] == "memory"
    assert d["validity"]["ok"] is True
    assert d["domain"]["in_domain"] is True
    assert d["escalation"]["recommended"] is False


class ReferenceEvaluator:
    """Minimal stand-in for a real backend adapter (ZigZag, Timeloop, ...) — enough to prove the
    ABI + IR compose end to end. Not a cost model; always returns a fixed Result, tagged with the
    real content hashes of whatever candidate it was given.
    """

    def evaluate(self, candidate: Candidate, budget: Budget, metrics: frozenset[str]) -> Result:
        workload_hash = (
            candidate.workload
            if isinstance(candidate.workload, str)
            else flux_ir.content_hash(candidate.workload)
        )
        arch_hash = (
            candidate.arch if isinstance(candidate.arch, str) else flux_ir.content_hash(candidate.arch)
        )
        result = _sample_result()
        return Result(
            metrics=result.metrics,
            validity=result.validity,
            domain=result.domain,
            bottleneck=result.bottleneck,
            provenance=Provenance(
                evaluator="reference@0.1.0",
                inputs={"workload_hash": workload_hash, "arch_hash": arch_hash},
            ),
            escalation=result.escalation,
        )

    def evaluate_batch(
        self, candidates: list[Candidate], budget: Budget, metrics: frozenset[str]
    ) -> list[Result]:
        return [self.evaluate(c, budget, metrics) for c in candidates]


def test_reference_evaluator_satisfies_the_evaluator_protocol():
    assert isinstance(ReferenceEvaluator(), Evaluator)


def test_reference_evaluator_tags_provenance_with_real_ir_content_hashes(ir_example):
    kind, path = ir_example
    if kind != "workload":
        pytest.skip("one workload example is enough to exercise the evaluator")
    workload = flux_ir.load_document(path)
    arch = flux_ir.load_document(
        path.parents[2] / "architecture/examples/my-npu-v3.yaml"
    )
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    result = ReferenceEvaluator().evaluate(candidate, Budget(), frozenset({"latency_cycles"}))

    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)
    assert result.provenance.inputs["arch_hash"] == flux_ir.content_hash(arch)


def test_evaluate_batch_matches_sequential_evaluate(ir_example):
    kind, path = ir_example
    if kind != "workload":
        pytest.skip("one workload example is enough to exercise the evaluator")
    workload = flux_ir.load_document(path)
    candidates = [Candidate(workload=workload, arch={"id": f"arch{i}"}) for i in range(3)]

    evaluator = ReferenceEvaluator()
    batch_results = evaluator.evaluate_batch(candidates, Budget(), frozenset({"latency_cycles"}))
    sequential_results = [evaluator.evaluate(c, Budget(), frozenset({"latency_cycles"})) for c in candidates]

    assert [r.to_dict() for r in batch_results] == [r.to_dict() for r in sequential_results]
