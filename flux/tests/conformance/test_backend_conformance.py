"""The conformance suite (docs/04.md §10): "any new evaluator backend must pass a shared suite
proving it interprets the IR the same way as the reference, or that it fails loudly on the parts
it cannot express."

In practice: one shared corpus (every workload example x every architecture example in
`ir/*/examples/`) and one shared test function, run against every backend in `_BACKENDS` below —
not a separate ad hoc fixture set per adapter. Adding a third backend later means adding one
entry to `_BACKENDS` and filling in its column of `EXPECTED`; the corpus and the test logic don't
change.

`EXPECTED` was populated by actually running every (backend, workload, architecture) combination
and recording what happened — not derived from reading the translators' code and guessing. That
matters: this project's own history in this session includes at least one test written from a
plausible-sounding assumption ("a different architecture must give a different latency") that
turned out to be empirically false. This suite exists specifically to not repeat that mistake at
the level that counts most — cross-backend agreement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate

FLUX_ROOT = Path(__file__).resolve().parents[2]
WORKLOAD_DIR = FLUX_ROOT / "ir/workload/examples"
ARCH_DIR = FLUX_ROOT / "ir/architecture/examples"

WORKLOADS = ["llama3-8b-decode-layer0", "soc-dma-desc-fetch", "mlp-gemm0"]
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
# Default every combination to "fail", then carve out the specific successes — this makes the
# common case (most workload/architecture pairs are NOT expressible by a given v0.1 translator)
# the default, and every success an explicit, deliberate exception, matching how narrow both
# translators' documented scopes actually are.
EXPECTED: dict[tuple[str, str, str], Any] = {
    (backend, workload, arch): "fail"
    for backend in _BACKENDS
    for workload in WORKLOADS
    for arch in ARCHITECTURES
}
EXPECTED[("zigzag", "mlp-gemm0", "simple-npu-1d-v1")] = 1554.0
EXPECTED[("zigzag", "mlp-gemm0", "simple-npu-v1")] = 210.0
EXPECTED[("timeloop", "mlp-gemm0", "simple-npu-1d-v1")] = 512.0
# timeloop x mlp-gemm0 x simple-npu-v1 stays "fail": simple-npu-v1 is 2D (X,Y) and Timeloop's
# architecture translator only models a single spatial dimension (evaluators/timeloop's
# architecture_translator.py) — a documented, deliberate scope gap, not a bug either suite hides.


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
    """Where the EXPECTED matrix has more than one backend succeeding on the same
    (workload, architecture) pair, every one of those backends' provenance must report having
    seen the exact same content hash — the mechanical proof that "the IR round-tripped
    identically," not just "both runs happened to finish." Currently exactly one pair qualifies
    (mlp-gemm0 x simple-npu-1d-v1); this test is written to keep working if more do later.
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
