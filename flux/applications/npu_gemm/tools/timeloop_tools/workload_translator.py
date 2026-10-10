"""Flux Workload IR -> Timeloop problem-instance translation.

Scope, matching evaluator/zigzag's: a single Flux `einsum` op describing a plain 2D GEMM --
two input operands of two dims each, sharing one (the reduction), with static integer bounds.
Timeloop's problem shape (reference/problem_base.yaml) is a convolution with 8 dims
(C, M, R, S, N, P, Q, G); only N (batch), C (reduction) and M (output) are set and the rest stay
1, so every op becomes a 1x1-kernel, single-pixel "convolution".

Convention, for `expr = "in1_dims, in2_dims -> out_dims"` (e.g. "B C, C K -> B K"):
  - the dim shared by both inputs is the reduction dim -> Timeloop's C
  - the other dim of the first input is the batch dim  -> Timeloop's N
  - the other dim of the second input is the output dim -> Timeloop's M
  - `out_dims` must equal `[N_dim, M_dim]` in that order (no transposed output).

Sparsity (D78): `op["sparsity"]` = `{<flux_tensor_name>: {distribution: "hypergeometric",
density: <0-1>}}` is translated into Timeloop's `problem.instance.densities`, keyed by its
dataspace names (`Inputs`/`Weights`/`Outputs`) via the convention above. Only `hypergeometric`
is supported: `fixed_structured` needs parameters beyond a density and, with only a density,
gave a non-monotonic gated fraction on the pinned Timeloop image.
"""

from __future__ import annotations


from flux_ir import parse_einsum
from typing import Any

from .errors import NotExpressibleError


# Supported Sparseloop distributions; "fixed_structured" is excluded (see module docstring).
_SUPPORTED_SPARSITY_DISTRIBUTIONS = frozenset({"hypergeometric"})


def flux_dims_to_timeloop_dims(op: dict[str, Any]) -> dict[str, str]:
    """The {flux_dim: timeloop_dim} mapping for one einsum op (batch -> N, reduction -> C,
    output -> M).
    """
    op_id = op.get("id", "<no id>")

    if op.get("kind") != "einsum":
        raise NotExpressibleError(
            f"op {op_id!r} has kind={op.get('kind')!r}; only 'einsum' ops translate to Timeloop "
            "today (data_dependent and compute_kernel have no Timeloop equivalent)."
        )

    expr = op.get("expr")
    if not expr:
        raise NotExpressibleError(f"op {op_id!r} is missing 'expr'")

    parsed = parse_einsum(expr)          # the IR's own grammar, parsed once (D467)
    if parsed is None:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r} is not a two-input einsum ('a b, b c -> a c'); "
            "Timeloop's problem shape here is bilinear (Gemm/Conv-style)."
        )
    out_dims = list(parsed.out)
    if not parsed.two_dimensional:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r}: this translator only handles plain 2D GEMM (each "
            "input operand needs exactly two dims, e.g. 'b c, c k -> b k')."
        )

    reduction = parsed.shared
    if len(reduction) != 1:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r}: expected exactly one dim shared between the two "
            f"input operands (the reduction dim), found {sorted(reduction)}."
        )
    reduction_dim = reduction[0]
    batch_dim, output_dim = parsed.expected_out()

    if out_dims != [batch_dim, output_dim]:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r}: expected output dims {[batch_dim, output_dim]} "
            f"(batch, output — no transposed output in v0.1), found {out_dims}."
        )

    return {batch_dim: "N", reduction_dim: "C", output_dim: "M"}


