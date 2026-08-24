"""Unit tests for flux_validity: pure logic against synthetic Workload/Architecture IR and
Results, no real evaluator involved. See
tests/integration/test_chia_flux_check_validity_live.py for the real end-to-end version (real
ZigZag/RTL numbers, including the compute-bound-minimum arithmetic this repo already verified by
hand in docs/phase1-exit-criterion-report.md).
"""

from __future__ import annotations

import pytest
from flux_evaluator_abi import (
    Bottleneck,
    Domain,
    Escalation,
    Estimate,
    Limiter,
    Method,
    Provenance,
    Result,
    Validity,
)
from flux_validity import (
    NotIndependentlyCheckable,
    check_declared_constraints,
    check_independent_validity,
    check_physical_validity,
    merge_validity,
)

_GEMM_WORKLOAD = {
    "schema_version": "0.1.0",
    "id": "test/wl",
    "tensors": [],
    "ops": [
        {
            "id": "op0", "kind": "einsum", "expr": "B C, C K -> B K",
            "bounds": {"B": 4, "C": 32, "K": 32},
        }
    ],
}

_ARCH_8_LANES = {
    "schema_version": "0.1.0",
    "id": "test/arch",
    "hierarchy": [{"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8}}}],
    "constraints": [{"kind": "area_mm2", "max": 4.0}, {"kind": "tdp_w", "max": 2.0}],
}


def _estimate(value: float, unit: str = "cycles") -> Estimate:
    return Estimate(value=value, ci_low=value, ci_high=value, unit=unit, method=Method.ANALYTIC)


def _result(metrics: dict[str, Estimate], evaluator: str = "fake@0.0.0") -> Result:
    return Result(
        metrics=metrics,
        validity=Validity(ok=True, checker_version="none-v0.1"),
        domain=Domain(in_domain=False),
        bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
        provenance=Provenance(evaluator=evaluator, inputs={}),
        escalation=Escalation(recommended=False),
    )


# --- check_declared_constraints ------------------------------------------------------------


def test_constraints_pass_when_within_declared_bounds():
    result = _result({"area_mm2": _estimate(2.0, "mm2")})
    validity = check_declared_constraints(_ARCH_8_LANES, result)
    assert validity.ok is True
    assert validity.checker_version == "constraints-v0.1:checked=1/2"


def test_constraints_fail_when_max_exceeded():
    result = _result({"area_mm2": _estimate(10.0, "mm2")})
    validity = check_declared_constraints(_ARCH_8_LANES, result)
    assert validity.ok is False
    assert len(validity.violations) == 1
    assert "exceeds declared max" in validity.violations[0].detail


def test_constraints_apply_the_kind_to_metric_alias_for_tdp_w():
    result = _result({"power_w": _estimate(5.0, "W")})
    validity = check_declared_constraints(_ARCH_8_LANES, result)
    assert validity.ok is False
    assert validity.violations[0].kind == "tdp_w"


def test_constraints_skip_metrics_the_result_never_computed():
    result = _result({"latency_cycles": _estimate(100.0)})  # neither area_mm2 nor power_w present
    validity = check_declared_constraints(_ARCH_8_LANES, result)
    assert validity.ok is True
    assert validity.checker_version == "constraints-v0.1:checked=0/2"


def test_constraints_skip_min_bound_violations_too():
    arch = {"constraints": [{"kind": "area_mm2", "min": 1.0}]}
    result = _result({"area_mm2": _estimate(0.5, "mm2")})
    validity = check_declared_constraints(arch, result)
    assert validity.ok is False
    assert "below declared min" in validity.violations[0].detail


def test_constraints_never_check_thermal_kind_yet():
    arch = {"constraints": [{"kind": "thermal", "max_junction_c": 105, "model": "3d-ice"}]}
    result = _result({})
    validity = check_declared_constraints(arch, result)
    assert validity.ok is True
    assert validity.checker_version == "constraints-v0.1:checked=0/1"


def test_constraints_handles_arch_none_and_arch_as_hash_string():
    result = _result({"area_mm2": _estimate(100.0, "mm2")})  # would violate if checked
    for arch in (None, "sha256:deadbeef"):
        validity = check_declared_constraints(arch, result)
        assert validity.ok is True
        assert validity.checker_version == "constraints-v0.1:checked=0/0"


# --- check_physical_validity (roofline) --------------------------------------------------


def test_roofline_passes_for_the_real_pinned_zigzag_and_rtl_numbers():
    """1554 (ZigZag) and 529 (real Verilator RTL) are this repo's own pinned real numbers for
    mlp-gemm0.yaml on an 8-lane array — both must clear the 512-cycle compute-bound minimum
    (4*32*32/8) this check computes independently."""
    for real_value in (1554.0, 529.0, 512.0):
        result = _result({"latency_cycles": _estimate(real_value)})
        validity = check_physical_validity(_GEMM_WORKLOAD, _ARCH_8_LANES, result)
        assert validity.ok is True, f"{real_value} should clear the 512-cycle roofline"
    assert validity.checker_version == "roofline-v0.1:lower_bound=512.0"


def test_roofline_fails_for_a_physically_impossible_latency():
    result = _result({"latency_cycles": _estimate(100.0)})  # below the 512-cycle minimum
    validity = check_physical_validity(_GEMM_WORKLOAD, _ARCH_8_LANES, result)
    assert validity.ok is False
    assert "physically impossible" in validity.violations[0].detail


def test_roofline_not_applicable_without_an_inline_arch():
    result = _result({"latency_cycles": _estimate(100.0)})
    for arch in (None, "sha256:deadbeef"):
        with pytest.raises(NotIndependentlyCheckable):
            check_physical_validity(_GEMM_WORKLOAD, arch, result)


def test_roofline_not_applicable_without_a_latency_metric():
    result = _result({"energy_pj": _estimate(100.0, "pJ")})
    with pytest.raises(NotIndependentlyCheckable):
        check_physical_validity(_GEMM_WORKLOAD, _ARCH_8_LANES, result)


def test_roofline_not_applicable_for_multi_op_or_wrong_shape_workloads():
    multi_op = {"ops": [_GEMM_WORKLOAD["ops"][0], _GEMM_WORKLOAD["ops"][0]]}
    result = _result({"latency_cycles": _estimate(100.0)})
    with pytest.raises(NotIndependentlyCheckable):
        check_physical_validity(multi_op, _ARCH_8_LANES, result)

    wrong_dims = {"ops": [{"kind": "einsum", "bounds": {"B": 4, "K": 32}}]}
    with pytest.raises(NotIndependentlyCheckable):
        check_physical_validity(wrong_dims, _ARCH_8_LANES, result)


def test_roofline_not_applicable_for_multi_spatial_dim_architectures():
    arch_2d = {
        "hierarchy": [{"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": 8, "Y": 8}}}]
    }
    result = _result({"latency_cycles": _estimate(100.0)})
    with pytest.raises(NotIndependentlyCheckable):
        check_physical_validity(_GEMM_WORKLOAD, arch_2d, result)


