"""Runs the real, compiled SystemC coarse-grain model through the Flux Evaluator ABI. Requires
`g++` and `libsystemc-dev` (already present in this environment; no nix devShell gate the way
`evaluators/rtl`'s Verilator requirement has, since SystemC doesn't need Docker or Verilator).

Mirrors `test_rtl_adapter_live.py`'s test structure closely (same workload/architecture pairs,
same kind of validation checks) since both adapters target the identical `mac_array` design —
plus one test neither adapter's own suite can express alone:
`test_agrees_exactly_with_the_real_rtl_evaluator`, which runs the SAME candidate through both
real evaluators and checks they agree — the actual point of a coarse-grain pre-check rung.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_rtl import RTLEvaluator
from flux_evaluator_systemc import NotExpressibleError, SystemCEvaluator

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D_V1 = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"  # X=8
SIMPLE_NPU_1D_V3 = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v3.yaml"  # X=16


@pytest.fixture(scope="module")
def evaluator() -> SystemCEvaluator:
    return SystemCEvaluator()


def test_gemm_workload_evaluates_through_real_systemc_with_default_lanes(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch=None, mapping=None)

    result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))

    # Same real number test_rtl_adapter_live.py pins for the identical shape — proven exact by
    # the closed-form derivation in evaluators/systemc/README.md, not a coincidence.
    assert result.metrics["latency_cycles"].value == pytest.approx(529.0)
    assert result.metrics["latency_cycles"].method == Method.SIMULATED
    assert result.metrics["latency_cycles"].ci_low == result.metrics["latency_cycles"].ci_high

    assert result.validity.ok is True
    assert result.validity.checker_version == "systemc-coarse-self-check-v0.1"
    assert result.provenance.evaluator == "systemc-coarse@mac_array-v0.1"
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)
    assert result.escalation.next_rung == "rtl"


def test_translated_architecture_evaluates_through_real_systemc(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(SIMPLE_NPU_1D_V1)
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))

    assert result.metrics["latency_cycles"].value == pytest.approx(529.0)
    assert result.provenance.inputs["accelerator"] == f"translated:{flux_ir.content_hash(arch)}"


def test_wider_architecture_gives_a_different_real_result(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch_8 = flux_ir.load_document(SIMPLE_NPU_1D_V1)
    arch_16 = flux_ir.load_document(SIMPLE_NPU_1D_V3)

    result_8 = evaluator.evaluate(
        Candidate(workload=workload, arch=arch_8, mapping=None), Budget(), frozenset({"latency_cycles"})
    )
    result_16 = evaluator.evaluate(
        Candidate(workload=workload, arch=arch_16, mapping=None), Budget(), frozenset({"latency_cycles"})
    )

    assert result_16.metrics["latency_cycles"].value < result_8.metrics["latency_cycles"].value
    assert result_16.validity.ok is True


def test_non_einsum_workload_raises_not_expressible(evaluator):
    dma_workload = flux_ir.load_document(FLUX_ROOT / "core/ir/workload/examples/soc-dma-desc-fetch.yaml")
    candidate = Candidate(workload=dma_workload, arch=None, mapping=None)

    with pytest.raises(NotExpressibleError):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


def test_explicit_mapping_is_rejected(evaluator):
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    candidate = Candidate(workload=workload, arch=None, mapping={"id": "some-mapping"})

    with pytest.raises(NotExpressibleError, match="does not translate Mapping IR"):
        evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


def test_2d_architecture_is_rejected():
    """architecture_ir_to_lanes is evaluators/rtl's own function, reused directly — this just
    confirms the reuse actually wires the same validation (its NotExpressibleError, a ValueError
    subclass like this package's own) rather than silently accepting something it shouldn't."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch_2d = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-v1.yaml")
    candidate = Candidate(workload=workload, arch=arch_2d, mapping=None)

    with pytest.raises(ValueError, match="single spatial dimension"):
        SystemCEvaluator().evaluate(candidate, Budget(), frozenset({"latency_cycles"}))


@pytest.mark.parametrize(
    "arch_path", ["simple-npu-1d-v1.yaml", "simple-npu-1d-v2.yaml", "simple-npu-1d-v3.yaml"],
)
def test_agrees_exactly_with_the_real_rtl_evaluator(evaluator, arch_path):
    """The actual point of this rung: a real coarse-grain simulation reaching the same answer
    as a real cycle-accurate one, across every array width this repo's corpus has an RTL
    ground-truth measurement for — not just the one width the other tests happen to pin."""
    workload = flux_ir.load_document(GEMM_WORKLOAD)
    arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples" / arch_path)
    candidate = Candidate(workload=workload, arch=arch, mapping=None)

    systemc_result = evaluator.evaluate(candidate, Budget(), frozenset({"latency_cycles"}))
    rtl_result = RTLEvaluator().evaluate(candidate, Budget(), frozenset({"latency_cycles"}))

    assert systemc_result.metrics["latency_cycles"].value == rtl_result.metrics["latency_cycles"].value
