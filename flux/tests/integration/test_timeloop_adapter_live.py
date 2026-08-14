"""Runs a real Timeloop+Accelergy end to end through the Flux Evaluator ABI.

Either runner will do (docs/decisions.md D206): a `docker` daemon, which pulls
timeloopaccelergy/accelergy-timeloop-infrastructure on first run, or the hermetic build from
`nix develop .#timeloop` with `FLUX_TIMELOOP_LOCAL=1`. The pinned numbers below hold on both —
measured, not assumed. Slow either way (a real LOMA-equivalent mapper search) — integration, not
unit.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_timeloop import NotExpressibleError, TimeloopEvaluator

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"


@pytest.fixture(scope="module")
def evaluator() -> TimeloopEvaluator:
    return TimeloopEvaluator()


def test_gemm_workload_evaluates_through_real_timeloop(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch=None, mapping=None)

    result = evaluator.evaluate(
        candidate, Budget(), frozenset({"latency_cycles", "energy_pj", "area_mm2"})
    )

    # Real numbers for this exact (workload, vendored reference/ bundle) pair — pinned so a
    # future Docker image update or an accidental adapter/reference change is caught.
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert result.metrics["energy_pj"].value == pytest.approx(100000.0, rel=1e-6)
    for estimate in result.metrics.values():
        assert estimate.method == Method.ANALYTIC
        assert estimate.ci_low == estimate.ci_high == estimate.value  # v0.1: point estimate only

    # Either runner is legitimate (docs/decisions.md D206) — this test's subject is the numbers,
    # not which tool produced them. What must still hold is that provenance names one of the two
    # by prefix, so a replay can tell them apart; a bare "timeloop" would fail this.
    assert result.provenance.evaluator.startswith(("timeloop-docker@", "timeloop-nix@"))
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)
    assert result.bottleneck.per_level_utilisation["pe_array"] == pytest.approx(1.0)


def test_non_einsum_workload_raises_not_expressible_before_touching_docker(evaluator):
    dma_workload = flux_ir.load_document(FLUX_ROOT / "core/ir/workload/examples/soc-dma-desc-fetch.yaml")
    candidate = Candidate(workload=dma_workload, arch=None, mapping=None)

    with pytest.raises(NotExpressibleError):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


def test_unrecognised_string_arch_is_rejected_rather_than_silently_ignored(evaluator):
    """A dict Candidate.arch is now legitimately routed through architecture_translator.py (see
    tests/integration/test_timeloop_architecture_translation_live.py) — this covers the
    remaining case, an arch reference that's neither None nor a translatable dict."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch="/some/other/accelerator.yaml", mapping=None)

    with pytest.raises(NotExpressibleError, match="only accepts Candidate.arch"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


FFN_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-ffn0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"


def test_multi_op_workload_aggregates_across_real_separate_timeloop_runs(evaluator):
    """docs/decisions.md D62: real support for a multi-op (multi-layer) workload — one real,
    separate Timeloop Docker invocation per einsum op, aggregated. Pinned against each layer's
    own independently-verified real number (via the pre-existing single-op path, run separately
    below) rather than trusted as an opaque total: 256.0 + 256.0 = 512.0 cycles,
    320000.0 + 310000.0 = 630000.0 pJ — an exact match, confirming the two real Docker runs are
    genuinely distinct (not a duplicate-invocation bug) and the aggregation arithmetic is real.
    """
    workload = flux_ir.load_document(FFN_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    result = evaluator.evaluate(
        candidate, Budget(), frozenset({"latency_cycles", "energy_pj", "area_mm2"})
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert result.metrics["energy_pj"].value == pytest.approx(630000.0, rel=1e-6)
    assert result.metrics["area_mm2"].value == pytest.approx(0.0)

    # Independently re-derive the same total from each layer's own real, separately-run result —
    # the actual proof this is real aggregation, not an assumption about the pinned number alone.
    total_cycles = 0.0
    total_energy = 0.0
    for op in workload["ops"]:
        single_workload = dict(workload)
        single_workload["ops"] = [op]
        single_result = evaluator.evaluate(
            Candidate(workload=single_workload, arch=arch, mapping=None),
            Budget(), frozenset({"latency_cycles", "energy_pj"}),
        )
        total_cycles += single_result.metrics["latency_cycles"].value
        total_energy += single_result.metrics["energy_pj"].value
    assert total_cycles == pytest.approx(result.metrics["latency_cycles"].value)
    assert total_energy == pytest.approx(result.metrics["energy_pj"].value)


def test_multi_op_workload_with_explicit_mapping_is_rejected(evaluator):
    """docs/decisions.md D62: Mapping IR is inherently per-op (`for_op: <id>`) — an explicit
    Candidate.mapping alongside a multi-op workload is genuinely ambiguous (which op does it
    apply to?) and must be rejected before ever touching Docker, not silently applied to the
    first op or ignored."""
    workload = flux_ir.load_document(FFN_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    mapping = flux_ir.load_document(FLUX_ROOT / "core/ir/mapping/examples/mlp-gemm0-simple-npu-1d-map0.yaml")
    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)

    with pytest.raises(NotExpressibleError, match="ambiguous"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
