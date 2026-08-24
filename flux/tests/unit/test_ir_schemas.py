"""Every reference example validates against its schema, showing the general-SoC (not DNN-only) scope is representable (D1).
"""

from __future__ import annotations

from pathlib import Path

import flux_ir
import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]


def test_examples_validate(ir_example):
    kind, path = ir_example
    doc = flux_ir.load_document(path)
    flux_ir.validate(kind, doc)  # raises on failure


def test_examples_have_matching_schema_version(ir_example):
    _, path = ir_example
    doc = flux_ir.load_document(path)
    assert doc["schema_version"] == "0.1.0"


@pytest.mark.parametrize("kind", ["workload", "architecture", "mapping"])
def test_missing_required_top_level_key_is_rejected(kind):
    with pytest.raises(flux_ir.SchemaValidationError):
        flux_ir.validate(kind, {"schema_version": "0.1.0"})


@pytest.mark.parametrize("kind", ["workload", "architecture", "mapping"])
def test_unknown_top_level_key_is_rejected(kind, ir_example):
    example_kind, path = ir_example
    if example_kind != kind:
        pytest.skip("one example per kind is enough")
    doc = flux_ir.load_document(path)
    doc["not_a_real_field"] = True
    with pytest.raises(flux_ir.SchemaValidationError):
        flux_ir.validate(kind, doc)


def test_einsum_op_without_expr_or_bounds_is_rejected():
    doc = {
        "schema_version": "0.1.0",
        "id": "bad/einsum",
        "ops": [{"id": "x", "kind": "einsum"}],
    }
    with pytest.raises(flux_ir.SchemaValidationError):
        flux_ir.validate("workload", doc)


def test_compute_kernel_op_without_semantics_is_rejected():
    doc = {
        "schema_version": "0.1.0",
        "id": "bad/compute-kernel",
        "ops": [{"id": "x", "kind": "compute_kernel"}],
    }
    with pytest.raises(flux_ir.SchemaValidationError):
        flux_ir.validate("workload", doc)


def test_mapping_not_expressible_in_is_symmetric_between_dnn_and_soc_examples():
    """The `compatibility` block flags inexpressibility both ways (uneven ZigZag mappings in
    Timeloop; general-SoC compute_kernel mappings in either DNN tool)."""
    mapping_examples = FLUX_ROOT / "core/ir/mapping/examples"
    dnn = flux_ir.load_document(mapping_examples / "attn-qk-map0.yaml")
    soc = flux_ir.load_document(mapping_examples / "dma-desc-fetch-map0.yaml")
    assert "timeloop" in dnn["compatibility"]["not_expressible_in"]
    assert "zigzag" in dnn["compatibility"]["expressible_in"]
    assert set(soc["compatibility"]["not_expressible_in"]) == {"zigzag", "timeloop"}


def test_validation_reports_every_error_not_just_the_first():
    """All schema errors are reported at once, so a repair loop can fix them in one round (D187)."""
    bad = {"schema_version": "not-a-version", "id": 123, "hierarchy": "should-be-a-list"}

    with pytest.raises(flux_ir.SchemaValidationError) as exc:
        flux_ir.validate("architecture", bad)

    message = str(exc.value)
    assert "3 errors" in message
    for field in ("schema_version", "id", "hierarchy"):
        assert field in message, f"{field} missing from {message}"


def test_each_error_names_its_path_in_the_document():
    """Each error names its path in the document."""
    arch = {
        "schema_version": "0.1.0", "id": "test/arch",
        "hierarchy": [{"level": 123, "class": "compute"}],
    }

    with pytest.raises(flux_ir.SchemaValidationError) as exc:
        flux_ir.validate("architecture", arch)

    assert "hierarchy/0" in str(exc.value)


def test_a_single_error_still_reads_naturally():
    """A single error is not formatted as a list."""
    with pytest.raises(flux_ir.SchemaValidationError) as exc:
        flux_ir.validate("architecture", {"schema_version": "0.1.0", "id": "x"})

    assert "1 error" in str(exc.value) and "errors" not in str(exc.value)
