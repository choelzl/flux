"""Unit tests for flux_workload_dynamism.distributions (docs/decisions.md D87): pure resolution/
quantile-sampling logic against a small, synthetic on-disk distribution file — no real ShareGPT
download needed. See tests/integration/test_dynamic_shape_sweep_live.py for the real, ingested
`kv-cache-len-v1` version.
"""

from __future__ import annotations

import json

import pytest
from flux_workload_dynamism import (
    DistributionResolutionError,
    load_empirical_distribution,
    parse_distribution_ref,
    quantile_sample_points,
)


def test_parse_distribution_ref_splits_scheme_and_name():
    assert parse_distribution_ref("empirical@corpus/kv-cache-len-v1") == ("empirical", "kv-cache-len-v1")
    assert parse_distribution_ref("measured@corpus/moe-route-v1") == ("measured", "moe-route-v1")


def test_parse_distribution_ref_rejects_an_unknown_shape():
    with pytest.raises(DistributionResolutionError, match="doesn't match"):
        parse_distribution_ref("not-a-real-ref")


@pytest.fixture
def synthetic_corpus_root(tmp_path):
    """A small, synthetic real-shaped distribution: percentiles 0..100 are just 0..100 themselves
    (a real uniform[0,100] distribution) — chosen so quantile math is trivially checkable by hand.
    """
    d = tmp_path / "uniform-0-100"
    d.mkdir()
    (d / "data.json").write_text(json.dumps({
        "summary": {"n_observations": 101, "min": 0, "max": 100, "mean": 50.0, "median": 50, "stdev": 29.0},
        "percentiles": {str(p): p for p in range(101)},
    }))
    return tmp_path


def test_load_empirical_distribution_reads_real_percentiles(synthetic_corpus_root):
    dist = load_empirical_distribution("empirical@corpus/uniform-0-100", corpus_root=synthetic_corpus_root)
    assert dist.name == "uniform-0-100"
    assert dist.percentiles[50] == 50
    assert dist.percentiles[0] == 0
    assert dist.percentiles[100] == 100
    assert dist.summary["n_observations"] == 101


def test_load_empirical_distribution_raises_on_unknown_name(synthetic_corpus_root):
    with pytest.raises(DistributionResolutionError, match="isn't real, ingested data"):
        load_empirical_distribution("empirical@corpus/not-ingested", corpus_root=synthetic_corpus_root)


def test_quantile_sample_points_reads_the_midpoint_of_each_equal_bucket(synthetic_corpus_root):
    dist = load_empirical_distribution("empirical@corpus/uniform-0-100", corpus_root=synthetic_corpus_root)
    # n=4 -> buckets centered at p12.5, p37.5, p62.5, p87.5 -> round to 12/38/62/88 (banker's
    # rounding on the .5 cases) -> for this uniform[0,100] distribution, percentile==value.
    points = quantile_sample_points(dist, 4)
    assert points == [12, 38, 62, 88]


def test_quantile_sample_points_n1_reads_the_real_median(synthetic_corpus_root):
    dist = load_empirical_distribution("empirical@corpus/uniform-0-100", corpus_root=synthetic_corpus_root)
    assert quantile_sample_points(dist, 1) == [50]


def test_quantile_sample_points_clips_to_lo_hi(synthetic_corpus_root):
    dist = load_empirical_distribution("empirical@corpus/uniform-0-100", corpus_root=synthetic_corpus_root)
    points = quantile_sample_points(dist, 4, lo=20, hi=70)
    assert points == [20, 38, 62, 70]  # 12 clipped up to 20, 88 clipped down to 70


def test_quantile_sample_points_rejects_n_below_1(synthetic_corpus_root):
    dist = load_empirical_distribution("empirical@corpus/uniform-0-100", corpus_root=synthetic_corpus_root)
    with pytest.raises(ValueError, match="n=0"):
        quantile_sample_points(dist, 0)


def test_real_ingested_kv_cache_len_v1_loads_and_has_a_real_right_skew():
    """Real, ingested data — no corpus_root override, the actual repo path
    (docs/decisions.md D87, knowledge/corpus/distributions/kv-cache-len-v1/)."""
    dist = load_empirical_distribution("empirical@corpus/kv-cache-len-v1")
    assert dist.summary["n_observations"] > 60_000  # real, not a toy handful of points
    # Real, heavily right-skewed conversation-length data: median well below mean.
    assert dist.summary["median"] < dist.summary["mean"]
    assert dist.percentiles[50] == dist.summary["median"]
    points = quantile_sample_points(dist, 5, lo=1, hi=4096)
    assert points == sorted(points)  # percentiles are monotonic, so must the sample points be
    assert all(1 <= p <= 4096 for p in points)


# --- Review-driven fixes (docs/decisions.md D96) ---


def test_non_empirical_scheme_is_refused_not_silently_resolved():
    """The scheme was previously parsed and discarded — `garbage@corpus/kv-cache-len-v1`
    resolved the real empirical table as if the scheme meant nothing (review finding)."""
    with pytest.raises(DistributionResolutionError, match="scheme 'garbage'"):
        load_empirical_distribution("garbage@corpus/kv-cache-len-v1")
    with pytest.raises(DistributionResolutionError, match="scheme 'measured'"):
        load_empirical_distribution("measured@corpus/kv-cache-len-v1")


def test_sparse_percentile_table_raises_typed_error_not_bare_keyerror():
    """kv-cache-len-v1 ships dense 0..100, but nothing guarantees a future ingestion does — a
    sparse table previously surfaced as a bare KeyError mid-sampling (review finding)."""
    from flux_workload_dynamism.distributions import EmpiricalDistribution

    sparse = EmpiricalDistribution(
        name="sparse-test", summary={}, percentiles={0: 1, 50: 10, 100: 100},  # 5%-granularity-style gaps
    )
    with pytest.raises(DistributionResolutionError, match="no percentile"):
        quantile_sample_points(sparse, 4)
