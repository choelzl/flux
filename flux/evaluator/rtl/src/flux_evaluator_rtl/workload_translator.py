"""Flux Workload IR -> mac_array.sv shape parameters (B, C, K).

v0.1 scope, matching evaluators/zigzag's and evaluators/timeloop's own einsum handling: a single
Flux `einsum` op describing a plain 2D GEMM — exactly two input operands, each with exactly two
dims, sharing exactly one dim (the reduction), with fully static integer bounds. Dim names are
derived generically (batch/reduction/output), not tied to any particular naming convention —
same approach as evaluators/timeloop's `flux_dims_to_timeloop_dims`, independently reimplemented
here rather than imported, to keep this package's dependency graph to just flux-ir and
flux-evaluator-abi (see pyproject.toml).
"""

from __future__ import annotations

import re
from typing import Any

from .errors import NotExpressibleError

_EXPR_RE = re.compile(r"^\s*([\w\s]+?)\s*,\s*([\w\s]+?)\s*->\s*([\w\s]+?)\s*$")


def _dims(spec: str) -> list[str]:
    return spec.split()


def einsum_op_to_mac_array_shape(op: dict[str, Any]) -> dict[str, int]:
    """Returns {"B": batch_size, "C": reduction_size, "K": output_size} — mac_array.sv's own
    parameter names.
    """
    op_id = op.get("id", "<no id>")

    if op.get("kind") != "einsum":
        raise NotExpressibleError(
            f"op {op_id!r} has kind={op.get('kind')!r}; only 'einsum' ops translate to this RTL "
            "adapter today (data_dependent and compute_kernel have no RTL equivalent)."
        )

    expr = op.get("expr")
    if not expr:
        raise NotExpressibleError(f"op {op_id!r} is missing 'expr'")

    match = _EXPR_RE.match(expr)
    if not match:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r} is not a two-input einsum ('a b, b c -> a c'); "
            "mac_array.sv is bilinear (Gemm-style)."
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

    bounds = op.get("bounds", {})
    shape: dict[str, int] = {}
    for name, dim in (("B", batch_dim), ("C", reduction_dim), ("K", output_dim)):
        size = bounds.get(dim)
        if not isinstance(size, int):
            raise NotExpressibleError(
                f"op {op_id!r} dim {dim!r} has non-static bound {size!r}; mac_array.sv needs a "
                "fixed size, not a distribution — this is docs/gap-analysis.md G5's dynamic-shape gap, "
                "not a translation bug."
            )
        shape[name] = size
    return shape
