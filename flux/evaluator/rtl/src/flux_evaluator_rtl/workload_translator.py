"""Flux Workload IR -> mac_array.sv shape parameters (B, C, K).

v0.1 scope, matching evaluator/zigzag's and evaluator/timeloop's own einsum handling: a single
Flux `einsum` op describing a plain 2D GEMM — exactly two input operands, each with exactly two
dims, sharing exactly one dim (the reduction), with fully static integer bounds. Dim names are
derived generically (batch/reduction/output), not tied to any particular naming convention —
same approach as evaluator/timeloop's `flux_dims_to_timeloop_dims`, independently reimplemented
here rather than imported, to keep this package's dependency graph to just flux-ir and
flux-evaluator-abi (see pyproject.toml).
"""

from __future__ import annotations


from flux_ir import parse_einsum
from typing import Any

from .errors import NotExpressibleError

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

    parsed = parse_einsum(expr)          # the IR's own grammar, parsed once (D467)
    if parsed is None:
        raise NotExpressibleError(
            f"op {op_id!r} expr {expr!r} is not a two-input einsum ('a b, b c -> a c'); "
            "mac_array.sv is bilinear (Gemm-style)."
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
    batch_dim, output_dim = parsed.expected_out()
    reduction_dim = reduction[0]

    if parsed.gemm() is None:
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
