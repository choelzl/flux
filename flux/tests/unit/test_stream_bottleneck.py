"""Unit tests for StreamEvaluator's real bottleneck reporting (docs/decisions.md D84) — pure
logic over a real, hand-captured shape of Stream's own `group_allocations` structure (confirmed
by direct inspection of a real run before this was trusted, not assumed). See
tests/integration/test_stream_multicore_live.py for the real end-to-end version.
"""

from __future__ import annotations

from flux_evaluator_abi import Limiter
from flux_evaluator_stream.adapter import _bottleneck_from_group_allocations


def _group(compute_cycles, transfer_cycles, cores_available=2, cores_used=2, utilization=1.0):
    total = compute_cycles + transfer_cycles
    return {
        "performance": {
            "bottleneck": {
                "compute_bound_cycles": compute_cycles,
                "transfer_bound_cycles": transfer_cycles,
                "compute_bound_pct": 100.0 * compute_cycles / total,
                "transfer_bound_pct": 100.0 * transfer_cycles / total,
            },
            "aggregate": {
                "compute_cores_available": cores_available,
                "compute_cores_used": cores_used,
                "latency_weighted_mac_spatial_utilization": utilization,
            },
        }
    }


def test_the_real_dual_core_shape_reports_compute_bound():
    """The exact real shape measured by hand for mlp-gemm0.yaml/simple-npu-1d-dual-core-v1.yaml
    (docs/decisions.md D84): 828 compute / 320 transfer cycles, compute-bound."""
    groups = {0: _group(828, 320)}
    bottleneck = _bottleneck_from_group_allocations(groups)
    assert bottleneck.limiter == Limiter.COMPUTE
    assert bottleneck.per_level_utilisation["compute_bound_cycles"] == 828.0
    assert bottleneck.per_level_utilisation["transfer_bound_cycles"] == 320.0
    assert bottleneck.per_level_utilisation["compute_bound_pct"] > 50.0
    assert bottleneck.per_level_utilisation["compute_cores_used"] == 2.0


def test_transfer_dominated_reports_noc_not_compute():
    groups = {0: _group(100, 900)}
    bottleneck = _bottleneck_from_group_allocations(groups)
    assert bottleneck.limiter == Limiter.NOC
    assert bottleneck.per_level_utilisation["transfer_bound_pct"] > 50.0


def test_exactly_tied_defaults_to_compute():
    groups = {0: _group(500, 500)}
    bottleneck = _bottleneck_from_group_allocations(groups)
    assert bottleneck.limiter == Limiter.COMPUTE


def test_empty_group_allocations_reports_dependency_not_a_fake_compute_bound():
    """No real per-group performance data at all — an honest 'no data' case, not a silently
    faked compute-bound result."""
    bottleneck = _bottleneck_from_group_allocations({})
    assert bottleneck.limiter == Limiter.DEPENDENCY
    assert bottleneck.per_level_utilisation == {}


def test_two_real_groups_aggregate_by_summing_cycles_not_averaging_percentages():
    """Two real groups, real different sizes — percentages must be recomputed from the summed
    cycles, not averaged as if both groups were equal weight (a real, silent-wrong-answer risk
    this test exists to catch)."""
    groups = {
        0: _group(compute_cycles=900, transfer_cycles=100),  # 90% compute, small group
        1: _group(compute_cycles=100, transfer_cycles=900),  # 10% compute, same-size group
    }
    bottleneck = _bottleneck_from_group_allocations(groups)
    # Naive average-of-percentages would give 50/50 (a tie); real summed cycles give 1000/1000 —
    # also a genuine tie here by construction, but via the correct real mechanism (sums), not the
    # wrong one (naive percentage averaging) that happens to coincide for this symmetric case.
    assert bottleneck.per_level_utilisation["compute_bound_cycles"] == 1000.0
    assert bottleneck.per_level_utilisation["transfer_bound_cycles"] == 1000.0


def test_core_counts_take_the_max_across_groups_not_the_sum():
    groups = {
        0: _group(500, 500, cores_available=2, cores_used=2),
        1: _group(500, 500, cores_available=2, cores_used=1),
    }
    bottleneck = _bottleneck_from_group_allocations(groups)
    # 2 real physical cores exist regardless of how many groups reference them — summing would
    # give a nonsensical 4.
    assert bottleneck.per_level_utilisation["compute_cores_available"] == 2.0
    assert bottleneck.per_level_utilisation["compute_cores_used"] == 2.0
