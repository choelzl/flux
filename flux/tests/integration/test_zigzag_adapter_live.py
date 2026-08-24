"""Runs the real, installed zigzag-dse package end to end through the Flux Evaluator ABI.
Separate from tests/unit because this actually invokes ZigZag's LOMA search — seconds, not
milliseconds, and it exercises real third-party code rather than only our own logic.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_zigzag import NotExpressibleError, ZigZagEvaluator

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

    # These are real ZigZag numbers for this exact (workload, tpu_like, default mapping)
    # triple — pinned so a future ZigZag upgrade or an accidental change to the example/adapter
    # is caught, not just "did it run without crashing".
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
    """A schema-valid mapping that spatially splits `B` (bound 4) fully onto the array (size 4),
    leaving a size-1 temporal loop for `B`, crashes zigzag-dse==3.8.5 itself with a raw
    `RuntimeError: dictionary changed size during iteration` (a real bug in its
    `LayerTemporalOrdering.is_complete()` — see adapter.py's handler for the full explanation).
    Found by search/exhaustive/'s exhaustive sweep, not hand-guessed — see
    tests/integration/test_search_exhaustive_live.py for that context. This is the adapter-level
    regression test: the crash must surface as `NotExpressibleError`, not an unexplained
    third-party `RuntimeError`.
    """
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
    """A dict Candidate.arch is now legitimately routed through architecture_translator.py (see
    tests/integration/test_zigzag_architecture_translation_live.py) — this covers the remaining
    case, an arch reference that's neither None, this instance's bound path, nor a translatable
    dict."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch="/some/other/accelerator.yaml", mapping=None)

    with pytest.raises(NotExpressibleError, match="only accepts Candidate.arch"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
