"""Flux Workload IR -> Timeloop problem-instance translation.

v0.1 scope, matching evaluators/zigzag's: a single Flux `einsum` op describing a plain 2D GEMM
— exactly two input operands, each with exactly two dims, sharing exactly one dim (the
reduction), with fully static integer bounds. Timeloop's problem shape
(reference/problem_base.yaml) models a convolution with 8 free dims (C, M, R, S, N, P, Q, G);
this translator only overrides N (batch), C (reduction/input-channel), M (output-channel) and
leaves everything else at the degenerate default (R=S=P=Q=G=1) — every op translated this way
becomes a plain GEMM: a 1x1-kernel, single-pixel "convolution".

Convention, for `expr = "in1_dims, in2_dims -> out_dims"` (e.g. "B C, C K -> B K"):
  - the dim shared by both inputs is the reduction dim -> Timeloop's C
  - the other dim of the first input is the batch dim  -> Timeloop's N
  - the other dim of the second input is the output dim -> Timeloop's M
  - `out_dims` must equal `[N_dim, M_dim]` in that order (no transposed output, v0.1).
"""

from __future__ import annotations

import re
from typing import Any

from .errors import NotExpressibleError

_EXPR_RE = re.compile(r"^\s*([\w\s]+?)\s*,\s*([\w\s]+?)\s*->\s*([\w\s]+?)\s*$")


def _dims(spec: str) -> list[str]:
    return spec.split()


def flux_dims_to_timeloop_dims(op: dict[str, Any]) -> dict[str, str]:
    """Derive the {flux_dim: timeloop_dim} name mapping this translator's convention assigns for
    one einsum op (batch -> N, reduction -> C, output -> M — see module docstring). Shared by
    einsum_op_to_timeloop_instance (below) and mapping_translator.py, so both agree on which
    Flux dim name means what without re-deriving it independently.
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

    match = _EXPR_RE.match(expr)
    if not match:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r} is not a two-input einsum ('a b, b c -> a c'); "
            "Timeloop's problem shape here is bilinear (Gemm/Conv-style)."
        )
    in1_dims, in2_dims, out_dims = (_dims(group) for group in match.groups())
    if len(in1_dims) != 2 or len(in2_dims) != 2:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r}: this translator only handles plain 2D GEMM (each "
            "input operand needs exactly two dims, e.g. 'b c, c k -> b k')."
        )

    reduction = set(in1_dims) & set(in2_dims)
    if len(reduction) != 1:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r}: expected exactly one dim shared between the two "
            f"input operands (the reduction dim), found {sorted(reduction)}."
        )
    reduction_dim = next(iter(reduction))
    batch_dim = next(d for d in in1_dims if d != reduction_dim)
    output_dim = next(d for d in in2_dims if d != reduction_dim)

    if out_dims != [batch_dim, output_dim]:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r}: expected output dims {[batch_dim, output_dim]} "
            f"(batch, output — no transposed output in v0.1), found {out_dims}."
        )

    return {batch_dim: "N", reduction_dim: "C", output_dim: "M"}


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
                "a fixed instance size, not a distribution (docs/03.md G5's dynamic-shape gap, "
                "not a translation bug)."
            )
        overrides[timeloop_dim] = size

    return overrides
