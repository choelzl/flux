"""Unit tests for the fusion-tile search axis (docs/decisions.md D104) — pure candidate
generation and the DSE engine's newly-honored optional `mapping` attribute. No Stream import
here; the real non-monotone sweep is pinned in tests/integration/test_stream_multicore_live.py.
"""

from __future__ import annotations

import pytest
from flux_search_architecture import (
    FusionTileCandidate,
    NotAFusionSweepCandidate,
    divisor_tile_sizes,
    generate_fusion_tile_candidates,
)

_ARCH = {"schema_version": "0.1.0", "id": "test/arch", "hierarchy": []}

_WORKLOAD = {
    "id": "mlp/ffn",
    "ops": [
        {"id": "ffn.down", "kind": "einsum", "expr": "B C, C H -> B H", "bounds": {"B": 8, "C": 32, "H": 16}},
        {"id": "ffn.up", "kind": "einsum", "expr": "B H, H K -> B K", "bounds": {"B": 8, "H": 16, "K": 32}},
    ],
}


def test_default_sweeps_the_complete_feasible_space():
    cands = generate_fusion_tile_candidates(_WORKLOAD, _ARCH)
    assert [c.tile_size for c in cands] == [1, 2, 4, 8]  # every divisor of B=8
    assert all(c.tile_dim == "B" for c in cands)
    assert all(c.op_group == ("ffn.down", "ffn.up") for c in cands)


def test_each_candidate_carries_a_valid_fusion_only_mapping():
    cands = generate_fusion_tile_candidates(_WORKLOAD, _ARCH, tile_sizes=[2])
    m = cands[0].mapping
    assert m["fusion"] == {"group": ["ffn.down", "ffn.up"], "tile": {"B": 2}}
    assert m["operands"] == {}  # fusion-only; the Stream translator rejects anything else
    assert m["for_op"] == "ffn.down"
    assert "spatial" not in m and "placement" not in m


def test_the_architecture_is_untouched_by_this_axis():
    cands = generate_fusion_tile_candidates(_WORKLOAD, _ARCH, tile_sizes=[1, 8])
    assert all(c.arch == _ARCH for c in cands)
    assert cands[0].arch is not _ARCH  # deep-copied, not aliased


def test_divisor_tile_sizes():
    assert divisor_tile_sizes(16) == [1, 2, 4, 8, 16]
    assert divisor_tile_sizes(1) == [1]


def test_single_op_workload_is_rejected():
    single = {"id": "w", "ops": [_WORKLOAD["ops"][0]]}
    with pytest.raises(NotAFusionSweepCandidate, match="at least two chained ops"):
        generate_fusion_tile_candidates(single, _ARCH)


def test_ops_not_sharing_a_row_dim_are_rejected():
    mismatched = {"id": "w", "ops": [
        _WORKLOAD["ops"][0],
        {"id": "other", "kind": "einsum", "expr": "X H, H K -> X K", "bounds": {"X": 8, "H": 16, "K": 32}},
    ]}
    with pytest.raises(NotAFusionSweepCandidate, match="do not share one row dim"):
        generate_fusion_tile_candidates(mismatched, _ARCH)


def test_dynamic_or_mismatched_bounds_are_rejected():
    dyn = {"id": "w", "ops": [
        {"id": "a", "kind": "einsum", "expr": "B C, C H -> B H", "bounds": {"B": {"dyn": [1, 8]}, "C": 32, "H": 16}},
        {"id": "b", "kind": "einsum", "expr": "B H, H K -> B K", "bounds": {"B": {"dyn": [1, 8]}, "H": 16, "K": 32}},
    ]}
    with pytest.raises(NotAFusionSweepCandidate, match="not a static integer bound"):
        generate_fusion_tile_candidates(dyn, _ARCH)


def test_ops_disagreeing_on_the_shared_bound_are_rejected():
    mismatched = {"id": "w", "ops": [
        {"id": "a", "kind": "einsum", "expr": "B C, C H -> B H", "bounds": {"B": 8, "C": 32, "H": 16}},
        {"id": "b", "kind": "einsum", "expr": "B H, H K -> B K", "bounds": {"B": 4, "H": 16, "K": 32}},
    ]}
    with pytest.raises(NotAFusionSweepCandidate, match="disagree on"):
        generate_fusion_tile_candidates(mismatched, _ARCH)


@pytest.mark.parametrize("bad", [[3], [16], [0], [5, 2]])
def test_non_divisor_tile_sizes_are_rejected(bad):
    with pytest.raises(NotAFusionSweepCandidate, match="do not divide"):
        generate_fusion_tile_candidates(_WORKLOAD, _ARCH, tile_sizes=bad)


def test_the_dse_engine_honors_an_optional_candidate_mapping():
    """D104's engine generalization: `run_architecture_dse` passed `mapping=None` unconditionally,
    so its documented axis-agnosticism held only for architecture axes. A candidate carrying
    `.mapping` must now reach the evaluator with it."""
    from flux_evaluator_abi import (
        Bottleneck, Budget, Domain, Escalation, Estimate, Limiter, Method, Provenance, Result, Validity,
    )
    from flux_search_architecture import run_architecture_dse

    seen: list[object] = []

    class _Recorder:
        def evaluate(self, candidate, budget, metrics):
            seen.append(candidate.mapping)
            return Result(
                metrics={"latency_cycles": Estimate(value=1.0, ci_low=1.0, ci_high=1.0, unit="cycles", method=Method.ANALYTIC)},
                validity=Validity(ok=True, checker_version="t"), domain=Domain(in_domain=True),
                bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
                provenance=Provenance(evaluator="stub@0", inputs={}), escalation=Escalation(recommended=False),
            )

        def evaluate_batch(self, candidates, budget, metrics):
            return [self.evaluate(c, budget, metrics) for c in candidates]

    cands = generate_fusion_tile_candidates(_WORKLOAD, _ARCH, tile_sizes=[2, 4])
    run_architecture_dse(
        workload=_WORKLOAD, candidates=cands, screening_evaluator=_Recorder(),
        metric="latency_cycles", budget=Budget(),
    )
    assert [m["fusion"]["tile"]["B"] for m in seen] == [2, 4]


def test_architecture_axis_candidates_still_get_mapping_none():
    """The generalization must not change the existing axes' behavior."""
    from flux_search_architecture import generate_width_candidates

    c = generate_width_candidates({"schema_version": "0.1.0", "id": "a", "hierarchy": [
        {"level": "pe", "class": "compute", "attrs": {"dims": {"X": 8}}},
    ]}, [4])[0]
    assert getattr(c, "mapping", None) is None
    assert isinstance(generate_fusion_tile_candidates(_WORKLOAD, _ARCH, tile_sizes=[1])[0], FusionTileCandidate)
