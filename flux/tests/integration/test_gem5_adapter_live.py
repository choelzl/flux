"""Runs real gem5 through the Flux Evaluator ABI (docs/decisions.md D35/D38) — via CHIA's own
`chia.simulators.gem5.Gem5Node`, the second evaluator here (after `evaluators/cacti`) adapted
through an existing CHIA tool integration rather than wrapping the external tool directly.

Requires `git`, `g++`, `make`, `scons` on `PATH`, plus a working `python3.12-config` at
`/usr/bin/python3.12-config` (a real, sandbox-specific build gotcha found and documented in
`evaluators/gem5/README.md` — a second, broken Python 3.12 install earlier on this sandbox's PATH
breaks gem5's build-time Python-embedding step otherwise). The real build itself is
substantial — tens of minutes even at the empirically-found-safe `-j8` (gem5's own default job
count hit a real GCC 13 internal-compiler-error on this 64-core machine at higher parallelism,
see `adapter.py`'s module docstring) — the largest build cost of any adapter in this repo,
confirmed, not estimated.

Pinned numbers come from a real, from-scratch build+run of gem5's own bundled RISC-V "hello
world" test binary (`tests/test-progs/hello/bin/riscv/linux/hello`) on a single-core `rv64gc`
`TimingSimpleCPU` at 1.2GHz — fully deterministic (no LLM, no randomness anywhere in this path)
*given the gem5 clone itself is pinned to a fixed tag* (`adapter.py`'s `_GEM5_REF`), so a fresh
build+run reproduces the exact same numbers a prior verification run already found, not merely
something close. An earlier version of this pin was taken from an unpinned clone of gem5's moving
default branch and genuinely failed to reproduce weeks later — see docs/decisions.md D38.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_gem5 import Gem5Evaluator, NotExpressibleError

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
GENERIC_RISCV_SOC = FLUX_ROOT / "core/ir/architecture/examples/generic-riscv-soc-v1.yaml"


def _cpu_only_arch(cores: int = 1, freq_ghz: float = 1.2) -> dict:
    """Extracts generic-riscv-soc-v1.yaml's real cpu0 node into a standalone, single-compute-node
    arch dict — Gem5Evaluator's v0.1 contract (see architecture_translator.py's module docstring:
    gem5 characterizes one CPU config at a time, same "the arch dict *is* the thing being
    evaluated" shape evaluators/booksim/noxim/cacti already use).
    """
    full_arch = flux_ir.load_document(GENERIC_RISCV_SOC)
    cpu = next(n for n in full_arch["hierarchy"] if n["level"] == "cpu0")
    return {
        "schema_version": full_arch["schema_version"],
        "id": f"{full_arch['id']}-cpu-only",
        "tech": full_arch["tech"],
        "hierarchy": [{
            "level": "cpu0", "class": "compute",
            "attrs": {"isa": cpu["attrs"]["isa"], "cores": cores, "freq_ghz": freq_ghz},
        }],
    }


@pytest.fixture(scope="module")
def evaluator() -> Gem5Evaluator:
    return Gem5Evaluator()


@pytest.fixture(scope="module")
def workload():
    return flux_ir.load_document(GEMM_WORKLOAD)


def test_single_core_riscv_runs_through_real_gem5(evaluator, workload):
    arch = _cpu_only_arch(cores=1, freq_ghz=1.2)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )

    assert result.metrics["latency_cycles"].value > 0
    assert result.metrics["latency_cycles"].method == Method.SIMULATED
    assert result.metrics["latency_cycles"].ci_low == result.metrics["latency_cycles"].ci_high
    assert result.bottleneck.per_level_utilisation["sim_insts"] > 0
    assert result.bottleneck.limiter.value == "compute"
    assert result.provenance.evaluator == "gem5@real"
    assert result.provenance.inputs["benchmark"] == "tests/test-progs/hello/bin/riscv/linux/hello"
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)


def test_matches_pinned_real_values_from_a_prior_verified_run(evaluator, workload):
    """Pinned so a future translator/subprocess regression is caught — fully deterministic (no
    LLM, no randomness) *once the gem5 clone itself is pinned to a fixed tag* (docs/decisions.md
    D38: an earlier version of this test pinned values from an unpinned `git clone --depth 1` of
    gem5's moving default branch, and genuinely failed to reproduce weeks later when upstream
    moved — these values are from a fresh build+run at the exact pinned `_GEM5_REF` tag, confirmed
    twice: once via an unpinned clone that happened to already be at this tag, once via the
    explicit pin, both producing exactly these numbers)."""
    arch = _cpu_only_arch(cores=1, freq_ghz=1.2)
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"})
    )
    assert result.metrics["latency_cycles"].value == pytest.approx(550513.0)
    assert result.bottleneck.per_level_utilisation["sim_insts"] == pytest.approx(5840.0)
    assert result.bottleneck.per_level_utilisation["ipc"] == pytest.approx(0.010608287179412658)


def test_none_architecture_is_rejected(evaluator, workload):
    with pytest.raises(NotExpressibleError, match="requires an inline Architecture IR"):
        evaluator.evaluate(Candidate(workload=workload, arch=None, mapping=None), Budget(), frozenset({"latency_cycles"}))


def test_explicit_mapping_is_rejected(evaluator, workload):
    arch = _cpu_only_arch()
    with pytest.raises(NotExpressibleError, match="does not use Mapping IR"):
        evaluator.evaluate(
            Candidate(workload=workload, arch=arch, mapping={"id": "some-mapping"}), Budget(), frozenset({"latency_cycles"})
        )


def test_non_riscv_isa_is_rejected_before_reaching_gem5(evaluator, workload):
    arch = _cpu_only_arch()
    arch["hierarchy"][0]["attrs"]["isa"] = "x86_64"
    with pytest.raises(NotExpressibleError, match="isn't RISC-V"):
        evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"}))


def test_full_generic_riscv_soc_is_rejected_its_cpu0_node_has_four_cores(evaluator, workload):
    """Gem5Evaluator's real, honest scope limit: `generic-riscv-soc-v1.yaml`'s `cpu0` node is the
    repo's only real class=='compute' node here, so this arch actually satisfies the "exactly one
    compute node" structural rule — but that node carries `cores: 4`, and evaluators/gem5 v0.1
    only supports cores=1 (a real, verified finding, not an arbitrary restriction — see
    architecture_translator.py's module docstring, docs/decisions.md D38: a fresh, real
    `--num-cpus 4` run against this exact node completed simulation but failed CHIA's own stats
    parsing). A caller must extract a single-core variant first, the same way this test file's own
    `_cpu_only_arch` helper does."""
    full_arch = flux_ir.load_document(GENERIC_RISCV_SOC)
    with pytest.raises(NotExpressibleError, match="cores=4"):
        evaluator.evaluate(
            Candidate(workload=workload, arch=full_arch, mapping=None), Budget(), frozenset({"latency_cycles"})
        )


def test_two_compute_nodes_is_rejected_needs_exactly_one(evaluator, workload):
    """The real "exactly one class=='compute' node" structural rule, exercised directly — no repo
    example actually has two compute nodes (generic-riscv-soc-v1.yaml has one, with cores=4, see
    the test above), so this constructs one synthetically. Rejected by the translator before gem5
    is ever invoked, so this doesn't pay the real build/run cost."""
    arch = _cpu_only_arch()
    second_cpu = dict(arch["hierarchy"][0], level="cpu1")
    arch["hierarchy"].append(second_cpu)
    with pytest.raises(NotExpressibleError, match="exactly one"):
        evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"latency_cycles"}))
