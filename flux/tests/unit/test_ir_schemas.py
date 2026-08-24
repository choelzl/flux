"""Every reference example must validate against its schema — the examples double as fixtures
proving docs/decisions.md D1 (general-SoC, not DNN-only) is actually representable, not just
asserted in prose.
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
    """The `compatibility` block should flag inexpressibility both ways: ZigZag's uneven
    mapping is inexpressible in Timeloop (docs/ir.md), and a general-SoC compute_kernel
    mapping is inexpressible in either DNN-only tool (docs/decisions.md D1)."""
    mapping_examples = FLUX_ROOT / "core/ir/mapping/examples"
    dnn = flux_ir.load_document(mapping_examples / "attn-qk-map0.yaml")
    soc = flux_ir.load_document(mapping_examples / "dma-desc-fetch-map0.yaml")
    assert "timeloop" in dnn["compatibility"]["not_expressible_in"]
    assert "zigzag" in dnn["compatibility"]["expressible_in"]
    assert set(soc["compatibility"]["not_expressible_in"]) == {"zigzag", "timeloop"}


def test_validation_reports_every_error_not_just_the_first():
    """`jsonschema.validate` raises on the first error only. The main consumer of this message is
    a repair loop — `generation/architecture.py` feeds it back to an LLM and retries — so a
    document with three independent mistakes needed three rounds, against a default budget of
    three attempts total: it could not converge even with a model that fixed one per round
    perfectly (docs/decisions.md D187).
    """
    bad = {"schema_version": "not-a-version", "id": 123, "hierarchy": "should-be-a-list"}

    with pytest.raises(flux_ir.SchemaValidationError) as exc:
        flux_ir.validate("architecture", bad)

    message = str(exc.value)
    assert "3 errors" in message
    for field in ("schema_version", "id", "hierarchy"):
        assert field in message, f"{field} missing from {message}"


def test_each_error_names_its_path_in_the_document():
    """A message saying only "'x' is not of type 'string'" doesn't say *where*, which is useless
    for a nested document."""
    arch = {
        "schema_version": "0.1.0", "id": "test/arch",
        "hierarchy": [{"level": 123, "class": "compute"}],
    }

    with pytest.raises(flux_ir.SchemaValidationError) as exc:
        flux_ir.validate("architecture", arch)

    assert "hierarchy/0" in str(exc.value)


def test_a_single_error_still_reads_naturally():
    """The common case must not regress into list formatting for one item."""
    with pytest.raises(flux_ir.SchemaValidationError) as exc:
        flux_ir.validate("architecture", {"schema_version": "0.1.0", "id": "x"})

    assert "1 error" in str(exc.value) and "errors" not in str(exc.value)
