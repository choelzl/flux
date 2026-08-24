"""Detection of unpacked array ports before Yosys sees them (docs/decisions.md D127).

Yosys's Verilog frontend rejects unpacked array ports outright — `-sv` included, verified rather
than assumed: it reports `syntax error, unexpected '['` pointing into a temp file the caller never
wrote. The D120/D121 designs use them (wide operand vectors, GEMM memories), so they verify in
Verilator and can never reach synthesis. Detecting that here turns a confusing tool error into a
statement of the real limitation.

The false-positive direction matters more than the true-positive one: a wrong flag here would
block synthesis for every ordinary design in the repo.
"""

from __future__ import annotations

import pytest
from flux_codegen_rtl_harness import unpacked_array_ports


@pytest.mark.parametrize("source,expected", [
    ("module M (\n  input logic signed [31:0] a [0:7],\n  output logic signed [31:0] y\n);\nendmodule", ["a"]),
    ("module M (\n  input logic signed [31:0] i_mem [0:3][0:31],\n  output logic y\n);\nendmodule", ["i_mem"]),
    ("module M (input logic [7:0] w [0:1], output logic [7:0] o [0:1]);\nendmodule", ["w", "o"]),
])
def test_unpacked_array_ports_are_found(source, expected):
    assert unpacked_array_ports(source) == expected


@pytest.mark.parametrize("source", [
    # A packed range belongs *before* the name and is perfectly synthesisable.
    "module M (\n  input logic signed [31:0] a,\n  output logic signed [31:0] y\n);\nendmodule",
    "module M (input logic clk, input logic rst_n, output logic done);\nendmodule",
    "module M (\n  input logic [7:0] a,\n  input logic [7:0] b,\n  output logic [15:0] p\n);\nendmodule",
    # An internal unpacked array is fine — only *ports* are the problem.
    "module M (input logic clk, output logic [31:0] y);\n  logic [31:0] mem [0:7];\nendmodule",
    "",
])
def test_ordinary_designs_are_not_flagged(source):
    assert unpacked_array_ports(source) == []


def test_every_generated_wrapper_shape_is_classified_correctly():
    """The real designs this exists for, checked against the real generators rather than against
    hand-written approximations of them."""
    from flux_codegen_rtl_harness import generate_gemm_wrapper, generate_tiled_wrapper

    flat = generate_tiled_wrapper("T", "L", lane_width=2, steps=2)
    arrays = generate_tiled_wrapper("T", "L", lane_width=2, steps=2, array_operands=True)
    gemm = generate_gemm_wrapper("G", "GL", B=2, C=4, K=4, lanes=2)

    assert unpacked_array_ports(flat) == []          # synthesisable
    assert unpacked_array_ports(arrays) == ["a", "w"]
    assert unpacked_array_ports(gemm) == ["i_mem", "w_mem", "o_mem"]


def test_the_error_names_the_ports_and_the_way_out():
    from flux_codegen_rtl_harness import UnsupportedForSynthesisError, generate_gemm_wrapper
    from flux_codegen_rtl_harness.synth import _reject_unpacked_array_ports

    with pytest.raises(UnsupportedForSynthesisError) as exc:
        _reject_unpacked_array_ports(generate_gemm_wrapper("G", "GL", B=2, C=4, K=4, lanes=2))

    message = str(exc.value)
    assert "i_mem" in message and "array_operands=False" in message
    # It is a SynthesisError subclass, so callers that already record synthesis failure as an
    # outcome (rather than crashing a whole DSE report) keep working unchanged.
    from flux_codegen_rtl_harness import SynthesisError
    assert isinstance(exc.value, SynthesisError)
    assert exc.value.returncode == 0 and exc.value.stdout == ""


# --- Composition refuses array-port leaves (docs/decisions.md D128) ---


def _array_leaf():
    from flux_codegen_rtl_harness import design_spec_from_dict

    return design_spec_from_dict({
        "schema_version": "0.1.0", "id": "arr/L", "module_name": "ArrLeaf",
        "ports": [{"name": "v", "dir": "in", "dtype": "int", "depth": 4},
                  {"name": "y", "dir": "out", "dtype": "int"}],
        "behavior": "sums a 4-element vector",
        "test_vectors": [{"inputs": {"v": [1, 2, 3, 4]}, "expected": {"y": 10}}],
    })


def _composition_doc():
    return {
        "schema_version": "0.1.0", "id": "comp/Top", "top_module_name": "Top",
        "instances": [{"instance_name": "u0", "module_name": "ArrLeaf"}],
        "nets": {"u0": {"v": "vin", "y": "yout"}},
        "ports": [{"name": "vin", "dir": "in", "dtype": "int"},
                  {"name": "yout", "dir": "out", "dtype": "int"}],
        "behavior": "wraps a leaf that has an array port",
        "test_vectors": [{"inputs": {"vin": 1}, "expected": {"yout": 1}}],
    }


def test_rtl_composition_refuses_a_leaf_with_an_array_port():
    """Found by probing the seam (docs/decisions.md D128): this used to succeed and emit
    `ArrLeaf u0 (.v(vin), ...)` — a 4-element array port bound to a scalar net. Verilator then
    fails on generated code the caller never wrote, which is the failure mode this validation
    layer exists to prevent."""
    from flux_codegen_rtl_harness.compose import composition_spec_from_dict
    from flux_codegen_rtl_harness import InvalidSpecError

    with pytest.raises(InvalidSpecError, match="array port"):
        composition_spec_from_dict(_composition_doc(), leaf_specs={"ArrLeaf": _array_leaf()})


def test_systemc_composition_refuses_a_leaf_with_an_array_port():
    """The same seam in the SystemC harness — checked because both compose modules read leaf
    ports from the same `DesignSpec`, and only one of them having the guard is how this class of
    gap survives."""
    from flux_codegen_systemc_harness.compose import composition_spec_from_dict
    from flux_codegen_systemc_harness import InvalidSpecError

    with pytest.raises(InvalidSpecError, match="array port"):
        composition_spec_from_dict(_composition_doc(), leaf_specs={"ArrLeaf": _array_leaf()})


def test_a_parameter_default_containing_parentheses_does_not_hide_an_unpacked_port():
    """The guard used a regex whose parameter block was `#\\s*\\([^)]*\\)`, which stops at the first
    `)`. An ordinary default like `parameter KEEP = (WIDTH>8)` truncated the match and took the
    port list with it, so this returned `[]` — the guard passed, and Yosys then failed with the
    exact `syntax error, unexpected '['` that docs/decisions.md D127 added this guard to pre-empt.

    Found by transfer: the identical regex broke `flux_protocols.conform` against real third-party
    RTL (D178), and the same shape was still here. Both now share one balanced-paren scanner
    (D179).
    """
    source = """
    module leaf #(
        parameter WIDTH = 8,
        parameter KEEP = (WIDTH>8)
    ) (
        input logic clk,
        input logic [31:0] a [0:7],
        output logic [31:0] y
    );
    endmodule
    """

    assert unpacked_array_ports(source) == ["a"]


def test_the_same_module_without_parentheses_in_a_default_still_works():
    """Control: proves the case above is about the parentheses and not the fixture."""
    source = """
    module leaf #(
        parameter WIDTH = 8,
        parameter KEEP = 1
    ) (
        input logic [31:0] a [0:7]
    );
    endmodule
    """

    assert unpacked_array_ports(source) == ["a"]
