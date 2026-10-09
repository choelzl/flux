"""The installed zigzag-dse package end to end through the Flux Evaluator ABI (runs ZigZag's
LOMA search: seconds, not milliseconds)."""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from zigzag_tools import NotExpressibleError, ZigZagEvaluator

# ZigZag logs verbosely at INFO by default; keep test output readable.
logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"


@pytest.fixture(scope="module")
def evaluator(tmp_path_factory) -> ZigZagEvaluator:
    dump_folder = tmp_path_factory.mktemp("zigzag-dump")
    return ZigZagEvaluator(dump_folder=str(dump_folder))


def test_gemm_workload_evaluates_through_real_zigzag(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch=None, mapping=None)

    result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles", "energy_pj"}))

    # ZigZag's numbers for (workload, tpu_like, default mapping), pinned to catch an upgrade or
    # an adapter change.
    assert result.metrics["latency_cycles"].value == pytest.approx(145.0)
    assert result.metrics["energy_pj"].value == pytest.approx(113416.448, rel=1e-6)
    for estimate in result.metrics.values():
        assert estimate.method == Method.ANALYTIC
        assert estimate.ci_low == estimate.ci_high == estimate.value  # v0.1: point estimate only

    assert result.provenance.evaluator.startswith("zigzag@")
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)
    assert result.validity.ok is True


def test_evaluate_batch_runs_the_same_workload_twice_consistently(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidates = [Candidate(workload=workload, arch=None, mapping=None) for _ in range(2)]

    results = evaluator.evaluate_batch(candidates, Budget(), frozenset({"latency_cycles"}))

    assert len(results) == 2
    assert results[0].metrics["latency_cycles"].value == results[1].metrics["latency_cycles"].value


def test_non_einsum_workload_raises_not_expressible_before_touching_zigzag(evaluator):
    dma_workload = flux_ir.load_document(FLUX_ROOT / "core/ir/workload/examples/soc-dma-desc-fetch.yaml")
    candidate = Candidate(workload=dma_workload, arch=None, mapping=None)

    with pytest.raises(NotExpressibleError):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


def test_size_one_temporal_loop_is_reported_as_not_expressible_not_a_raw_crash(evaluator):
    """A mapping that fully unrolls `B` onto the array crashes zigzag-dse 3.8.5 with a RuntimeError
    (see adapter.py's handler); the adapter surfaces it as `NotExpressibleError`."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml")
    mapping = {
        "schema_version": "0.1.0",
        "id": "test/size-one-temporal-loop",
        "for_op": "mlp.gemm0",
        "operands": {
            name: [
                {
                    "level": "gbuf",
                    "loops": [
                        {"dim": "B", "size": 1, "order": 0},
                        {"dim": "C", "size": 32, "order": 1},
                        {"dim": "K", "size": 32, "order": 2},
                    ],
                }
            ]
            for name in ("I", "W", "O")
        },
        "spatial": [{"dim": "B", "array_dim": "X", "size": 4}],
    }
    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)

    with pytest.raises(NotExpressibleError, match="temporal loop has size 1"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


def test_unrecognised_string_arch_is_rejected_rather_than_silently_ignored(evaluator):
    """An arch that is neither None, this instance's bound path, nor a translatable dict is refused."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch="/some/other/accelerator.yaml", mapping=None)

    with pytest.raises(NotExpressibleError, match="only accepts Candidate.arch"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
