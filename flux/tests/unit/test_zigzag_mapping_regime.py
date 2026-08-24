"""Unit tests for the `lanes == C` predicate (docs/decisions.md D109/D110) — the advisory
detector for the one candidate shape where ZigZag's residual against RTL behaves qualitatively
differently. Every positive/negative case below is a real measured point from D109's sweeps, so
these tests encode the actual finding rather than a made-up example.
"""

from __future__ import annotations

import pytest
from flux_evaluator_zigzag import CAVEAT, caveat_for, fully_unrolls_reduction_dim, reduction_dims


def _wl(B: int, C: int, K: int) -> dict:
    return {"schema_version": "0.1.0", "id": f"b{B}c{C}k{K}",
            "ops": [{"id": "gemm0", "kind": "einsum", "expr": "B C, C K -> B K",
                     "bounds": {"B": B, "C": C, "K": K}}]}


def _arch(lanes: int) -> dict:
    return {"schema_version": "0.1.0", "id": f"w{lanes}",
            "hierarchy": [{"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
                          {"level": "pe", "class": "compute", "attrs": {"dims": {"X": lanes}}}]}


def test_reduction_dim_is_the_one_shared_by_inputs_and_absent_from_output():
    assert reduction_dims({"expr": "B C, C K -> B K"}) == ["C"]
    assert reduction_dims({"expr": "S D, T D -> S T"}) == ["D"]
    assert reduction_dims({"expr": "not an einsum"}) == []
    assert reduction_dims({}) == []


@pytest.mark.parametrize("lanes,C", [(8, 8), (16, 16), (32, 32)])
def test_real_measured_positives_are_detected(lanes, C):
    """D109's confirmed low-residual points: +0.772, +0.876, +0.932 respectively."""
    assert fully_unrolls_reduction_dim(_wl(4, C, 32), _arch(lanes)) is True
    assert caveat_for(_wl(4, C, 32), _arch(lanes)) == CAVEAT


@pytest.mark.parametrize("lanes,C", [(32, 64), (64, 32), (16, 32), (8, 32), (1, 32)])
def test_real_measured_negatives_are_not_flagged(lanes, C):
    """D109's confirmed normal-residual points (~+1.9 to +2.1) — including lanes=32 with C=64,
    which is what refuted the earlier 'width 32 is special' explanation."""
    assert fully_unrolls_reduction_dim(_wl(4, C, 32), _arch(lanes)) is False
    assert caveat_for(_wl(4, C, 32), _arch(lanes)) is None


@pytest.mark.parametrize("arch", [
    None,
    {"hierarchy": []},                                                    # no compute node
    {"hierarchy": [{"class": "compute", "attrs": {"dims": {"X": 8, "Y": 8}}}]},  # multi-dim
    {"hierarchy": [{"class": "compute", "attrs": {}}]},                   # no dims
    {"hierarchy": [{"class": "compute", "attrs": {"dims": {"X": "eight"}}}]},    # non-integer
])
def test_unidentifiable_shapes_are_conservative_not_fatal(arch):
    """A residual is only ever excluded from calibration on a positive, well-understood match —
    anything ambiguous returns False rather than raising, since this is advisory."""
    assert fully_unrolls_reduction_dim(_wl(4, 32, 32), arch) is False


def test_a_dynamic_bound_does_not_match_and_does_not_raise():
    wl = {"ops": [{"id": "o", "kind": "einsum", "expr": "B C, C K -> B K",
                   "bounds": {"B": 4, "C": {"dyn": [1, 32]}, "K": 32}}]}
    assert fully_unrolls_reduction_dim(wl, _arch(32)) is False


def test_a_single_op_workload_matches_on_its_own_reduction_dim():
    """UPDATED CONTRACT (docs/decisions.md D112). This test previously asserted that ANY matching
    op caveats a multi-op workload; that was a false positive — see
    `test_multi_op_workload_needs_every_op_on_the_diagonal` below. A single-op workload is the
    case where any and all coincide, which is what this now pins."""
    wl = {"ops": [
        {"id": "b", "kind": "einsum", "expr": "B H, H K -> B K", "bounds": {"B": 4, "H": 16, "K": 32}},
    ]}
    assert fully_unrolls_reduction_dim(wl, _arch(16)) is True   # its reduction dim H == 16
    assert fully_unrolls_reduction_dim(wl, _arch(8)) is False


def test_non_einsum_ops_are_ignored():
    wl = {"ops": [{"id": "x", "kind": "data_dependent", "expr": "B C, C K -> B K",
                   "bounds": {"B": 4, "C": 32, "K": 32}}]}
    assert fully_unrolls_reduction_dim(wl, _arch(32)) is False


# --- The "never raises" contract, actually enforced (docs/decisions.md D112) ---


@pytest.mark.parametrize("bad_arch", [
    {"hierarchy": None},
    {"hierarchy": [{"class": "compute", "attrs": None}]},
    {"hierarchy": ["not-a-dict"]},
    {"hierarchy": [{"class": "compute", "attrs": {"dims": None}}]},
    {"hierarchy": [{"class": "compute", "attrs": {"dims": "eight"}}]},
    {"hierarchy": [{"class": "compute", "attrs": {"dims": {"X": True}}}]},  # bool is not a width
])
def test_malformed_architectures_return_false_instead_of_raising(bad_arch):
    """Review finding: `.get('attrs', {})` returns None for a present-but-null key, and the two
    CHIA callers guard only ImportError — so these killed a real conformance run."""
    assert fully_unrolls_reduction_dim({"ops": []}, bad_arch) is False


@pytest.mark.parametrize("bad_wl", [
    {"ops": None},
    {"ops": "nope"},
    {"ops": [None]},
    {"ops": [{"kind": "einsum", "expr": None, "bounds": {"C": 8}}]},
    {"ops": [{"kind": "einsum", "expr": "B C, C K -> B K", "bounds": None}]},
])
def test_malformed_workloads_return_false_instead_of_raising(bad_wl):
    assert fully_unrolls_reduction_dim(bad_wl, _arch(8)) is False


def test_multi_op_workload_needs_every_op_on_the_diagonal(): 
    """Review finding (D112): `any` meant one matching op out of many excluded a residual that
    was mostly off-diagonal, silently shrinking the calibration pool."""
    mixed = {"ops": [
        {"id": "a", "kind": "einsum", "expr": "B C, C H -> B H", "bounds": {"B": 4, "C": 64, "H": 16}},
        {"id": "b", "kind": "einsum", "expr": "B H, H K -> B K", "bounds": {"B": 4, "H": 16, "K": 32}},
    ]}
    assert fully_unrolls_reduction_dim(mixed, _arch(16)) is False   # only op b matches -> not caveated
    assert fully_unrolls_reduction_dim(mixed, _arch(64)) is False   # only op a matches

    both = {"ops": [
        {"id": "a", "kind": "einsum", "expr": "B C, C H -> B H", "bounds": {"B": 4, "C": 16, "H": 16}},
        {"id": "b", "kind": "einsum", "expr": "B H, H K -> B K", "bounds": {"B": 4, "H": 16, "K": 32}},
    ]}
    assert fully_unrolls_reduction_dim(both, _arch(16)) is True     # every op on the diagonal