# --- check_independent_validity / merge_validity -----------------------------------------


def test_check_independent_validity_combines_both_checks():
    result = _result({"latency_cycles": _estimate(1554.0), "area_mm2": _estimate(2.0, "mm2")})
    validity = check_independent_validity(_GEMM_WORKLOAD, _ARCH_8_LANES, result)
    assert validity.ok is True
    assert "constraints-v0.1" in validity.checker_version
    assert "roofline-v0.1" in validity.checker_version


def test_check_independent_validity_fails_if_either_check_fails():
    # Passes the roofline (1554 >= 512) but violates area_mm2's max=4.0.
    result = _result({"latency_cycles": _estimate(1554.0), "area_mm2": _estimate(10.0, "mm2")})
    validity = check_independent_validity(_GEMM_WORKLOAD, _ARCH_8_LANES, result)
    assert validity.ok is False
    assert len(validity.violations) == 1
    assert validity.violations[0].kind == "area_mm2"


def test_check_independent_validity_records_roofline_not_applicable_honestly():
    result = _result({"area_mm2": _estimate(2.0, "mm2")})  # no latency_cycles at all
    validity = check_independent_validity(_GEMM_WORKLOAD, _ARCH_8_LANES, result)
    assert validity.ok is True  # not-applicable doesn't count as a failure
    assert "not_applicable" in validity.checker_version


def test_merge_validity_unions_violations_and_ands_ok():
    a = Validity(ok=False, violations=(_violation("a"),), checker_version="a-v1")
    b = Validity(ok=True, violations=(), checker_version="b-v1")
    merged = merge_validity(a, b)
    assert merged.ok is False
    assert [v.kind for v in merged.violations] == ["a"]
    assert merged.checker_version == "a-v1+b-v1"


def _violation(kind: str):
    from flux_evaluator_abi import Constraint

    return Constraint(kind=kind, detail="synthetic")


def test_a_constraint_with_no_bound_is_not_counted_as_checked():
    """The IR schema requires only `kind`, so `{"kind": "area_mm2"}` with neither max nor min is
    schema-valid. Counting it reported `checked=1/1, ok=True` for a comparison that never
    happened — inside the anti-reward-hacking checker G14 names, where "checked and fine" being
    distinguishable from "nothing to check" is the whole point (docs/decisions.md D188).
    """
    arch = {
        "schema_version": "0.1.0", "id": "a",
        "hierarchy": [{"level": "pe_array", "class": "compute"}],
        "constraints": [{"kind": "area_mm2"}],
    }

    validity = check_declared_constraints(arch, _result({"area_mm2": _estimate(999999.0, "mm2")}))

    assert validity.ok is True, "nothing was violated, because nothing was checkable"
    assert validity.checker_version == "constraints-v0.1:checked=0/1"


def test_a_bounded_constraint_alongside_a_bound_less_one_is_still_checked():
    """Control: the guard must skip only the vacuous entry, not the real one beside it."""
    arch = {
        "schema_version": "0.1.0", "id": "a",
        "hierarchy": [{"level": "pe_array", "class": "compute"}],
        "constraints": [{"kind": "area_mm2"}, {"kind": "area_mm2", "max": 10.0}],
    }

    validity = check_declared_constraints(arch, _result({"area_mm2": _estimate(999.0, "mm2")}))

    assert validity.ok is False
    assert validity.checker_version == "constraints-v0.1:checked=1/2"
