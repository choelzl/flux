"""Flux Workload IR -> ZigZag manual-workload layer translation.

v0.1 scope, deliberately narrow: a single Flux `einsum` op with exactly two input operands
(bilinear — matches ZigZag's Gemm/Conv equation grammar, `O[...]+=I[...]*W[...]`) and fully
static integer bounds. `data_dependent` and `compute_kernel` ops (docs/00-decisions.md D1) have
no ZigZag equivalent at all; dynamic bounds (`{dyn: [...]}`, docs/04.md §3.1) have no ZigZag
equivalent either, since ZigZag has no notion of a symbolic dimension. Both raise
NotExpressibleError rather than silently picking a bound value or dropping an operand.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import NotExpressibleError

_EXPR_RE = re.compile(r"^\s*([\w\s]+?)\s*,\s*([\w\s]+?)\s*->\s*([\w\s]+?)\s*$")

_DEFAULT_PRECISION = {"I": 8, "W": 8, "O": 16, "O_final": 8}


def _dims(spec: str) -> list[str]:
    return spec.split()


def einsum_op_to_zigzag_layer(op: dict[str, Any], layer_id: int) -> dict[str, Any]:
    """Translate one Flux Workload IR op into a ZigZag manual-workload layer dict, per
    zigzag.parser.workload_validator.WorkloadValidator.LAYER_SCHEMA.
    """
    op_id = op.get("id", "<no id>")

    if op.get("kind") != "einsum":
        raise NotExpressibleError(
            f"op {op_id!r} has kind={op.get('kind')!r}; only 'einsum' ops translate to ZigZag "
            "today (data_dependent and compute_kernel have no ZigZag equivalent)."
        )

    expr = op.get("expr")
    if not expr:
        raise NotExpressibleError(f"op {op_id!r} is missing 'expr'")

    match = _EXPR_RE.match(expr)
    if not match:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r} is not a two-input einsum ('a b, b c -> a c'); "
            "ZigZag's equation grammar is bilinear (Gemm/Conv-style)."
        )
    in1_dims, in2_dims, out_dims = (_dims(group) for group in match.groups())

    bounds = op.get("bounds", {})
    all_dims = list(dict.fromkeys([*out_dims, *in1_dims, *in2_dims]))
    loop_sizes: list[int] = []
    for dim in all_dims:
        size = bounds.get(dim)
        if not isinstance(size, int):
            raise NotExpressibleError(
                f"op {op_id!r} dim {dim!r} has non-static bound {size!r}; ZigZag needs a fixed "
                "loop size, not a distribution — this is docs/03.md G5's dynamic-shape gap, not "
                "a translation bug."
            )
        loop_sizes.append(size)

    equation = (
        "O[" + "][".join(out_dims) + "]+="
        "I[" + "][".join(in1_dims) + "]*"
        "W[" + "][".join(in2_dims) + "]"
    )

    return {
        "id": layer_id,
        "name": op_id,
        "operator_type": "Gemm",
        "equation": equation,
        "loop_dims": all_dims,
        "loop_sizes": loop_sizes,
        "operand_precision": dict(op.get("precision", _DEFAULT_PRECISION)),
    }


def workload_to_zigzag_layers(workload: dict[str, Any]) -> list[dict[str, Any]]:
    """Translate every `einsum` op in an Flux Workload IR document into ZigZag layers, in
    order. Non-einsum ops are silently skipped here (not silently *approximated* — they simply
    don't produce a layer); callers that need to know a workload was only partially translated
    should check `len(result) < len(workload['ops'])` themselves. Raises NotExpressibleError if
    there are no einsum ops at all, since evaluating nothing is never the right silent default.
    """
    ops = workload.get("ops", [])
    einsum_ops = [op for op in ops if op.get("kind") == "einsum"]
    if not einsum_ops:
        raise NotExpressibleError(
            f"workload {workload.get('id')!r} has no 'einsum' ops; ZigZag cannot evaluate "
            "data_dependent or compute_kernel ops (docs/00-decisions.md D1)."
        )
    return [einsum_op_to_zigzag_layer(op, i) for i, op in enumerate(einsum_ops)]
