"""`flux_check_validity` against real ZigZag, real Verilator-simulated RTL, and real Timeloop —
the fifth CHIA node (docs/decisions.md D9/D10), verified against real numbers rather than
fakes: real ZigZag/RTL never compute `area_mm2`/`power_w` (the declared-constraints check
honestly reports `checked=0/2`), real Timeloop does compute `area_mm2` (the same check reports
`checked=1/2`, a real constraint actually evaluated) — and all three real latency measurements
(1554, 529, and exactly 512 cycles) clear the same 512-cycle compute-bound roofline this repo
already computed by hand in docs/phase1-exit-criterion-report.md.

Requires the real `chia` package (see `flows/chia_nodes/README.md` for the submodule gotcha),
`flux-evaluator-zigzag`, `flux-evaluator-rtl` (needs `verilator`), and `flux-evaluator-timeloop`
(needs a working `docker` daemon — real Timeloop-via-Docker parts of this file are the slowest).
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
import ray
from flux_chia_nodes import flux_check_validity

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"


@pytest.fixture
def workload_and_arch():
    return (
        flux_ir.load_document(GEMM_WORKLOAD),
        flux_ir.load_document(SIMPLE_NPU_1D),
    )


def test_zigzag_result_clears_the_real_compute_bound_roofline(workload_and_arch):
    """1554 cycles (ZigZag's real, pinned, and documented overestimate for this candidate) must
    clear the independently-computed 512-cycle compute-bound minimum (4*32*32/8) — the same
    arithmetic docs/phase1-exit-criterion-report.md already verified by hand.
    """
    workload, arch = workload_and_arch
    result = flux_check_validity("zigzag", workload, arch)
    assert result.metrics["latency_cycles"].value == pytest.approx(1554.0)
    assert result.validity.ok is True
    assert "roofline-v0.1:lower_bound=512.0" in result.validity.checker_version


def test_rtl_result_clears_the_roofline_and_keeps_its_own_self_check(workload_and_arch):
    """The merge must preserve evaluators/rtl's own real self-check (against a Python golden
    reference), not discard it in favour of the independent one.
    """
    workload, arch = workload_and_arch
    result = flux_check_validity("rtl", workload, arch)
    assert result.metrics["latency_cycles"].value == pytest.approx(529.0)
    assert result.validity.ok is True
    assert "rtl-testbench-self-check-v0.1" in result.validity.checker_version
    assert "roofline-v0.1:lower_bound=512.0" in result.validity.checker_version


def test_neither_zigzag_nor_rtl_compute_area_or_power_so_constraints_are_honestly_unchecked(
    workload_and_arch,
):
    """simple-npu-1d-v1.yaml declares area_mm2 max=4.0 and tdp_w max=2.0, but neither ZigZag nor
    RTL report those metrics — the constraints check must say `checked=0/2`, not silently pass as
    if it had verified something it never looked at.
    """
    workload, arch = workload_and_arch
    for backend in ("zigzag", "rtl"):
        result = flux_check_validity(backend, workload, arch)
        assert "constraints-v0.1:checked=0/2" in result.validity.checker_version


def test_timeloop_actually_computes_area_so_the_constraint_is_really_checked(workload_and_arch):
    """Timeloop is the one real backend here that reports `area_mm2` — this is the one case
    where the declared-constraints check has something real to compare, not a synthetic fixture.
    """
    workload, arch = workload_and_arch
    result = flux_check_validity(
        "timeloop", workload, arch, metrics=["latency_cycles", "energy_pj", "area_mm2"]
    )
    assert "area_mm2" in result.metrics
    assert "constraints-v0.1:checked=1/2" in result.validity.checker_version
    # Timeloop's real mapper found the exact compute-bound optimum for this candidate — the
    # roofline check's >= boundary must accept an exact match, not just strictly-above.
    assert result.metrics["latency_cycles"].value == pytest.approx(512.0)
    assert "roofline-v0.1:lower_bound=512.0" in result.validity.checker_version


def test_arch_none_makes_both_independent_checks_report_not_applicable(workload_and_arch):
    """With no inline Architecture IR document, neither independent check has an architecture to
    read constraints or lane counts from — both must say so honestly rather than pass by default.
    """
    workload, _arch = workload_and_arch
    result = flux_check_validity("zigzag", workload, arch=None)
    assert result.validity.ok is True  # not-applicable is not a failure
    assert "constraints-v0.1:checked=0/0" in result.validity.checker_version
    assert "roofline-v0.1:not_applicable" in result.validity.checker_version


def test_dispatches_through_a_real_ray_task(workload_and_arch):
    from chia.base.ChiaFunction import get

    workload, arch = workload_and_arch
    ref = flux_check_validity.chia_remote("zigzag", workload, arch)
    assert isinstance(ref, ray.ObjectRef)
    result = get(ref)
    assert result.metrics["latency_cycles"].value == pytest.approx(1554.0)
    assert result.validity.ok is True
