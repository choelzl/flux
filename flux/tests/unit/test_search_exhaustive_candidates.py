"""Unit tests for flux_search_exhaustive.candidates: pure generation logic over synthetic and
real IR documents, no evaluator involved. See
tests/integration/test_search_exhaustive_live.py for the real-ZigZag version that reproduces
docs/phase1-exit-criterion-report.md's Finding 4.
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest
from flux_search_exhaustive.candidates import NotAFlatMappingCandidate, generate_flat_mapping_candidates

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _workload(bounds: dict[str, int], tensors: list[str] | None = None) -> dict:
    tensors = tensors if tensors is not None else ["I", "W", "O"]
    return {
        "schema_version": "0.1.0",
        "id": "test/wl",
        "tensors": [{"name": t, "rank": list(bounds), "dtype": "int8"} for t in tensors],
        "ops": [{"id": "test.op", "kind": "einsum", "expr": "unused", "bounds": bounds}],
    }


def _arch_1d(array_size: int) -> dict:
    return {
        "schema_version": "0.1.0",
        "id": "test/arch",
        "tech": {"node": "n28", "pdk_class": "open"},
        "hierarchy": [
            {"level": "dram", "class": "memory", "attrs": {"size_kb": 1_000_000}},
            {"level": "gbuf", "class": "memory", "attrs": {"size_kb": 512}},
            {"level": "pe_array", "class": "compute", "attrs": {"dims": {"X": array_size}}},
        ],
    }


def test_candidate_count_matches_dims_factorial_times_dims():
    # 3 loop dims -> 3 spatial-split choices x 3! = 6 temporal orders = 18 candidates, exactly
    # docs/phase1-exit-criterion-report.md Finding 4's "3 spatial splits x 6 permutations".
    workload = _workload({"B": 4, "C": 32, "K": 32})
    arch = _arch_1d(8)
    candidates = generate_flat_mapping_candidates(workload, arch, for_op="test.op")
    assert len(candidates) == 18


def test_spatial_size_is_the_largest_divisor_at_most_the_array_width():
    workload = _workload({"B": 4, "C": 32, "K": 32})
    arch = _arch_1d(8)
    candidates = generate_flat_mapping_candidates(workload, arch, for_op="test.op")
    by_spatial_dim = {c.spatial_dim: c.spatial_size for c in candidates}
    assert by_spatial_dim["B"] == 4  # 4 < 8: use all of B, array has spare lanes
    assert by_spatial_dim["C"] == 8  # 32 > 8: use the whole array, remainder stays temporal
    assert by_spatial_dim["K"] == 8


def test_every_operand_gets_the_same_flat_loop_order():
    workload = _workload({"B": 4, "C": 32, "K": 32})
    arch = _arch_1d(8)
    candidates = generate_flat_mapping_candidates(workload, arch, for_op="test.op")
    mapping = candidates[0].mapping
    loop_lists = [tuple((d["dim"], d["size"]) for d in ops[0]["loops"]) for ops in mapping["operands"].values()]
    assert len(set(loop_lists)) == 1  # I, W, O all identical


def test_generated_mapping_document_is_schema_valid():
    workload = _workload({"B": 4, "C": 32, "K": 32})
    arch = _arch_1d(8)
    candidates = generate_flat_mapping_candidates(workload, arch, for_op="test.op")
    for c in candidates:
        flux_ir.validate("mapping", c.mapping)  # raises SchemaValidationError on failure


def test_temporal_sizes_multiply_back_to_the_original_bound():
    workload = _workload({"B": 4, "C": 32, "K": 32})
    arch = _arch_1d(8)
    candidates = generate_flat_mapping_candidates(workload, arch, for_op="test.op")
    for c in candidates:
        spatial_entry = c.mapping["spatial"][0]
        temporal_size = next(
            loop["size"] for loop in c.mapping["operands"]["I"][0]["loops"] if loop["dim"] == c.spatial_dim
        )
        original_bound = {"B": 4, "C": 32, "K": 32}[c.spatial_dim]
        assert spatial_entry["size"] * temporal_size == original_bound


def test_rejects_workload_with_no_matching_op():
    workload = _workload({"B": 4})
    arch = _arch_1d(8)
    with pytest.raises(NotAFlatMappingCandidate):
        generate_flat_mapping_candidates(workload, arch, for_op="does.not.exist")


def test_rejects_non_einsum_op():
    workload = _workload({"B": 4})
    workload["ops"][0]["kind"] = "compute_kernel"
    arch = _arch_1d(8)
    with pytest.raises(NotAFlatMappingCandidate):
        generate_flat_mapping_candidates(workload, arch, for_op="test.op")


def test_rejects_multi_spatial_dim_architecture():
    workload = _workload({"B": 4, "K": 32})
    arch = _arch_1d(8)
    arch["hierarchy"][-1]["attrs"]["dims"] = {"X": 8, "Y": 4}
    with pytest.raises(NotAFlatMappingCandidate):
        generate_flat_mapping_candidates(workload, arch, for_op="test.op")


def test_rejects_multi_compute_level_architecture():
    workload = _workload({"B": 4, "K": 32})
    arch = _arch_1d(8)
    arch["hierarchy"].append({"level": "pe_array2", "class": "compute", "attrs": {"dims": {"Y": 4}}})
    with pytest.raises(NotAFlatMappingCandidate):
        generate_flat_mapping_candidates(workload, arch, for_op="test.op")


def test_reproduces_the_real_mlp_gemm0_search_space():
    """Against the actual IR documents docs/phase1-exit-criterion-report.md's Finding 4 used —
    not a synthetic stand-in — confirming this generates the exact same 18-candidate space the
    hand-run sweep covered."""
    workload = flux_ir.load_document(FLUX_ROOT / "ir/workload/examples/mlp-gemm0.yaml")
    arch = flux_ir.load_document(FLUX_ROOT / "ir/architecture/examples/simple-npu-1d-v1.yaml")
    candidates = generate_flat_mapping_candidates(workload, arch, for_op="mlp.gemm0")
    assert len(candidates) == 18
    assert {c.spatial_dim for c in candidates} == {"B", "C", "K"}
