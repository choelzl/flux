"""Real end-to-end native evaluation (docs/decisions.md D75): builds the real `flux-core` Rust
crate via `ensure_native_extension`, runs `NativeEvaluator` against real Architecture/Workload IR
documents, and re-measures — honestly, not just asserted from prose — the real throughput
comparison this decision's own record is built on: a native, in-repo cost model does clear
docs/architecture.md's stated ">=10^5 dense-layer mapping evaluations/second/core" target, but for
a computation this cheap, is not meaningfully faster than the equivalent pure Python.
"""

from __future__ import annotations

import importlib.util
import json
import time

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate
from flux_evaluator_native import NativeEvaluator, NotExpressibleError
from flux_evaluator_native.build import ensure_native_extension

FLUX_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
ARCH = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"
WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"

# Phase 3's own stated exit criterion (docs/architecture.md's Performance-engineering table).
_TARGET_EVALS_PER_SEC = 1e5


@pytest.fixture(scope="module")
def native_module():
    binary_path = ensure_native_extension(timeout_s=300.0)
    spec = importlib.util.spec_from_file_location("flux_core", binary_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def evaluator() -> NativeEvaluator:
    return NativeEvaluator(timeout_s=300.0)


def test_matches_the_already_established_512_cycle_compute_bound(evaluator):
    """The exact real bound docs/phase1-exit-criterion-report.md and validity/roofline.py both
    already independently established for this exact workload/architecture pair — real Timeloop's
    own mapper hits it exactly; ZigZag (1554) and real Verilator RTL (529) both clear it, the
    physically correct direction.
    """
    workload = flux_ir.load_document(WORKLOAD)
    arch = flux_ir.load_document(ARCH)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset(),
    )
    assert result.metrics["latency_cycles"].value == 512.0
    assert result.provenance.evaluator == "flux-core@0.1"


def test_scales_inversely_with_declared_lane_count(evaluator):
    import copy

    workload = flux_ir.load_document(WORKLOAD)
    base_arch = flux_ir.load_document(ARCH)

    def _cycles_for(lanes: int) -> float:
        arch = copy.deepcopy(base_arch)
        arch["hierarchy"][2]["attrs"]["dims"] = {"X": lanes}
        result = evaluator.evaluate(
            Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset(),
        )
        return result.metrics["latency_cycles"].value

    assert _cycles_for(16) == 256.0
    assert _cycles_for(4) == 1024.0


def test_an_unexpressible_multi_op_workload_is_rejected(evaluator):
    workload = flux_ir.load_document(WORKLOAD)
    workload["ops"] = workload["ops"] * 2  # two einsum ops — out of this evaluator's v0.1 scope
    arch = flux_ir.load_document(ARCH)
    with pytest.raises(NotExpressibleError):
        evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset())


def test_real_native_vs_python_throughput_for_the_numeric_hot_loop(native_module):
    """The honest finding this decision's own record names explicitly: for a computation this
    cheap (a single division), the real, measured native-Rust hot loop clears the stated
    >=10^5 evals/s/core target comfortably, but is not meaningfully faster than the equivalent
    pure-Python loop — the PyO3 FFI marshaling cost dominates, not the arithmetic. This is a real
    regression check on that finding (both numbers must clear the target; neither number is
    asserted to beat the other, since which one wins run-to-run is noise-level close).
    """
    n = 200_000
    lanes = [8] * n

    t0 = time.perf_counter()
    native_out = native_module.roofline_latency_cycles_for_lane_sweep(4096, lanes)
    native_elapsed = time.perf_counter() - t0
    native_evals_per_s = n / native_elapsed

    t0 = time.perf_counter()
    python_out = [4096 / l for l in lanes]
    python_elapsed = time.perf_counter() - t0
    python_evals_per_s = n / python_elapsed

    assert native_out[0] == 512.0
    assert python_out[0] == 512.0
    assert native_evals_per_s > _TARGET_EVALS_PER_SEC
    assert python_evals_per_s > _TARGET_EVALS_PER_SEC


def test_real_native_json_batch_reproduces_the_single_call_result(native_module):
    workload_json = json.dumps(flux_ir.load_document(WORKLOAD))
    arch_json = json.dumps(flux_ir.load_document(ARCH))
    single = native_module.roofline_latency_cycles(workload_json, arch_json)
    batch = native_module.roofline_latency_cycles_batch(workload_json, [arch_json, arch_json])
    assert batch == [single, single]


def test_chia_node_reaches_native_through_the_generic_flux_evaluate_registry():
    """No dedicated CHIA node was added for the native evaluator (docs/decisions.md D75) — same
    shape evaluators/gem5/thermal/dramsim3 already established: reachable through the generic
    flux_evaluate node once "native" is registered in flux_cli.registry.
    """
    from flux_chia_nodes import flux_evaluate

    workload = flux_ir.load_document(WORKLOAD)
    arch = flux_ir.load_document(ARCH)
    result = flux_evaluate("native", workload, arch, None, metrics=["latency_cycles"])
    assert result.metrics["latency_cycles"].value == 512.0
    assert result.provenance.evaluator == "flux-core@0.1"
