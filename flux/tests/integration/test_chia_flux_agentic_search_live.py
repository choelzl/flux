"""`flux_agentic_mapping_search`/`flux_agentic_architecture_search`/`flux_agentic_noc_search`/
`flux_agentic_memory_search`/`flux_agentic_joint_search` against real local Ollama
(`qwen2.5-coder:7b`, no API credentials — docs/decisions.md D9) and real evaluators (ZigZag,
Booksim2) dispatched as real CHIA `@ChiaFunction()` nodes (docs/decisions.md D17/D26/D27/D28) —
the CHIA-specific dispatch surface for `search/agentic/`'s five `Strategy` implementations
(D12/D13/D14/D16/D26/D28), verified the same way `flux_evaluate`/`flux_search` already are: a
local (in-process) call, and at least one real `.chia_remote()` dispatch to a separate Ray worker
process (not assumed to work just because it's decorated).

Each test uses a reduced candidate/iteration count relative to the full live tests in
`tests/integration/test_search_agentic_*_live.py` (which already prove convergence to the real
proven optimum for each axis) — this file's job is proving the CHIA dispatch layer itself works,
not re-proving each strategy's search quality.

Requires the real `chia` package, `openai` (for `chia.models.ollama.OllamaLLM`),
`flux-evaluator-zigzag`, and `flux-evaluator-booksim` (needs `flex`/`bison`).
"""

from __future__ import annotations

import logging
from pathlib import Path

import flux_ir
import pytest

import _helpers

# Guard added by the D246 review: this file drove the nightly sweep red on every
# runner without an Ollama server — an unguarded failure, not a skip.
pytestmark = _helpers.requires_ollama
import ray
from chia.base.ChiaFunction import get
from flux_chia_nodes import (
    flux_agentic_architecture_search,
    flux_agentic_joint_search,
    flux_agentic_mapping_search,
    flux_agentic_memory_search,
    flux_agentic_noc_search,
)

logging.getLogger("zigzag").setLevel(logging.WARNING)

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
NOC_MESH_2D = FLUX_ROOT / "core/ir/architecture/examples/noc-mesh-2d-v1.yaml"


def test_flux_agentic_mapping_search_local_call():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D)
    report = flux_agentic_mapping_search(
        workload, arch, "zigzag", for_op="mlp.gemm0", max_iterations=3, seed=0,
    )
    assert report.iterations == 3
    assert report.best is not None
    assert report.best_result.metrics["latency_cycles"].value > 0


def test_flux_agentic_architecture_search_local_call_finds_the_proven_optimum():
    """max_iterations=4 covers the full candidate set, so this is the same deterministic
    argument test_search_agentic_architecture_live.py uses: guaranteed to find 263.0 cycles.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    report = flux_agentic_architecture_search(
        workload, base_arch, "zigzag", valid_widths=[4, 8, 16, 32], max_iterations=4, seed=0,
    )
    assert report.iterations == 4
    assert report.best.width == 32
    assert report.best_result.metrics["latency_cycles"].value == pytest.approx(263.0)


def test_flux_agentic_architecture_search_dispatches_through_a_real_ray_task():
    """The harder case, same discipline flux_search's own live test uses: a real remote dispatch,
    not just a decorated function that happens to also work locally.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    ref = flux_agentic_architecture_search.chia_remote(
        workload, base_arch, "zigzag", valid_widths=[4, 8, 16, 32], max_iterations=4, seed=0,
    )
    assert isinstance(ref, ray.ObjectRef)
    report = get(ref)
    assert report.best.width == 32
    assert report.best_result.metrics["latency_cycles"].value == pytest.approx(263.0)


def test_flux_agentic_noc_search_local_call():
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(NOC_MESH_2D)
    report = flux_agentic_noc_search(
        workload, base_arch, "booksim",
        valid_variants=[("mesh", [8, 8]), ("mesh", [4, 4, 4]), ("torus", [4, 4, 4])],
        max_iterations=3, seed=0,
    )
    assert report.iterations == 3
    assert report.best is not None
    assert report.best_result.metrics["latency_cycles"].value > 0


def test_flux_agentic_memory_search_local_call_finds_the_proven_optimum():
    """max_iterations=4 covers the full candidate set, so this is the same deterministic
    argument test_search_agentic_memory_live.py uses: guaranteed to find the smallest feasible
    size (1.25 KiB), not the numerically smallest tried (1.0 KiB, which is infeasible).
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    report = flux_agentic_memory_search(
        workload, base_arch, "zigzag",
        level="gbuf", valid_sizes_kb=[1.0, 1.25, 2.0, 64.0], max_iterations=4, seed=0,
    )
    assert report.iterations == 4
    assert report.skipped_infeasible == 1
    assert report.best is not None
    assert report.best.size_kb == 1.25
    assert report.best_result.metrics["energy_pj"].value == pytest.approx(1116618.0081255918)


def test_flux_agentic_joint_search_local_call_finds_the_proven_optimum():
    """max_iterations=6 covers the full 2x3 grid, so this is the same deterministic argument
    test_search_agentic_joint_live.py uses: guaranteed to find width=32/size_kb=1.25.
    """
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    base_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    report = flux_agentic_joint_search(
        workload, base_arch, "zigzag",
        level="gbuf", valid_widths=[4, 32], valid_sizes_kb=[1.0, 1.25, 64.0],
        max_iterations=6, seed=0,
    )
    assert report.iterations == 6
    assert report.skipped_infeasible == 2
    assert report.best is not None
    assert report.best.width == 32
    assert report.best.size_kb == 1.25
    assert report.best_result.metrics["energy_pj"].value == pytest.approx(193018.0081255918)
