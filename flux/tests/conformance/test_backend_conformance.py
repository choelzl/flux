"""The conformance suite (docs/architecture.md): every backend interprets the IR the same way as
the reference, or fails loudly on what it cannot express.

One shared corpus (every workload x every architecture example) and one test function, run
against every backend in `_BACKENDS`. A new backend adds an entry there and its column of
`EXPECTED`. `EXPECTED` was recorded by running every combination, not derived from the code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate

FLUX_ROOT = Path(__file__).resolve().parents[2]
WORKLOAD_DIR = FLUX_ROOT / "core/ir/workload/examples"
ARCH_DIR = FLUX_ROOT / "core/ir/architecture/examples"

WORKLOADS = ["llama3-8b-decode-layer0", "soc-dma-desc-fetch", "mlp-gemm0", "mlp-ffn0"]
ARCHITECTURES = ["generic-riscv-soc-v1", "my-npu-v3", "simple-npu-1d-v1", "simple-npu-v1"]


def _zigzag_backend():
    from flux_evaluator_zigzag import ZigZagEvaluator
    from flux_evaluator_zigzag.errors import NotExpressibleError

    return ZigZagEvaluator(), NotExpressibleError


def _timeloop_backend():
    from flux_evaluator_timeloop import TimeloopEvaluator
    from flux_evaluator_timeloop.errors import NotExpressibleError

    return TimeloopEvaluator(), NotExpressibleError


_BACKENDS = {"zigzag": _zigzag_backend, "timeloop": _timeloop_backend}

# (backend, workload, architecture) -> expected outcome.
# "fail"  = evaluate() must raise that backend's NotExpressibleError.
# a float = evaluate() must succeed and report exactly that latency_cycles value.
#
# Every combination defaults to "fail"; each success below is an explicit exception.
EXPECTED: dict[tuple[str, str, str], Any] = {
    (backend, workload, arch): "fail"
    for backend in _BACKENDS
    for workload in WORKLOADS
    for arch in ARCHITECTURES
}
EXPECTED[("zigzag", "mlp-gemm0", "simple-npu-1d-v1")] = 1554.0
EXPECTED[("zigzag", "mlp-gemm0", "simple-npu-v1")] = 210.0
EXPECTED[("timeloop", "mlp-gemm0", "simple-npu-1d-v1")] = 512.0
# 2-D (D215): 4096 MACs over the 8x8 array at 100% utilisation, the compute roofline.
EXPECTED[("timeloop", "mlp-gemm0", "simple-npu-v1")] = 64.0

# mlp-ffn0 (D59, 2 layers): ZigZag handles multi-layer workloads on both NPU-shaped architectures.
EXPECTED[("zigzag", "mlp-ffn0", "simple-npu-1d-v1")] = 1560.0
EXPECTED[("zigzag", "mlp-ffn0", "simple-npu-v1")] = 208.0
# Timeloop runs one invocation per layer and sums them (D62): 256 + 256 = 512 cycles. It fails
# on generic-riscv-soc-v1 (no compute dims) and my-npu-v3 (3-D, no third mesh axis).
EXPECTED[("timeloop", "mlp-ffn0", "simple-npu-1d-v1")] = 512.0
# Two layers on the 8x8 array: 32 + 32 = 64.
EXPECTED[("timeloop", "mlp-ffn0", "simple-npu-v1")] = 64.0


@pytest.fixture(scope="module")
def workload_docs() -> dict[str, dict]:
    return {w: flux_ir.load_document(WORKLOAD_DIR / f"{w}.yaml") for w in WORKLOADS}


@pytest.fixture(scope="module")
def arch_docs() -> dict[str, dict]:
    return {a: flux_ir.load_document(ARCH_DIR / f"{a}.yaml") for a in ARCHITECTURES}


@pytest.mark.parametrize("backend_name", sorted(_BACKENDS))
@pytest.mark.parametrize("workload_name", WORKLOADS)
@pytest.mark.parametrize("arch_name", ARCHITECTURES)
def test_conformance(backend_name, workload_name, arch_name, workload_docs, arch_docs):
    evaluator, not_expressible_error = _BACKENDS[backend_name]()
    workload = workload_docs[workload_name]
    arch = arch_docs[arch_name]
    candidate = Candidate(workload=workload, arch=arch, mapping=None)
    expected = EXPECTED[(backend_name, workload_name, arch_name)]

    if expected == "fail":
        with pytest.raises(not_expressible_error):
            evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
        return

    result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
    assert result.metrics["latency_cycles"].value == pytest.approx(expected)
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)


def test_backends_that_both_succeed_on_the_same_pair_agree_on_what_they_were_given(
    workload_docs, arch_docs
):
    """Where several backends succeed on the same (workload, architecture) pair, their provenance
    reports the exact same content hash.
    """
    successes_by_pair: dict[tuple[str, str], list[str]] = {}
    for (backend, workload, arch), outcome in EXPECTED.items():
        if outcome != "fail":
            successes_by_pair.setdefault((workload, arch), []).append(backend)

    shared_pairs = {pair: backends for pair, backends in successes_by_pair.items() if len(backends) > 1}
    assert shared_pairs, "expected at least one (workload, architecture) pair with 2+ passing backends"

    for (workload_name, arch_name), backend_names in shared_pairs.items():
        workload = workload_docs[workload_name]
        arch = arch_docs[arch_name]
        candidate = Candidate(workload=workload, arch=arch, mapping=None)
        expected_workload_hash = flux_ir.content_hash(workload)
        expected_arch_hash = flux_ir.content_hash(arch)

        for backend_name in backend_names:
            evaluator, _ = _BACKENDS[backend_name]()
            result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
            assert result.provenance.inputs["workload_hash"] == expected_workload_hash
            assert result.provenance.inputs["accelerator"] == f"translated:{expected_arch_hash}"
