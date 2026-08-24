"""`flux_explain_candidate` (docs/decisions.md D157).

The node steers a choice, so its failure mode matters more than its success: telling a caller a
backend *cannot* express their design when the checker itself is broken is a false negative that
sends them somewhere else for no reason. That bug was written and caught during development — a
stale import reported Timeloop as refusing a candidate it handles — so it is pinned here.
"""

from __future__ import annotations

import pytest
from flux_chia_nodes import flux_explain_candidate

_WORKLOAD = {
    "schema_version": "0.1.0", "id": "t/gemm",
    "ops": [{"id": "g", "kind": "einsum", "expr": "B C, C K -> B K",
             "bounds": {"B": 4, "C": 32, "K": 32},
             "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8}}],
}


def _arch(lanes: int) -> dict:
    return {"schema_version": "0.1.0", "id": f"t/a{lanes}",
            "hierarchy": [{"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
                          {"level": "pe", "class": "compute", "attrs": {"dims": {"X": lanes}}}]}


def test_a_ragged_k_group_is_refused_by_rtl_with_the_adapters_own_reason():
    """The candidate D130/D134 built for: `evaluators/rtl` cannot express it, and the reason names
    the actual constraint rather than saying "not expressible"."""
    report = flux_explain_candidate.__wrapped__(_WORKLOAD, _arch(12))

    assert "rtl" in report.refused_by
    rtl = next(b for b in report.backends if b.backend == "rtl")
    assert "not a multiple of LANES=12" in rtl.reason


def test_a_divisible_candidate_is_expressible_everywhere_checked():
    report = flux_explain_candidate.__wrapped__(_WORKLOAD, _arch(8))

    assert report.refused_by == []
    assert {"rtl", "zigzag", "timeloop"} <= set(report.expressible_by)


def test_a_broken_checker_reports_unknown_not_refused(monkeypatch):
    """The bug this file exists for. A checker that raises ImportError/AttributeError is *our*
    fault; reporting `False` would blame the candidate and send the caller elsewhere."""
    import flux_chia_nodes.explain_candidate as mod

    def _stale_import(workload, arch):
        raise ImportError("cannot import name 'einsum_op_to_timeloop_problem_yaml'")

    monkeypatch.setitem(mod._CHECKS, "timeloop", _stale_import)
    report = flux_explain_candidate.__wrapped__(_WORKLOAD, _arch(8))

    timeloop = next(b for b in report.backends if b.backend == "timeloop")
    assert timeloop.expressible is None, "a broken check must not read as a refusal"
    assert "check unavailable" in timeloop.reason
    assert "timeloop" not in report.refused_by


def test_backends_without_a_check_are_unknown_rather_than_assumed():
    """`None` for the backends with no translation-only check — an unknown is not a yes, and not
    a no. Both mistakes would be worse than saying nothing."""
    report = flux_explain_candidate.__wrapped__(_WORKLOAD, _arch(8))

    unknown = [b for b in report.backends if b.expressible is None]
    assert unknown, "several backends have no cheap check; that must be visible"
    assert all(b.backend not in report.expressible_by + report.refused_by for b in unknown)
    assert "no simulation" in report.to_dict()["checked"]


def test_the_timeloop_check_looks_at_the_workload_not_just_the_architecture():
    """Found by review: `_check_timeloop` accepted `workload` and never used it, so a workload
    Timeloop cannot express was reported expressible on the strength of a valid architecture. A
    check that answers `True` without looking is worse than one that answers `None` — the same
    false-positive shape as reporting a broken checker as a refusal, in the other direction."""
    no_einsum = {"schema_version": "0.1.0", "id": "t/dd",
                 "ops": [{"id": "r", "kind": "data_dependent", "expr": "x -> y",
                          "bounds": {"N": 4}}]}

    report = flux_explain_candidate.__wrapped__(no_einsum, _arch(8))
    timeloop = next(b for b in report.backends if b.backend == "timeloop")

    assert timeloop.expressible is False
    assert "einsum" in timeloop.reason


def test_a_valid_gemm_still_translates_for_timeloop():
    """The other side of the same change: tightening the check must not start refusing the
    workloads this repo actually evaluates."""
    report = flux_explain_candidate.__wrapped__(_WORKLOAD, _arch(8))
    timeloop = next(b for b in report.backends if b.backend == "timeloop")

    assert timeloop.expressible is True


_MAPPING = {
    "schema_version": "0.1.0", "id": "t/map0", "for_op": "g",
    "spatial": [{"dim": "K", "level": "pe", "factor": 8}],
    "temporal": [{"dim": "C", "level": "gbuf", "factor": 32}],
}


def test_rtl_refuses_any_mapping_because_its_schedule_is_fixed():
    """`RTLEvaluator` does not translate Mapping IR — its schedule lives in the RTL. The node had
    no `mapping` parameter at first, so it could not be wrong about this, only silent."""
    report = flux_explain_candidate.__wrapped__(_WORKLOAD, _arch(8), _MAPPING)

    rtl = next(b for b in report.backends if b.backend == "rtl")
    assert rtl.expressible is False and "fixed schedule" in rtl.reason


def test_a_mapping_is_checked_against_the_architecture_not_the_workload():
    """Regression for the third false negative written into this file: `mapping_ir_to_zigzag_mapping`
    takes `(mapping, arch)`, and passing the workload produced 'arch ... has 0 compute nodes' — a
    refusal caused by this code rather than by the candidate. A caller cannot tell those apart, so
    the test asserts the *reason*, not merely that something was refused."""
    report = flux_explain_candidate.__wrapped__(_WORKLOAD, _arch(8), _MAPPING)

    zigzag = next(b for b in report.backends if b.backend == "zigzag")
    assert "0 compute nodes" not in zigzag.reason


def test_omitting_a_mapping_leaves_the_backends_that_need_none_expressible():
    report = flux_explain_candidate.__wrapped__(_WORKLOAD, _arch(8))
    assert "rtl" in report.expressible_by
