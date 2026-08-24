"""Real, end-to-end proof of the full Stream integration arc (docs/decisions.md D80-D82): a
genuine, hand-authored multi-core Flux Architecture IR document, translated by this repo's own
new code (not Stream's own bundled reference hardware), runs through real Stream via the real
`StreamEvaluator` Evaluator-ABI adapter — and is reachable through the generic `flux_evaluate`
CHIA node, the same "one definition, three surfaces" proof every other evaluator here gets.

Requires the real `stream`/`ortools`/`onnx` packages this repo's `flake.nix` provides.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_chia_nodes import flux_evaluate
from flux_evaluator_abi import Budget, Candidate, Limiter
from flux_evaluator_stream import NotExpressibleError, StreamEvaluator

FLUX_ROOT = Path(__file__).resolve().parents[2]
WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-gemm0.yaml"
DUAL_CORE_ARCH = FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-dual-core-v1.yaml"


@pytest.fixture(scope="module")
def evaluator() -> StreamEvaluator:
    return StreamEvaluator(timeout_s=250.0)


def test_real_dual_core_architecture_runs_through_real_stream(evaluator):
    """The real, decisive proof this whole three-decision arc (D80/D81/D82) was building toward:
    a genuine, from-scratch multi-core Flux Architecture IR document (two real compute cores,
    real inter-core links, a real off-chip DRAM core), translated by this repo's own new
    architecture_translator.py (reusing evaluators/zigzag's own existing per-core translator),
    running end to end through real Stream. Pinned to the exact real, deterministic number
    measured by hand before this test was written.
    """
    workload = flux_ir.load_document(WORKLOAD)
    arch = flux_ir.load_document(DUAL_CORE_ARCH)
    result = evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset())

    # Re-pinned at docs/decisions.md D253: the ONNX exporter now honours the Workload IR's
    # own declared `precision` instead of exporting every tensor as fp32, so this INT8
    # workload's tensors are a quarter the bytes they used to be and every latency below
    # dropped accordingly. The pre-D253 numbers were measured against a 4x overstated
    # working set, not against this workload.
    assert result.metrics["latency_cycles"].value == pytest.approx(908.0)  # was 1148.0 pre-D253
    assert result.provenance.evaluator == "stream-dse@real"
    assert result.provenance.inputs["backend"] == "ortools_highs"

    # Real bottleneck reporting (docs/decisions.md D84), not the earlier placeholder
    # Bottleneck(limiter=Limiter.DEPENDENCY) with no supporting data — the real numbers measured
    # by hand before this assertion was written.
    assert result.bottleneck.limiter == Limiter.COMPUTE
    util = result.bottleneck.per_level_utilisation
    assert util["compute_bound_cycles"] == pytest.approx(828.0)
    # Exactly a quarter of the pre-D253 320.0, while compute_bound_cycles above is unchanged at
    # 828.0 — the sharpest available confirmation that honouring declared precision changed the
    # DATA-MOVEMENT half of the cost and nothing else: 828 + 80 = 908, as 828 + 320 = 1148 was.
    assert util["transfer_bound_cycles"] == pytest.approx(80.0)  # was 320.0 pre-D253
    assert util["compute_cores_available"] == 2.0
    assert util["compute_cores_used"] == 2.0


def test_reachable_through_the_generic_flux_evaluate_chia_node():
    """No dedicated CHIA node was added for Stream — same shape evaluators/gem5/thermal/dramsim3/
    native already established: reachable through the generic flux_evaluate node once "stream" is
    registered in flux_cli.registry.
    """
    workload = flux_ir.load_document(WORKLOAD)
    arch = flux_ir.load_document(DUAL_CORE_ARCH)
    result = flux_evaluate("stream", workload, arch, None, metrics=["latency_cycles"])
    # Re-pinned at docs/decisions.md D253: the ONNX exporter now honours the Workload IR's
    # own declared `precision` instead of exporting every tensor as fp32, so this INT8
    # workload's tensors are a quarter the bytes they used to be and every latency below
    # dropped accordingly. The pre-D253 numbers were measured against a 4x overstated
    # working set, not against this workload.
    assert result.metrics["latency_cycles"].value == pytest.approx(908.0)  # was 1148.0 pre-D253
    assert result.provenance.evaluator == "stream-dse@real"


def test_an_architecture_with_no_multi_core_block_is_rejected(evaluator):
    workload = flux_ir.load_document(WORKLOAD)
    single_core_arch = flux_ir.load_document(FLUX_ROOT / "core/ir/architecture/examples/simple-npu-1d-v1.yaml")
    with pytest.raises(NotExpressibleError):
        evaluator.evaluate(Candidate(workload=workload, arch=single_core_arch, mapping=None), Budget(), frozenset())


def test_an_explicit_mapping_is_rejected(evaluator):
    workload = flux_ir.load_document(WORKLOAD)
    arch = flux_ir.load_document(DUAL_CORE_ARCH)
    with pytest.raises(NotExpressibleError, match="leave Candidate.mapping as None"):
        evaluator.evaluate(
            Candidate(workload=workload, arch=arch, mapping={"schema_version": "0.1.0", "id": "m", "for_op": "x"}),
            Budget(), frozenset(),
        )


# --- Real layer fusion (docs/decisions.md D103) ---

FFN_WORKLOAD = FLUX_ROOT / "core/ir/workload/examples/mlp-ffn0.yaml"


def _fusion_mapping(tile: int) -> dict:
    return {
        "schema_version": "0.1.0", "id": f"mlp/ffn0/fused-b{tile}", "for_op": "ffn.down",
        "operands": {},
        "fusion": {"group": ["ffn.down", "ffn.up"], "tile": {"B": tile}},
    }


def test_real_layer_fusion_changes_the_real_latency(evaluator):
    """The capability D80–D82's own README named as not-wired: Stream's `intra_core_tiling`,
    driven by the Flux Mapping IR's own `fusion` block (its first real consumer anywhere in this
    repo). Every number pinned from real Stream runs measured by hand before this test existed.

    Fusion really does reach Stream and really does move the number. Its SIGN, however, depends
    on the workload: at B=4 with correctly-costed INT8 tensors (docs/decisions.md D253) a
    tile-size-1 split is a small net LOSS (892.0 vs 888.0 unfused) — the per-tile overhead is no
    longer hidden behind 4x-overstated fp32 transfer costs. The pipelining win is real at larger
    batch, and `test_the_fusion_tile_space_is_really_non_monotone` below pins it there
    (B=16: 3288.0 unfused -> 3232.0 at tile 8). Both facts matter, which is why this test now
    pins that fusion *changes* the latency and leaves *which direction* to the axis-shape test.
    """
    workload = flux_ir.load_document(FFN_WORKLOAD)
    arch = flux_ir.load_document(DUAL_CORE_ARCH)

    unfused = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=None), Budget(), frozenset()
    )
    assert unfused.metrics["latency_cycles"].value == pytest.approx(888.0)  # was 1080.0 pre-D253

    fused = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=_fusion_mapping(1)), Budget(), frozenset()
    )
    assert fused.metrics["latency_cycles"].value == pytest.approx(892.0)  # was 976.0 pre-D253
    assert fused.metrics["latency_cycles"].value != unfused.metrics["latency_cycles"].value

    # Real bottleneck data survives the fused path (D84's mechanism, unchanged by D103).
    assert fused.bottleneck.per_level_utilisation["compute_bound_cycles"] > 0


def test_a_full_size_tile_reproduces_the_unfused_latency_exactly(evaluator):
    """Pins the semantics `fusion_translator.py` documents: `tile` is a tile SIZE, not a split
    count — tiling at the full bound (B=4) is the trivial no-op tiling, so it must return exactly
    the unfused number. A regression here means Stream's own tiling semantics changed.
    """
    workload = flux_ir.load_document(FFN_WORKLOAD)
    arch = flux_ir.load_document(DUAL_CORE_ARCH)
    full_tile = evaluator.evaluate(
        Candidate(workload=workload, arch=arch, mapping=_fusion_mapping(4)), Budget(), frozenset()
    )
    # Re-pinned at docs/decisions.md D253: the ONNX exporter now honours the Workload IR's
    # own declared `precision` instead of exporting every tensor as fp32, so this INT8
    # workload's tensors are a quarter the bytes they used to be and every latency below
    # dropped accordingly. The pre-D253 numbers were measured against a 4x overstated
    # working set, not against this workload.
    assert full_tile.metrics["latency_cycles"].value == pytest.approx(888.0)  # was 1080.0 pre-D253


def test_a_mapping_with_untranslatable_blocks_is_rejected_not_silently_ignored(evaluator):
    workload = flux_ir.load_document(FFN_WORKLOAD)
    arch = flux_ir.load_document(DUAL_CORE_ARCH)
    mapping = _fusion_mapping(2) | {"operands": {"I": [{"level": "gbuf", "loops": [{"dim": "B", "size": 4}]}]}}
    with pytest.raises(NotExpressibleError, match="Refusing to silently ignore"):
        evaluator.evaluate(Candidate(workload=workload, arch=arch, mapping=mapping), Budget(), frozenset())


# --- The fusion-tile search axis (docs/decisions.md D104) ---

FFN_B16_WORKLOAD = {
    "schema_version": "0.1.0", "id": "mlp/ffn-b16",
    "provenance": {"source": "handwritten", "importer": "flux-manual@0.1"},
    "tensors": [
        {"name": "I", "rank": ["B", "C"], "dtype": "int8"},
        {"name": "W0", "rank": ["C", "H"], "dtype": "int8"},
        {"name": "W1", "rank": ["H", "K"], "dtype": "int8"},
        {"name": "O", "rank": ["B", "K"], "dtype": "int16"},
    ],
    "ops": [
        {"id": "ffn.down", "kind": "einsum", "expr": "B C, C H -> B H",
         "bounds": {"B": 16, "C": 32, "H": 16}, "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8}},
        {"id": "ffn.up", "kind": "einsum", "expr": "B H, H K -> B K",
         "bounds": {"B": 16, "H": 16, "K": 32}, "precision": {"I": 8, "W": 8, "O": 16, "O_final": 8}},
    ],
}


def test_the_fusion_tile_space_is_really_non_monotone(evaluator):
    """The measurement that justifies searching this axis at all (docs/decisions.md D104): the
    optimum is INTERIOR (tile=8), and a badly-chosen tile (2) is genuinely *worse* than not
    fusing. Every number bit-identical across repeated real Stream runs before being pinned here.

    If this ever becomes monotone, the search axis is no longer earning its keep and D104's
    justification needs revisiting — hence pinning the shape, not just the winner.
    """
    arch = flux_ir.load_document(DUAL_CORE_ARCH)

    def latency(tile):
        mapping = None if tile is None else {
            "schema_version": "0.1.0", "id": f"t{tile}", "for_op": "ffn.down", "operands": {},
            "fusion": {"group": ["ffn.down", "ffn.up"], "tile": {"B": tile}},
        }
        return evaluator.evaluate(
            Candidate(workload=FFN_B16_WORKLOAD, arch=arch, mapping=mapping), Budget(), frozenset()
        ).metrics["latency_cycles"].value

    # Re-pinned at docs/decisions.md D253: the ONNX exporter now honours the Workload IR's
    # own declared `precision` instead of exporting every tensor as fp32, so this INT8
    # workload's tensors are a quarter the bytes they used to be and every latency below
    # dropped accordingly. The pre-D253 numbers were measured against a 4x overstated
    # working set, not against this workload.
    unfused = latency(None)
    assert unfused == pytest.approx(3288.0)      # was 3768.0 pre-D253
    assert latency(16) == pytest.approx(3288.0)  # full tile == unfused, exactly
    assert latency(8) == pytest.approx(3232.0)   # the interior optimum (was 3568.0)
    assert latency(2) == pytest.approx(3596.0)   # worse than not fusing at all (was 3824.0)

    assert latency(8) < unfused < latency(2), "the space must stay non-monotone with an interior optimum"


def test_flux_search_finds_the_real_fusion_optimum_end_to_end():
    """The axis through the real `flux_search` CHIA node against real Stream — the first
    mapping-space search in this repo, sweeping the complete feasible tile space."""
    from flux_chia_nodes import flux_search

    arch = flux_ir.load_document(DUAL_CORE_ARCH)
    report = flux_search(
        FFN_B16_WORKLOAD, arch, "stream",
        search_kind="fusion_tile", tile_sizes=[16, 8, 2], metric="latency_cycles",
    )
    assert report.winner is not None
    assert report.winner.tile_size == 8  # the real interior optimum, found by real search
    assert report.winner.tile_dim == "B"
    assert report.winner.mapping["fusion"]["tile"] == {"B": 8}
