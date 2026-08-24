"""Runs real CACTI 7 through the Flux Evaluator ABI (docs/decisions.md D35/D36) — via CHIA's own
`chia.vlsi.sram_cacti.run_cacti`, the first evaluator in this repo adapted *through* an existing
CHIA tool integration rather than wrapping the external tool directly. Requires `git`, `g++`,
`make` on `PATH` (no extra nix package needed, unlike booksim/noxim — confirmed by actually
building CACTI with a plain system toolchain).

Pinned numbers come from a real, from-scratch build+run against
`ir/architecture/examples/simple-npu-1d-v1.yaml`'s `gbuf` level (512 KiB), with an explicit
`word_width_bits: 128` added (that field doesn't exist on the real example — CACTI needs it,
`size_kb` alone doesn't determine depth, see `architecture_translator.py`) — run once and
recorded, matching every other adapter's "real numbers, pinned so a future regression is caught"
convention.
"""

from __future__ import annotations

import copy
from pathlib import Path

import flux_ir
import pytest
from flux_evaluator_abi import Budget, Candidate, Method
from flux_evaluator_cacti import CactiEvaluator, NotExpressibleError

FLUX_ROOT = Path(__file__).resolve().parents[2]
GEMM_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
SIMPLE_NPU_1D = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml"


def _gbuf_only_arch(word_width_bits: int = 128) -> dict:
    """Extracts simple-npu-1d-v1.yaml's real gbuf level (512 KiB) into a standalone,
    single-memory-node arch dict — CactiEvaluator's v0.1 contract (see
    architecture_translator.py's module docstring for why: CACTI characterizes one macro, not a
    whole hierarchy), with the one field this repo's real examples don't carry yet added
    explicitly, not silently defaulted.
    """
    full_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    gbuf = copy.deepcopy(next(n for n in full_arch["hierarchy"] if n["level"] == "gbuf"))
    gbuf["attrs"]["word_width_bits"] = word_width_bits
    return {
        "schema_version": full_arch["schema_version"],
        "id": f"{full_arch['id']}-gbuf-only",
        "tech": full_arch["tech"],
        "hierarchy": [gbuf],
    }


@pytest.fixture(scope="module")
def evaluator() -> CactiEvaluator:
    return CactiEvaluator()


@pytest.fixture(scope="module")
def workload():
    return flux_ir.load_document(GEMM_WORKLOAD)


def test_gbuf_sram_characterizes_through_real_cacti(evaluator, workload):
    arch = _gbuf_only_arch()
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(),
        frozenset({"area_mm2", "energy_pj", "power_w"}),
    )

    assert result.metrics["area_mm2"].value > 0
    assert result.metrics["energy_pj"].value > 0
    assert result.metrics["power_w"].value > 0
    for m in result.metrics.values():
        assert m.method == Method.SIMULATED
        assert m.ci_low == m.ci_high
    assert result.bottleneck.per_level_utilisation["access_time_ns"] > 0
    assert result.bottleneck.limiter.value == "memory"
    assert result.provenance.evaluator == "cacti7@real"
    assert result.provenance.inputs["sram"] == "gbuf-32768x128b"
    assert result.provenance.inputs["workload_hash"] == flux_ir.content_hash(workload)


def test_gbuf_sram_matches_pinned_real_values(evaluator, workload):
    """Pinned so a future translator/subprocess regression is caught — real numbers from a
    from-scratch build+run of this exact spec (512 KiB, 128-bit word, 28nm), docs/decisions.md
    D36."""
    arch = _gbuf_only_arch()
    result = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(),
        frozenset({"area_mm2", "energy_pj", "power_w"}),
    )
    assert result.metrics["area_mm2"].value == pytest.approx(0.527745648101, rel=1e-6)
    assert result.metrics["energy_pj"].value == pytest.approx(88.4356, rel=1e-6)
    assert result.metrics["power_w"].value == pytest.approx(0.206906, rel=1e-6)


def test_wider_word_width_gives_a_different_real_characterization(evaluator, workload):
    """A real, physically-meaningful sensitivity check: the same 512 KiB capacity at a different
    word width is a genuinely different physical macro (different depth), not the same result
    relabeled — checked via real CACTI, not assumed from the formula alone."""
    narrow = evaluator.evaluate(
        Candidate(workload=workload, arch=_gbuf_only_arch(word_width_bits=128), mapping=None),
        Budget(), frozenset({"area_mm2"}),
    )
    wide = evaluator.evaluate(
        Candidate(workload=workload, arch=_gbuf_only_arch(word_width_bits=256), mapping=None),
        Budget(), frozenset({"area_mm2"}),
    )
    assert narrow.metrics["area_mm2"].value != wide.metrics["area_mm2"].value


def test_technology_above_90nm_is_rejected_before_reaching_cacti(evaluator, workload):
    arch = _gbuf_only_arch()
    arch["tech"] = {"node": "n130", "pdk_class": "open"}
    with pytest.raises(NotExpressibleError, match="90nm ceiling"):
        evaluator.evaluate(
            Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset({"area_mm2"})
        )


def test_none_architecture_is_rejected(evaluator, workload):
    with pytest.raises(NotExpressibleError, match="requires an inline Architecture IR"):
        evaluator.evaluate(Candidate(workload=workload, arch=None, mapping=None), Budget(), frozenset({"area_mm2"}))


def test_explicit_mapping_is_rejected(evaluator, workload):
    arch = _gbuf_only_arch()
    with pytest.raises(NotExpressibleError, match="does not use Mapping IR"):
        evaluator.evaluate(
            Candidate(workload=workload, arch=arch, mapping={"id": "some-mapping"}), Budget(), frozenset({"area_mm2"})
        )


def test_full_multi_level_hierarchy_is_rejected_needs_exactly_one_memory_node(evaluator, workload):
    """CactiEvaluator's real, honest scope limit: the repo's own full architecture examples (with
    both dram AND gbuf memory-class nodes) aren't directly characterizable — a caller must extract
    the single level being characterized first, the same way this test file's own
    `_gbuf_only_arch` helper does."""
    full_arch = flux_ir.load_document(SIMPLE_NPU_1D)
    with pytest.raises(NotExpressibleError, match="exactly one"):
        evaluator.evaluate(
            Candidate(workload=workload, arch=full_arch, mapping=None), Budget(), frozenset({"area_mm2"})
        )
