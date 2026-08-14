"""Unit tests for flux_codegen_systemc_harness.spec: pure DesignSpec validation, no compiler
involved. See tests/integration/test_systemc_harness_live.py for the real compile+run+VCD
version.
"""

from __future__ import annotations

import pytest
from flux_codegen_systemc_harness import InvalidSpecError, design_spec_from_dict

_VALID_DOC = {
    "module_name": "Adder2",
    "ports": [
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "b", "dir": "in", "dtype": "int"},
        {"name": "sum", "dir": "out", "dtype": "int"},
    ],
    "behavior": "combinational: sum = a + b",
    "test_vectors": [{"inputs": {"a": 1, "b": 2}, "expected": {"sum": 3}}],
}


def _doc(**overrides):
    doc = {k: v for k, v in _VALID_DOC.items() if k not in overrides}
    doc.update(overrides)
    return doc


def test_valid_spec_parses():
    spec = design_spec_from_dict(_VALID_DOC)
    assert spec.module_name == "Adder2"
    assert len(spec.ports) == 3
    assert len(spec.test_vectors) == 1
    assert spec.is_clocked is False


def test_default_schema_version_and_id():
    spec = design_spec_from_dict(_VALID_DOC)
    assert spec.schema_version == "0.1.0"
    assert spec.id == "Adder2"


def test_missing_module_name_raises():
    with pytest.raises(InvalidSpecError, match="module_name"):
        design_spec_from_dict(_doc(module_name=""))


def test_non_identifier_module_name_raises():
    with pytest.raises(InvalidSpecError, match="module_name"):
        design_spec_from_dict(_doc(module_name="2bad"))


def test_empty_ports_raises():
    with pytest.raises(InvalidSpecError, match="ports must be non-empty"):
        design_spec_from_dict(_doc(ports=[]))


def test_duplicate_port_name_raises():
    with pytest.raises(InvalidSpecError, match="duplicate port"):
        design_spec_from_dict(_doc(ports=[
            {"name": "a", "dir": "in", "dtype": "int"},
            {"name": "a", "dir": "out", "dtype": "int"},
        ]))


def test_invalid_port_dir_raises():
    with pytest.raises(InvalidSpecError, match="dir="):
        design_spec_from_dict(_doc(ports=[{"name": "a", "dir": "sideways", "dtype": "int"}]))


def test_invalid_port_dtype_raises():
    with pytest.raises(InvalidSpecError, match="dtype="):
        design_spec_from_dict(_doc(ports=[{"name": "a", "dir": "in", "dtype": "struct_foo"}]))


def test_bool_dtype_accepted():
    """UPDATED (docs/decisions.md D124): this used to declare a lone `bool` *input* and no output
    at all, which is now rejected as unverifiable. The point of the test is dtype acceptance, so
    it keeps the bool and gains the output any real spec would have had anyway."""
    spec = design_spec_from_dict(_doc(
        ports=[{"name": "a", "dir": "in", "dtype": "bool"},
               {"name": "y", "dir": "out", "dtype": "bool"}],
        test_vectors=[{"inputs": {"a": True}, "expected": {"y": False}}],
    ))
    assert spec.ports[0].cpp_type == "bool"
    assert spec.ports[1].cpp_type == "bool"


def test_a_spec_with_no_output_ports_is_rejected():
    """Review finding (docs/decisions.md D124): such a spec is unverifiable by construction, and
    the RTL driver emitted a literally empty condition (`if () begin`) for it — a Verilator syntax
    error in generated code the caller never wrote, pointing away from the real problem."""
    with pytest.raises(InvalidSpecError, match="at least one output"):
        design_spec_from_dict(_doc(
            ports=[{"name": "a", "dir": "in", "dtype": "int"}],
            test_vectors=[{"inputs": {"a": 1}, "expected": {}}],
        ))


def test_empty_test_vectors_raises():
    with pytest.raises(InvalidSpecError, match="test_vectors must be non-empty"):
        design_spec_from_dict(_doc(test_vectors=[]))


