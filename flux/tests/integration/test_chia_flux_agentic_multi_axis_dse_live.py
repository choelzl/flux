"""`flux_agentic_multi_axis_dse` (docs/decisions.md D34) against real Ray, real Ollama, real
ZigZag, and real Booksim2 — the first CHIA node in this repo whose own internal concurrency is
the thing being tested, not just "does it also work via .chia_remote()" (every other agentic node
already proves that; this one's whole reason to exist is what happens *inside* one call).

Two real things checked, neither assumed:
1. **Real concurrency, not an interface that merely permits it.** The same three searches
   (architecture_width, memory_size, noc_topology) are also run sequentially, in-process, by this
   test — timed the same way — and the concurrent flow's own `dispatch_wall_clock_s` must beat
   that sequential sum by a real margin, not just be plausibly close.
2. **Whether two blindly, independently optimized axes (width, memory_size — each search never
   sees the other's result) land on the same point `AgenticJointStrategy`'s *coordinated* search
   over the combined space already found (docs/decisions.md D26/D28: width=32, size_kb=1.25,
   193018.0081255918 pJ).** Both individual axes' own established optima (width=32 for latency,
   docs/decisions.md D13; size_kb=1.25 for energy, D26/D27) already match the joint winner's own
   per-axis choices — so the composed candidate is predicted, not just hoped, to reproduce the
   exact same pinned value. A mismatch here would be a real, interesting non-separability
   finding; a match confirms this particular workload's two axes compose additively.

Requires real Booksim2 (`nix shell nixpkgs#flex nixpkgs#bison`, see
evaluators/booksim/README.md) and a local Ollama server with `qwen2.5-coder:7b` pulled (same as
every other agentic-search live test).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import flux_ir
import pytest
import ray
from chia.base.ChiaFunction import get
from flux_chia_nodes import (
    flux_agentic_architecture_search,
    flux_agentic_memory_search,
    flux_agentic_multi_axis_dse,
    flux_agentic_noc_search,
)

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
NOC_MESH_2D = FLUX_ROOT / "core/ir/architecture/examples/noc-mesh-2d-v1.yaml"

# Same grid test_search_agentic_joint_live.py uses, so the composite result is directly
# comparable to that file's own pinned joint optimum.
_VALID_WIDTHS = [4, 32]
_VALID_SIZES_KB = [1.0, 1.25, 64.0]
_NOC_VARIANTS = [("mesh", [8, 8]), ("torus", [4, 4, 4])]

# docs/decisions.md D26/D28's real, pinned joint-search optimum for this exact workload/arch/grid.
_KNOWN_JOINT_OPTIMUM_ENERGY_PJ = 193018.0081255918


@pytest.fixture(scope="module")
def workload():
    return flux_ir.load_document(GEMM_WORKLOAD)


@pytest.fixture(scope="module")
def compute_memory_arch():
    return flux_ir.load_document(SIMPLE_NPU_1D)


@pytest.fixture(scope="module")
def noc_arch():
    return flux_ir.load_document(NOC_MESH_2D)


def test_multi_axis_dse_dispatches_three_searches_and_composes_two_of_them(
    workload, compute_memory_arch, noc_arch
):
    report = flux_agentic_multi_axis_dse(
        workload, compute_memory_arch, noc_arch, "zigzag", "booksim",
        valid_widths=_VALID_WIDTHS, memory_level="gbuf", valid_sizes_kb=_VALID_SIZES_KB,
        valid_noc_variants=_NOC_VARIANTS, composite_metric="energy_pj", seed=0,
    )

    # All three sub-searches produced a real winner.
    assert report.width_report.best is not None
    assert report.memory_report.best is not None
    assert report.noc_report.best is not None

    # The composite candidate was built and evaluated for real.
    assert report.composite_arch is not None
    assert report.composite_error is None
    assert report.composite_result is not None
    assert report.composite_result.metrics["energy_pj"].value > 0
    assert report.dispatch_wall_clock_s > 0


def test_blindly_composed_width_and_memory_winners_match_the_known_joint_optimum(
    workload, compute_memory_arch, noc_arch
):
    """The real, predicted-in-advance finding this module exists to check: width's own
    independent optimum (32, for latency) and memory_size's own independent optimum (1.25 KiB,
    for energy) are individually already known to match AgenticJointStrategy's coordinated
    winner's own per-axis choices — so composing them here should reproduce the exact same
    pinned energy value, not merely something in the same ballpark.
    """
    report = flux_agentic_multi_axis_dse(
        workload, compute_memory_arch, noc_arch, "zigzag", "booksim",
        valid_widths=_VALID_WIDTHS, memory_level="gbuf", valid_sizes_kb=_VALID_SIZES_KB,
        valid_noc_variants=_NOC_VARIANTS, composite_metric="energy_pj", seed=0,
    )

    assert report.width_report.best.width == 32
    assert report.memory_report.best.size_kb == 1.25
    assert report.composite_result.metrics["energy_pj"].value == pytest.approx(
        _KNOWN_JOINT_OPTIMUM_ENERGY_PJ
    )


def test_concurrent_dispatch_is_really_faster_than_the_same_three_searches_run_sequentially(
    workload, compute_memory_arch, noc_arch
):
    """The real evidence this is genuine CHIA/Ray concurrency, not an interface that merely
    permits it: the same three searches, run back-to-back in-process (no chia_remote at all),
    timed the same way, must take meaningfully longer than the concurrent flow's own measured
    dispatch time.
    """
    concurrent_report = flux_agentic_multi_axis_dse(
        workload, compute_memory_arch, noc_arch, "zigzag", "booksim",
        valid_widths=_VALID_WIDTHS, memory_level="gbuf", valid_sizes_kb=_VALID_SIZES_KB,
        valid_noc_variants=_NOC_VARIANTS, composite_metric="energy_pj", seed=1,
    )

    sequential_start = time.monotonic()
    flux_agentic_architecture_search(
        workload, compute_memory_arch, "zigzag",
        valid_widths=_VALID_WIDTHS, metric="latency_cycles", minimize=True, seed=1,
    )
    flux_agentic_memory_search(
        workload, compute_memory_arch, "zigzag",
        level="gbuf", valid_sizes_kb=_VALID_SIZES_KB, metric="energy_pj", minimize=True, seed=1,
    )
    flux_agentic_noc_search(
        workload, noc_arch, "booksim",
        valid_variants=_NOC_VARIANTS, metric="latency_cycles", minimize=True, seed=1,
    )
    sequential_elapsed = time.monotonic() - sequential_start

    print(
        f"\nconcurrent dispatch_wall_clock_s={concurrent_report.dispatch_wall_clock_s:.2f}s, "
        f"sequential_elapsed={sequential_elapsed:.2f}s, "
        f"speedup={sequential_elapsed / concurrent_report.dispatch_wall_clock_s:.2f}x"
    )
    # A real margin, not a coin flip: concurrent dispatch should meaningfully beat the sequential
    # sum of the same three searches, not just possibly edge it out due to noise.
    assert concurrent_report.dispatch_wall_clock_s < sequential_elapsed * 0.8


def test_noc_winner_is_reported_but_not_merged_into_the_composite(
    workload, compute_memory_arch, noc_arch
):
    """The honest structural limitation this module's docstring names: no evaluator here spans
    both a compute+memory hierarchy and a real NoC block, so the NoC winner stays a separate,
    independently-grounded result, never silently folded into `composite_result`.
    """
    report = flux_agentic_multi_axis_dse(
        workload, compute_memory_arch, noc_arch, "zigzag", "booksim",
        valid_widths=_VALID_WIDTHS, memory_level="gbuf", valid_sizes_kb=_VALID_SIZES_KB,
        valid_noc_variants=_NOC_VARIANTS, composite_metric="energy_pj", seed=0,
    )
    assert report.noc_report.best_result is not None
    assert report.noc_report.best_result.metrics["latency_cycles"].value > 0
    # The composite result is a real ZigZag evaluation of the compute+memory arch only — genuinely
    # distinct provenance from the NoC search's own real Booksim2 evaluation, never merged.
    assert report.composite_result.provenance.evaluator.startswith("zigzag")
    assert report.noc_report.best_result.provenance.evaluator.startswith("booksim")


def test_chia_remote_on_the_top_level_node_also_works(workload, compute_memory_arch, noc_arch):
    """The whole flow is itself a real `@ChiaFunction()`, so it can also be dispatched as one
    outer Ray task (nested remote dispatch — Ray supports this, docs/decisions.md D9's own
    `flux_search`/`parallel_screening` note already established it works) — a real, if
    secondary, capability check alongside the flow's own internal concurrency.
    """
    ref = flux_agentic_multi_axis_dse.chia_remote(
        workload, compute_memory_arch, noc_arch, "zigzag", "booksim",
        valid_widths=_VALID_WIDTHS, memory_level="gbuf", valid_sizes_kb=_VALID_SIZES_KB,
        valid_noc_variants=_NOC_VARIANTS, composite_metric="energy_pj", seed=0,
    )
    assert isinstance(ref, ray.ObjectRef)
    report = get(ref)
    assert report.composite_result.metrics["energy_pj"].value == pytest.approx(
        _KNOWN_JOINT_OPTIMUM_ENERGY_PJ
    )
