"""Real memory-hierarchy-size design-space exploration (docs/decisions.md D26): sweep
mlp-gemm0.yaml's on-chip buffer (`gbuf`) capacity through real ZigZag, for `simple-npu-1d-v1.yaml`
(8-wide array). The third architecture axis alongside compute width (D5,
test_architecture_dse_live.py) and NoC topology (D6, test_chia_flux_search_live.py) — same real
evaluator, different knob.

**A genuinely different landscape shape than either of this package's other two axes** (found
empirically before this test was written, not assumed): below 1.25 KiB, `mlp-gemm0.yaml`'s
working set genuinely doesn't fit in `gbuf`, and ZigZag's own mapper rejects the candidate
outright ("No valid loop ordering was found ... does not fit within the full memory hierarchy") —
a real per-candidate failure, not a crash. At and above 1.25 KiB, `latency_cycles` is flat (buffer
capacity isn't the bottleneck once the working set fits) but `energy_pj` *increases monotonically
with size* — ZigZag's own cost model charges more energy per access for a bigger SRAM, even when
the extra capacity goes unused. The real minimum-energy point is therefore the *smallest feasible*
size (1.25 KiB here), not the largest, unlike the architecture-width axis where wider always wins.

Also checked directly, not assumed: this feasibility floor does **not** shift with compute array
width for this workload (1.0 KiB fails and 1.25 KiB succeeds at width=4, 8, and 32 alike) — the
width and memory-size axes are separable for `mlp-gemm0.yaml`/`simple-npu-1d-v1.yaml`, a real,
checked finding, not a structural guarantee of the search engine itself.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_zigzag import ZigZagEvaluator
from flux_search_architecture import (
    generate_joint_candidates,
    generate_memory_size_candidates,
    run_architecture_dse,
)

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D_V1 = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"

# Real, pinned ZigZag measurements at width=8 (docs/decisions.md D26) — 1.0 KiB is infeasible
# (the workload's working set doesn't fit); every size from 1.25 KiB up is feasible, with latency
# constant and energy increasing monotonically with size.
_INFEASIBLE_SIZE_KB = 1.0
_FEASIBLE_SIZES_KB = [1.25, 2.0, 4.0, 8.0, 16.0, 64.0, 512.0]
_LATENCY_AT_EVERY_FEASIBLE_SIZE = 1554.0
_ENERGY_BY_SIZE_KB = {
    1.25: 1116618.0081255918,
    2.0: 1116620.0962474998,
    4.0: 1116625.2273767025,
    8.0: 1116634.566857141,
    16.0: 1116651.5662136981,
    64.0: 1116738.826398288,
    512.0: 1117367.5287841093,
}


@pytest.fixture(scope="module")
def report():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D_V1)
    sizes = [_INFEASIBLE_SIZE_KB, *_FEASIBLE_SIZES_KB]
    return run_architecture_dse(
        workload, generate_memory_size_candidates(base_arch, "gbuf", sizes), ZigZagEvaluator(),
        metric="energy_pj", minimize=True,
    )


def test_the_too_small_candidate_fails_honestly_not_a_crash(report):
    failed = [p for p in report.swept if p.candidate.size_kb == _INFEASIBLE_SIZE_KB]
    assert len(failed) == 1
    assert failed[0].result is None
    assert failed[0].error is not None
    assert "memory hierarchy" in failed[0].error or "loop ordering" in failed[0].error


def test_every_feasible_size_succeeds(report):
    feasible = [p for p in report.swept if p.candidate.size_kb in _FEASIBLE_SIZES_KB]
    assert len(feasible) == len(_FEASIBLE_SIZES_KB)
    assert all(p.error is None for p in feasible)


def test_latency_is_flat_across_feasible_sizes(report):
    """Real ZigZag numbers: buffer capacity isn't mlp-gemm0.yaml's bottleneck at width=8 once the
    working set fits — every feasible size gives the identical 1554-cycle latency."""
    by_size = {
        p.candidate.size_kb: p.result.metrics["latency_cycles"].value
        for p in report.swept if p.candidate.size_kb in _FEASIBLE_SIZES_KB
    }
    assert all(v == pytest.approx(_LATENCY_AT_EVERY_FEASIBLE_SIZE) for v in by_size.values())


def test_energy_increases_monotonically_with_size(report):
    """The genuinely counter-intuitive real result this axis exists to demonstrate: a bigger
    buffer costs strictly more energy in ZigZag's own cost model, even though the extra capacity
    goes completely unused for this workload."""
    by_size = {
        p.candidate.size_kb: p.result.metrics["energy_pj"].value
        for p in report.swept if p.candidate.size_kb in _FEASIBLE_SIZES_KB
    }
    ordered_sizes = sorted(by_size)
    ordered_energies = [by_size[s] for s in ordered_sizes]
    assert ordered_energies == sorted(ordered_energies)  # strictly non-decreasing with size
    for size_kb, expected_energy in _ENERGY_BY_SIZE_KB.items():
        assert by_size[size_kb] == pytest.approx(expected_energy)


def test_winner_is_the_smallest_feasible_size_not_the_largest(report):
    """The real minimum-energy point: the smallest size that still fits, not the largest —
    unlike the architecture-width axis (D5), where the widest candidate always wins."""
    assert report.winner is not None
    assert report.winner.size_kb == min(_FEASIBLE_SIZES_KB)


class TestJointWidthAndMemorySize:
    """generate_joint_candidates: the width x memory-size Cartesian product, real ZigZag."""

    @staticmethod
    @pytest.fixture(scope="class")
    def joint_report():
        workload = flux_ir.load_document(GEMM_WORKLOAD)
        base_arch = flux_ir.load_document(SIMPLE_NPU_1D_V1)
        candidates = generate_joint_candidates(base_arch, [4, 8, 32], "gbuf", [1.25, 64.0])
        return run_architecture_dse(
            workload, candidates, ZigZagEvaluator(), metric="energy_pj", minimize=True,
        )

    def test_covers_the_full_six_point_grid(self, joint_report):
        assert len(joint_report.swept) == 6
        assert all(p.error is None for p in joint_report.swept)  # both sizes feasible at every width

    def test_winner_is_the_widest_and_smallest_point(self, joint_report):
        """For this workload the two axes are separable (checked, not assumed — see module
        docstring): the joint optimum is exactly where each single-axis optimum already points,
        width=32 (D13's real strictly-monotonic winner) combined with the smallest feasible
        buffer (this file's own single-axis finding above)."""
        assert joint_report.winner is not None
        assert joint_report.winner.width == 32
        assert joint_report.winner.size_kb == 1.25

    def test_energy_falls_with_width_at_fixed_size(self, joint_report):
        by_width = {
            p.candidate.width: p.result.metrics["energy_pj"].value
            for p in joint_report.swept if p.candidate.size_kb == 1.25
        }
        assert by_width[4] > by_width[8] > by_width[32]

    def test_energy_rises_with_size_at_fixed_width(self, joint_report):
        for width in (4, 8, 32):
            by_size = {
                p.candidate.size_kb: p.result.metrics["energy_pj"].value
                for p in joint_report.swept if p.candidate.width == width
            }
            assert by_size[1.25] < by_size[64.0]