def flux_tensor_to_timeloop_dataspace(workload: dict[str, Any], op: dict[str, Any]) -> dict[str, str]:
    """The `{flux_tensor_name: timeloop_dataspace_name}` mapping (`Inputs`/`Weights`/`Outputs`)
    for one einsum op's three operand tensors, matched by each tensor's `rank` against the op's
    dim sets. Shared by `op_sparsity_to_timeloop_densities` and `architecture_translator.py`'s
    `sparse_optimizations`, so a tensor name always resolves to the same dataspace.
    """
    op_id = op.get("id", "<no id>")
    dim_map = flux_dims_to_timeloop_dims(op)  # validates op shape as a side effect
    reduction_dim = next(d for d, t in dim_map.items() if t == "C")
    batch_dim = next(d for d, t in dim_map.items() if t == "N")
    output_dim = next(d for d, t in dim_map.items() if t == "M")

    result: dict[str, str] = {}
    for tensor in workload.get("tensors", []):
        rank = set(tensor.get("rank", []))
        name = tensor.get("name")
        if rank == {batch_dim, reduction_dim}:
            result[name] = "Inputs"
        elif rank == {reduction_dim, output_dim}:
            result[name] = "Weights"
        elif rank == {batch_dim, output_dim}:
            result[name] = "Outputs"
    if len(result) != 3:
        raise NotExpressibleError(
            f"op {op_id!r}: could not match all three operand tensors (Inputs/Weights/Outputs) "
            f"to workload['tensors'] by rank; matched {sorted(result)} of 3 expected — every "
            "operand tensor's own declared 'rank' must exactly match one of this op's own "
            "dim-sets (batch+reduction, reduction+output, batch+output)."
        )
    return result


def op_sparsity_to_timeloop_densities(
    workload: dict[str, Any], op: dict[str, Any]
) -> dict[str, dict[str, Any]] | None:
    """Translate `op["sparsity"]` (D78) into Timeloop's `problem.instance.densities` block, or
    `None` if the op declares no sparsity.

    `op["sparsity"]` shape: `{<flux_tensor_name>: {"distribution": "hypergeometric", "density":
    <0.0-1.0>}}`; tensors not named stay fully dense.
    """
    sparsity = op.get("sparsity")
    if not sparsity:
        return None
    op_id = op.get("id", "<no id>")
    tensor_map = flux_tensor_to_timeloop_dataspace(workload, op)

    densities: dict[str, dict[str, Any]] = {}
    for flux_tensor_name, spec in sparsity.items():
        if flux_tensor_name not in tensor_map:
            raise NotExpressibleError(
                f"op {op_id!r}: sparsity names tensor {flux_tensor_name!r}, which is not one of "
                f"this op's own three operand tensors ({sorted(tensor_map)})."
            )
        distribution = spec.get("distribution")
        if distribution not in _SUPPORTED_SPARSITY_DISTRIBUTIONS:
            raise NotExpressibleError(
                f"op {op_id!r}: sparsity distribution {distribution!r} for tensor "
                f"{flux_tensor_name!r} is not supported — only "
                f"{sorted(_SUPPORTED_SPARSITY_DISTRIBUTIONS)} (see this module's own docstring "
                "for why 'fixed_structured' is deliberately excluded)."
            )
        density = spec.get("density")
        if not isinstance(density, (int, float)) or not (0.0 <= density <= 1.0):
            raise NotExpressibleError(
                f"op {op_id!r}: sparsity density for tensor {flux_tensor_name!r} must be a "
                f"real number in [0, 1], found {density!r}."
            )
        densities[tensor_map[flux_tensor_name]] = {"distribution": distribution, "density": density}
    return densities


def einsum_op_to_timeloop_instance(op: dict[str, Any]) -> dict[str, int]:
    """Translate one Flux Workload IR op into instance overrides {N, C, M} for
    reference/problem_base.yaml.
    """
    op_id = op.get("id", "<no id>")
    dim_map = flux_dims_to_timeloop_dims(op)
    bounds = op.get("bounds", {})
    overrides: dict[str, int] = {}
    for flux_dim, timeloop_dim in dim_map.items():
        size = bounds.get(flux_dim)
        if not isinstance(size, int):
            raise NotExpressibleError(
                f"op {op_id!r} dim {flux_dim!r} has non-static bound {size!r}; Timeloop needs "
                "a fixed instance size, not a distribution (the dynamic-shape gap, "
                "not a translation bug)."
            )
        overrides[timeloop_dim] = size

    return overrides
