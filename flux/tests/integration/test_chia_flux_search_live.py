"""`flux_search` against a real, local Ray instance — including the harder case: `flux_search`
itself dispatched via `.chia_remote(...)`, which *internally* dispatches more Ray tasks (the
parallel screening sweep, via `ChiaParallelEvaluator`) from inside that outer task. Ray supports
nested remote dispatch natively; this proves it actually works for this code, not just in
Ray's own docs.
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest
import ray
from chia.base.ChiaFunction import get
from flux_chia_nodes import flux_search

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D_V1 = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
_WIDTHS = [4, 8, 16]


@pytest.fixture(scope="module")
def workload_and_arch():
    return (
        flux_ir.load_document(GEMM_WORKLOAD),
        flux_ir.load_document(SIMPLE_NPU_1D_V1),
    )


def test_local_call_screens_ranks_and_escalates(workload_and_arch):
    workload, base_arch = workload_and_arch
    report = flux_search(
        workload, base_arch, "zigzag", widths=_WIDTHS,
        escalation_backends=["systemc", "rtl"],
    )
    assert report.winner.width == 16
    assert [s.rung for s in report.escalation] == ["systemc", "rtl"]
    assert report.escalation[0].result.metrics["latency_cycles"].value == 265.0
    assert report.escalation[1].result.metrics["latency_cycles"].value == 265.0


def test_chia_remote_dispatch_of_flux_search_itself_works_with_nested_ray_calls(workload_and_arch):
    """The harder case: flux_search is dispatched as ONE Ray task, and that task itself
    dispatches THREE MORE Ray tasks (the parallel width sweep) from inside — nested remote
    dispatch, not merely `flux_search` calling plain local functions internally."""
    workload, base_arch = workload_and_arch
    ref = flux_search.chia_remote(
        workload, base_arch, "zigzag", widths=_WIDTHS,
        escalation_backends=["systemc", "rtl"],
    )
    assert isinstance(ref, ray.ObjectRef)
    report = get(ref)
    assert report.winner.width == 16
    assert report.escalation[0].result.metrics["latency_cycles"].value == 265.0
    assert report.escalation[1].result.metrics["latency_cycles"].value == 265.0


def test_chia_remote_blocking_returns_the_unwrapped_report(workload_and_arch):
    workload, base_arch = workload_and_arch
    report = flux_search.chia_remote_blocking(workload, base_arch, "zigzag", widths=_WIDTHS)
    assert report.winner.width == 16


def test_parallel_screening_false_still_gives_the_same_answer(workload_and_arch):
    """parallel_screening=False takes the sequential-in-process path (no nested Ray dispatch at
    all) — must reach the identical answer, proving the two paths are equivalent, not just both
    plausible-looking."""
    workload, base_arch = workload_and_arch
    report = flux_search(
        workload, base_arch, "zigzag", widths=_WIDTHS, parallel_screening=False,
    )
    assert report.winner.width == 16
    by_width = {p.candidate.width: p.result.metrics["latency_cycles"].value for p in report.swept}
    assert by_width == {4: 3106.0, 8: 1554.0, 16: 778.0}


def test_noc_topology_search_kind_connects_the_real_3d_noc_dse_into_chia():
    """The actual "is NoC DSE connected into CHIA" answer, made real: the same `flux_search`
    node, same engine, same `.chia_remote()` surface as the compute-width sweeps above — just
    `search_kind="noc_topology"` and a real Booksim2 backend instead. Compares a real 2D 8x8 mesh
    against a real 3D 4x4x4 mesh (both 64 nodes) dispatched as one Ray task.
    """
    noc_workload = flux_ir.load_document(GEMM_WORKLOAD)  # content unused by booksim, still hashed
    noc_base_arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/noc-mesh-2d-v1.yaml")

    report = flux_search(
        noc_workload, noc_base_arch, "booksim",
        search_kind="noc_topology",
        noc_topology_variants=[("mesh", [8, 8]), ("mesh", [4, 4, 4])],
        parallel_screening=True,
    )

    assert len(report.swept) == 2
    assert all(p.error is None for p in report.swept)
    assert report.winner is not None
    assert report.winner.dimensions == (4, 4, 4)  # the 3D topology wins on latency, as expected


def test_noc_topology_search_kind_requires_noc_topology_variants():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/noc-mesh-2d-v1.yaml")
    with pytest.raises(ValueError, match="requires noc_topology_variants"):
        flux_search(workload, base_arch, "booksim", search_kind="noc_topology")


def test_memory_size_search_kind_connects_the_real_memory_dse_into_chia():
    """`search_kind="memory_size"` (docs/decisions.md D26) through the same `flux_search` node
    and engine as the width/NoC sweeps above — real ZigZag, dispatched as one Ray task. Reproduces
    test_architecture_memory_dse_live.py's own finding at the CHIA layer: 1.0 KiB is infeasible
    (the workload's working set doesn't fit), the smallest feasible size (1.25 KiB) wins on
    energy, not the largest.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D_V1)

    report = flux_search(
        workload, base_arch, "zigzag",
        search_kind="memory_size", memory_level="gbuf", memory_sizes_kb=[1.0, 1.25, 64.0, 512.0],
        metric="energy_pj", minimize=True,
    )

    assert len(report.swept) == 4
    infeasible = next(p for p in report.swept if p.candidate.size_kb == 1.0)
    assert infeasible.error is not None
    assert all(p.error is None for p in report.swept if p.candidate.size_kb != 1.0)
    assert report.winner is not None
    assert report.winner.size_kb == 1.25


