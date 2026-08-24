"""Real end-to-end MoE routing sweep (docs/decisions.md D68): a real 8-expert MoE FFN block
(`moe-ffn-8experts-top2-v1.yaml`) — routing (which 2 of 8 experts actually run) is genuinely
data-dependent — evaluated at three real routing samples through real ZigZag,
aggregated by `flux_workload_dynamism.sweep_moe_routing` into one honest `Result`.
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
from flux_workload_dynamism import resolve_moe_routing, sweep_moe_routing

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/moe-ffn-8experts-top2-v1.yaml"
ARCH = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"

# Real, independently-verified numbers (each obtained via a real, separate ZigZag call before
# this test was written) — the sweep's own aggregate is checked against these directly below,
# not trusted as an opaque total.
_REAL_LATENCY_BY_ROUTING = {
    ("expert0.ffn", "expert1.ffn"): 494.0,
    ("expert6.ffn", "expert7.ffn"): 1649.0,
    ("expert0.ffn", "expert7.ffn"): 1072.0,
}
_ALL_EIGHT_EXPERTS_DENSE_LATENCY = 4286.0


@pytest.fixture(scope="module")
def workload() -> dict:
    return flux_ir.load_document(WORKLOAD)


@pytest.fixture(scope="module")
def arch() -> dict:
    return flux_ir.load_document(ARCH)


@pytest.fixture(scope="module")
def evaluator() -> ZigZagEvaluator:
    return ZigZagEvaluator()


def test_the_raw_unresolved_workload_silently_evaluates_every_candidate_expert(workload, arch, evaluator):
    """The whole real danger this decision closes: ZigZag's own `workload_to_zigzag_layers`
    silently *skips* the data_dependent op rather than rejecting the workload outright — so an
    unresolved MoE workload doesn't fail loudly, it silently evaluates as if all 8 experts ran,
    wildly overstating real per-token cost. Confirmed directly, not assumed."""
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(_ALL_EIGHT_EXPERTS_DENSE_LATENCY)


def test_each_real_routing_sample_matches_the_independently_verified_number(workload, arch, evaluator):
    """Re-derive each pinned number directly via resolve_moe_routing + a real ZigZag call — the
    actual proof the pinned numbers above are real, not copied from nowhere."""
    for selected, expected_latency in _REAL_LATENCY_BY_ROUTING.items():
        resolved = resolve_moe_routing(workload, "moe.route", list(selected))
        result = evaluator.evaluate(
            Candidate(workload=resolved, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
        )
        assert result.metrics["latency_cycles"].value == pytest.approx(expected_latency)


def test_every_resolved_sample_is_genuinely_cheaper_than_the_dense_all_experts_baseline(workload, arch, evaluator):
    """The real point of sparse MoE routing, quantified: computing only 2 of 8 real experts costs
    substantially less than computing all 8 densely — checked for every real sample, not assumed
    from one favorable case."""
    for selected in _REAL_LATENCY_BY_ROUTING:
        resolved = resolve_moe_routing(workload, "moe.route", list(selected))
        result = evaluator.evaluate(
            Candidate(workload=resolved, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
        )
        assert result.metrics["latency_cycles"].value < _ALL_EIGHT_EXPERTS_DENSE_LATENCY


def test_sweep_aggregates_across_all_real_routing_samples(workload, arch, evaluator):
    samples = [list(k) for k in _REAL_LATENCY_BY_ROUTING]
    result = sweep_moe_routing(
        workload, "moe.route", samples, evaluator,
        arch=arch, mapping=None, metric="latency_cycles",
    )
    est = result.metrics["latency_cycles"]
    real_values = list(_REAL_LATENCY_BY_ROUTING.values())
    assert est.value == pytest.approx(sum(real_values) / len(real_values))
    assert est.ci_low == pytest.approx(min(real_values))
    assert est.ci_high == pytest.approx(max(real_values))
    assert result.validity.ok is True
    assert result.provenance.inputs["routing_samples"] == samples


def test_chia_node_wraps_the_same_real_sweep(workload, arch):
    """`flux_sweep_moe_routing` (docs/decisions.md D68) — the CHIA node surface — must be a
    transparent wrapper around `sweep_moe_routing`, not a reimplementation: same real
    ZigZag-backed numbers, called through `flux_cli.registry.make_evaluator("zigzag")` instead of
    a directly-constructed `ZigZagEvaluator()`.
    """
    from flux_chia_nodes import flux_sweep_moe_routing

    samples = [list(k) for k in _REAL_LATENCY_BY_ROUTING]
    result = flux_sweep_moe_routing(
        "zigzag", workload, "moe.route", samples, arch=arch, metric="latency_cycles",
    )
    real_values = list(_REAL_LATENCY_BY_ROUTING.values())
    assert result.metrics["latency_cycles"].value == pytest.approx(sum(real_values) / len(real_values))


def test_sweep_also_aggregates_energy_pj_even_though_only_latency_was_the_named_metric(workload, arch, evaluator):
    """ZigZag always reports both latency_cycles and energy_pj regardless of what was requested
    (a real, checked adapter behavior established earlier in this repo's history) — the sweep's
    own aggregation must cover every metric present in every sample, not just the one named by
    `metric`."""
    result = sweep_moe_routing(
        workload, "moe.route",
        [["expert0.ffn", "expert1.ffn"], ["expert6.ffn", "expert7.ffn"]],
        evaluator, arch=arch, mapping=None, metric="latency_cycles",
    )
    assert "energy_pj" in result.metrics
    assert result.metrics["energy_pj"].value > 0


def test_real_result_db_path_skips_a_real_zigzag_rerun_for_a_repeated_routing_sample(
    workload, arch, tmp_path, monkeypatch,
):
    """Real, dependency-tracked re-evaluation for `flux_sweep_moe_routing` (docs/decisions.md
    D86, generalizing D79's own CACTI case) — counted directly against real ZigZag's own entry
    point, not inferred from wall-clock time.
    """
    from flux_chia_nodes import flux_sweep_moe_routing

    real_get_hw_perf = zigzag_adapter.get_hardware_performance_zigzag
    calls: list[int] = []

    def _counting_get_hw_perf(*args, **kwargs):
        calls.append(1)
        return real_get_hw_perf(*args, **kwargs)

    monkeypatch.setattr(zigzag_adapter, "get_hardware_performance_zigzag", _counting_get_hw_perf)

    with ResultStore(tmp_path / "flux.db") as store:
        r1 = flux_sweep_moe_routing(
            "zigzag", workload, "moe.route", [["expert0.ffn", "expert1.ffn"]],
            arch=arch, metric="latency_cycles", result_db_path=str(store.db_path),
        )
        r2 = flux_sweep_moe_routing(
            "zigzag", workload, "moe.route",
            [["expert0.ffn", "expert1.ffn"], ["expert1.ffn", "expert0.ffn"]],  # same set, other order
            arch=arch, metric="latency_cycles", result_db_path=str(store.db_path),
        )

    assert len(calls) == 1  # one real routing decision, real ZigZag call, exactly once
    assert r2.metrics["latency_cycles"].ci_low == pytest.approx(r1.metrics["latency_cycles"].ci_low)
    assert r2.metrics["latency_cycles"].ci_low == pytest.approx(494.0)  # the real, pinned number above
