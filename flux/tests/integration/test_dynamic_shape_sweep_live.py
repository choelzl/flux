"""Real end-to-end dynamic-shape sweep (docs/decisions.md D63): a real, single-token LLM decode-
mode attention QK^T workload (`llm-decode-attn-qk0.yaml`) — T (KV-cache length) is genuinely
dynamic — evaluated at four real sample points through real ZigZag, aggregated by
`flux_workload_dynamism.sweep_dynamic_shape` into one honest `Result`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_evaluator_zigzag import adapter as zigzag_adapter
from flux_store import ResultStore
from flux_workload_dynamism import resolve_dynamic_bound, sweep_dynamic_shape

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/llm-decode-attn-qk0.yaml"
ARCH = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"

# Real, independently-verified numbers (each obtained via a real, separate ZigZag call before
# this test was written) — the sweep's own aggregate is checked against these directly below,
# not trusted as an opaque total.
_REAL_LATENCY_BY_T = {1: 31.0, 8: 200.0, 32: 782.0, 128: 3110.0}


@pytest.fixture(scope="module")
def workload() -> dict:
    return flux_ir.load_document(WORKLOAD)


@pytest.fixture(scope="module")
def arch() -> dict:
    return flux_ir.load_document(ARCH)


@pytest.fixture(scope="module")
def evaluator() -> ZigZagEvaluator:
    return ZigZagEvaluator()


def test_the_workload_itself_is_not_directly_expressible():
    """The whole point: T is a real {dyn: [...]} bound, not a static int — ZigZag's own
    translator must reject it outright, confirming this workload genuinely needs the sweep, not
    just a convenience wrapper around something already evaluable."""
    from flux_evaluator_zigzag.errors import NotExpressibleError

    doc = flux_ir.load_document(WORKLOAD)
    arch_doc = flux_ir.load_document(ARCH)
    with pytest.raises(NotExpressibleError):
        ZigZagEvaluator().evaluate(
            Candidate(workload=doc, arch=arch_doc, mapping=None), Budget(), frozenset({"latency_cycles"})
        )


def test_each_real_sample_point_matches_the_independently_verified_number(workload, arch, evaluator):
    """Re-derive each pinned number directly via resolve_dynamic_bound + a real ZigZag call —
    the actual proof the pinned numbers above are real, not copied from nowhere."""
    for t_value, expected_latency in _REAL_LATENCY_BY_T.items():
        resolved = resolve_dynamic_bound(workload, "attn.qk", "T", t_value)
        result = evaluator.evaluate(
            Candidate(workload=resolved, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
        )
        assert result.metrics["latency_cycles"].value == pytest.approx(expected_latency)


def test_sweep_aggregates_across_all_real_sample_points(workload, arch, evaluator):
    sample_points = sorted(_REAL_LATENCY_BY_T)
    result = sweep_dynamic_shape(
        workload, "attn.qk", "T", sample_points, evaluator,
        arch=arch, mapping=None, metric="latency_cycles",
    )
    est = result.metrics["latency_cycles"]
    real_values = [_REAL_LATENCY_BY_T[t] for t in sample_points]
    assert est.value == pytest.approx(sum(real_values) / len(real_values))
    assert est.ci_low == pytest.approx(min(real_values))
    assert est.ci_high == pytest.approx(max(real_values))
    assert result.validity.ok is True
    assert result.provenance.inputs["sample_points"] == sample_points


def test_chia_node_wraps_the_same_real_sweep(workload, arch):
    """`flux_sweep_dynamic_shape` (docs/decisions.md D63) — the CHIA node surface — must be a
    transparent wrapper around `sweep_dynamic_shape`, not a reimplementation: same real
    ZigZag-backed numbers, called through `flux_cli.registry.make_evaluator("zigzag")` instead of
    a directly-constructed `ZigZagEvaluator()`.
    """
    from flux_chia_nodes import flux_sweep_dynamic_shape

    sample_points = sorted(_REAL_LATENCY_BY_T)
    result = flux_sweep_dynamic_shape(
        "zigzag", workload, "attn.qk", "T", sample_points, arch=arch, metric="latency_cycles",
    )
    real_values = [_REAL_LATENCY_BY_T[t] for t in sample_points]
    assert result.metrics["latency_cycles"].value == pytest.approx(sum(real_values) / len(real_values))


def test_sweep_also_aggregates_energy_pj_even_though_only_latency_was_the_named_metric(workload, arch, evaluator):
    """ZigZag always reports both latency_cycles and energy_pj regardless of what was requested
    (a real, checked adapter behavior established earlier in this repo's history) — the sweep's
    own aggregation must cover every metric present in every sample, not just the one named by
    `metric`."""
    result = sweep_dynamic_shape(
        workload, "attn.qk", "T", [1, 8], evaluator, arch=arch, mapping=None, metric="latency_cycles",
    )
    assert "energy_pj" in result.metrics
    assert result.metrics["energy_pj"].value > 0


def test_real_result_db_path_skips_a_real_zigzag_rerun_for_a_repeated_sample_point(
    workload, arch, tmp_path, monkeypatch,
):
    """Real, dependency-tracked re-evaluation for `flux_sweep_dynamic_shape` (docs/decisions.md
    D86, generalizing D79's own CACTI case) — counted directly against real ZigZag's own entry
    point, not inferred from wall-clock time, the same discipline
    tests/integration/test_chia_flux_characterize_memory_level_live.py established for CACTI.
    """
    from flux_chia_nodes import flux_sweep_dynamic_shape

    real_get_hw_perf = zigzag_adapter.get_hardware_performance_zigzag
    calls: list[int] = []

    def _counting_get_hw_perf(*args, **kwargs):
        calls.append(1)
        return real_get_hw_perf(*args, **kwargs)

    monkeypatch.setattr(zigzag_adapter, "get_hardware_performance_zigzag", _counting_get_hw_perf)

    with ResultStore(tmp_path / "flux.db") as store:
        r1 = flux_sweep_dynamic_shape(
            "zigzag", workload, "attn.qk", "T", [1, 8], arch=arch, metric="latency_cycles",
            result_db_path=str(store.db_path),
        )
        r2 = flux_sweep_dynamic_shape(
            "zigzag", workload, "attn.qk", "T", [1, 8, 1], arch=arch, metric="latency_cycles",
            result_db_path=str(store.db_path),
        )

    assert len(calls) == 2  # T=1 and T=8, each real exactly once across both calls
    assert r2.metrics["latency_cycles"].ci_low == pytest.approx(r1.metrics["latency_cycles"].ci_low)


def test_n_samples_draws_real_quantile_points_from_the_real_ingested_distribution(workload, arch):
    """Real, distribution-aware sweeping (docs/decisions.md D87, closing docs/gap-analysis.md
    G5's own last-named open piece): `workload`'s own `dynamism.distributions.T` names
    `"empirical@corpus/kv-cache-len-v1"` — a real, measured ShareGPT conversation-length
    distribution (see `knowledge/corpus/distributions/kv-cache-len-v1/PROVENANCE.md`) — no
    caller-hand-picked `sample_points` needed at all.
    """
    from flux_chia_nodes import flux_sweep_dynamic_shape
    from flux_workload_dynamism import dynamic_bound_range, load_empirical_distribution, quantile_sample_points

    result = flux_sweep_dynamic_shape("zigzag", workload, "attn.qk", "T", n_samples=5, arch=arch, metric="latency_cycles")

    # Re-derive the exact real sample points independently, the same way this repo always proves
    # a pinned/aggregate number is real rather than trusting it as an opaque total.
    lo, hi = dynamic_bound_range(workload, "attn.qk", "T")
    dist = load_empirical_distribution("empirical@corpus/kv-cache-len-v1")
    expected_points = quantile_sample_points(dist, 5, lo=lo, hi=hi)

    assert result.provenance.inputs["sample_points"] == expected_points
    assert len(set(expected_points)) >= 3  # real, varied quantiles, not everything collapsed by clipping
    assert result.validity.ok is True


def test_n_samples_without_a_real_distribution_reference_raises(workload, arch):
    from flux_chia_nodes import flux_sweep_dynamic_shape
    from flux_workload_dynamism import DynamicShapeError

    stripped = {**workload, "dynamism": {"symbolic_dims": ["T"], "distributions": {}}}
    with pytest.raises(DynamicShapeError, match="declares no dynamism.distributions"):
        flux_sweep_dynamic_shape("zigzag", stripped, "attn.qk", "T", n_samples=5, arch=arch)


def test_neither_sample_points_nor_n_samples_raises(workload, arch):
    from flux_chia_nodes import flux_sweep_dynamic_shape
    from flux_workload_dynamism import DynamicShapeError

    with pytest.raises(DynamicShapeError, match="requires either sample_points or n_samples"):
        flux_sweep_dynamic_shape("zigzag", workload, "attn.qk", "T", arch=arch)