def test_test_vector_missing_input_raises():
    with pytest.raises(InvalidSpecError, match="missing inputs"):
        design_spec_from_dict(_doc(test_vectors=[{"inputs": {"a": 1}, "expected": {"sum": 3}}]))


def test_test_vector_missing_expected_raises():
    with pytest.raises(InvalidSpecError, match="missing expected"):
        design_spec_from_dict(_doc(test_vectors=[{"inputs": {"a": 1, "b": 2}, "expected": {}}]))


def test_empty_behavior_raises():
    with pytest.raises(InvalidSpecError, match="behavior"):
        design_spec_from_dict(_doc(behavior=""))


def test_is_clocked_defaults_false_and_is_settable():
    assert design_spec_from_dict(_VALID_DOC).is_clocked is False
    assert design_spec_from_dict(_doc(is_clocked=True)).is_clocked is True


def test_a_port_can_declare_its_bit_width():
    """Widths exist so `derive_design_spec` can size an accumulator to the precision a workload
    declares, instead of refusing a 16-bit one because the ports were fixed at 32
    (docs/decisions.md D193, D202)."""
    spec = design_spec_from_dict(_doc(ports=[
        {"name": "a", "dir": "in", "dtype": "int", "bits": 16},
        {"name": "acc", "dir": "out", "dtype": "int", "bits": 40},
    ], test_vectors=[{"inputs": {"a": 1}, "expected": {"acc": 1}}]))

    assert [p.width for p in spec.ports] == [16, 40]
    assert [p.bits for p in spec.ports] == [16, 40]


def test_a_port_without_bits_keeps_the_historical_default():
    """Every spec written before widths existed must be unchanged: 32 for int, 1 for bool."""
    spec = design_spec_from_dict(_doc(ports=[
        {"name": "a", "dir": "in", "dtype": "int"},
        {"name": "flag", "dir": "out", "dtype": "bool"},
    ], test_vectors=[{"inputs": {"a": 1}, "expected": {"flag": True}}]))

    assert [p.width for p in spec.ports] == [32, 1]
    assert all(p.bits is None for p in spec.ports)


@pytest.mark.parametrize(
    "bits,why",
    [(1, "a 1-bit signed integer holds only 0 and -1"), (65, "past the harness ceiling"),
     (0, "zero-width"), (-4, "negative")],
)
def test_an_out_of_range_width_is_refused(bits, why):
    with pytest.raises(InvalidSpecError, match="outside"):
        design_spec_from_dict(_doc(ports=[
            {"name": "a", "dir": "in", "dtype": "int", "bits": bits},
            {"name": "y", "dir": "out", "dtype": "int"},
        ], test_vectors=[{"inputs": {"a": 1}, "expected": {"y": 1}}]))


def test_bits_on_a_bool_port_is_refused_rather_than_ignored():
    """Silently dropping it would let a caller believe a width was applied."""
    with pytest.raises(InvalidSpecError, match="dtype='int' only"):
        design_spec_from_dict(_doc(ports=[
            {"name": "flag", "dir": "in", "dtype": "bool", "bits": 8},
            {"name": "y", "dir": "out", "dtype": "int"},
        ], test_vectors=[{"inputs": {"flag": True}, "expected": {"y": 1}}]))


def test_a_sized_port_becomes_an_sc_int_of_that_width():
    """`sc_int<N>` rather than a wider native type: the point of declaring a width is that the DUT
    and the reference model agree on overflow, which `long long` would not give
    (docs/decisions.md D203)."""
    spec = design_spec_from_dict(_doc(ports=[
        {"name": "a", "dir": "in", "dtype": "int", "bits": 16},
        {"name": "acc", "dir": "out", "dtype": "int", "bits": 40},
        {"name": "plain", "dir": "out", "dtype": "int"},
    ], test_vectors=[{"inputs": {"a": 1}, "expected": {"acc": 1, "plain": 1}}]))

    assert [p.cpp_type for p in spec.ports] == ["sc_int<16>", "sc_int<40>", "int"]