def test_memory_size_search_kind_requires_memory_level_and_sizes():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D_V1)
    with pytest.raises(ValueError, match="requires memory_level and memory_sizes_kb"):
        flux_search(workload, base_arch, "zigzag", search_kind="memory_size")


def test_joint_search_kind_connects_the_real_joint_dse_into_chia():
    """`search_kind="joint"` (docs/decisions.md D26): the width x memory-size Cartesian product,
    real ZigZag, one Ray task. For this workload the two axes are separable (checked in
    test_architecture_memory_dse_live.py) — the joint winner is exactly where each single-axis
    optimum already points, width=16 (fastest/least energy) x 1.25 KiB (smallest feasible).
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D_V1)

    report = flux_search(
        workload, base_arch, "zigzag",
        search_kind="joint", widths=_WIDTHS, memory_level="gbuf", memory_sizes_kb=[1.25, 64.0],
        metric="energy_pj", minimize=True,
    )

    assert len(report.swept) == len(_WIDTHS) * 2
    assert all(p.error is None for p in report.swept)
    assert report.winner is not None
    assert report.winner.width == 16
    assert report.winner.size_kb == 1.25


def test_joint_search_kind_requires_widths_memory_level_and_sizes():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D_V1)
    with pytest.raises(ValueError, match="requires widths, memory_level, and memory_sizes_kb"):
        flux_search(workload, base_arch, "zigzag", search_kind="joint", widths=_WIDTHS)


def test_unknown_search_kind_is_rejected():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D_V1)
    with pytest.raises(ValueError, match="search_kind"):
        flux_search(workload, base_arch, "zigzag", search_kind="not-a-real-kind", widths=_WIDTHS)


def test_result_db_path_opts_into_warm_start_for_the_whole_sweep(tmp_path):
    """`result_db_path` (docs/decisions.md D19) wraps screening in `flux_store.CachingEvaluator`
    — a second identical sweep against the same store reproduces the same winner with every
    candidate served from the store, checked by timing the same way
    test_result_db_path_opts_into_warm_start does for `flux_evaluate`.
    """
    import time

    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D_V1)
    db_path = str(tmp_path / "flux.db")

    start = time.monotonic()
    first = flux_search(
        workload, base_arch, "zigzag", widths=_WIDTHS, parallel_screening=False,
        result_db_path=db_path,
    )
    first_elapsed = time.monotonic() - start

    start = time.monotonic()
    second = flux_search(
        workload, base_arch, "zigzag", widths=_WIDTHS, parallel_screening=False,
        result_db_path=db_path,
    )
    second_elapsed = time.monotonic() - start

    assert first.winner.width == second.winner.width
    assert (
        first.winner_screening_result.metrics["latency_cycles"].value
        == second.winner_screening_result.metrics["latency_cycles"].value
    )
    assert second_elapsed < first_elapsed / 10
