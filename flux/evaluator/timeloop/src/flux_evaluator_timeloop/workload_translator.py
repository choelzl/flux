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

**Real sparsity, via Timeloop's own real `sparse_optimizations`/`densities` mechanism**
(docs/decisions.md D78): `op["sparsity"]` — a field the Workload IR schema already declared
(`$defs.op.properties.sparsity`, unused until now) — names a real, caller-declared density for
one or more of the op's own operand tensors: `{<flux_tensor_name>: {distribution:
"hypergeometric", density: <0-1>}}`. `flux_tensor_to_timeloop_dataspace`/
`op_sparsity_to_timeloop_densities` translate that into Timeloop's own real `problem.instance.
densities` block, keyed by Timeloop's fixed tensor names (`Inputs`/`Weights`/`Outputs`, from
`reference/problem_base.yaml`'s own `shape.data_spaces`) via the same batch/reduction/output
convention above. **`hypergeometric` only, `fixed_structured` deliberately not supported**: both
are real Sparseloop distributions, but `fixed_structured` models genuinely structured sparsity
(e.g. NVIDIA-style N:M patterns) with parameters beyond a bare density scalar — real, hands-on
testing against this repo's own pinned Timeloop Docker image found its behavior non-monotonic
with density using only the parameters this translator can express (0.0->100% gated, 0.25->25%
gated, 0.5/1.0->0% gated — not a physically sensible curve for this scope), while
`hypergeometric` gave a clean, monotonically-decreasing gated fraction as density rose (100%,
37.5%, 6.25%, 0%, 0% at densities 0.0/0.25/0.5/0.75/1.0) — the same "verify empirically before
trusting a schema's own documented options" discipline this whole session has used for every
real external tool.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import NotExpressibleError

_EXPR_RE = re.compile(r"^\s*([\w\s]+?)\s*,\s*([\w\s]+?)\s*->\s*([\w\s]+?)\s*$")

# Real Sparseloop distributions this translator supports — see the module docstring for why
# "fixed_structured" is deliberately excluded (verified non-monotonic with density here).
_SUPPORTED_SPARSITY_DISTRIBUTIONS = frozenset({"hypergeometric"})


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


def flux_tensor_to_timeloop_dataspace(workload: dict[str, Any], op: dict[str, Any]) -> dict[str, str]:
    """Derive the `{flux_tensor_name: timeloop_dataspace_name}` mapping for one einsum op's own
    three operand tensors (`Inputs`/`Weights`/`Outputs` — `reference/problem_base.yaml`'s own
    real dataspace names), matched by each Flux tensor's own declared `rank` (dim-name list)
    against the op's own in1/in2/out dim sets `flux_dims_to_timeloop_dims` already derives, so
    both functions agree on which Flux dim/tensor name means what without re-deriving either
    independently. Used both by `op_sparsity_to_timeloop_densities` (below) and
    `architecture_translator.py`'s own `sparse_optimizations` translation, so a
    `target`/`condition_on` tensor name and a `sparsity` tensor name always resolve to the same
    Timeloop dataspace for the same op.
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
    """Translate `op["sparsity"]` (Workload IR's own, previously-unused schema field — docs/
    decisions.md D78) into Timeloop's real `problem.instance.densities` block, or `None` if the
    op declares no sparsity at all (the common case — every pre-existing example workload in
    this repo has none, so this is purely additive).

    `op["sparsity"]` shape: `{<flux_tensor_name>: {"distribution": "hypergeometric", "density":
    <0.0-1.0>}}` — one entry per operand tensor a caller wants to declare a real density for;
    tensors not named keep Timeloop's own default (fully dense, no sparse modeling).
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
                "a fixed instance size, not a distribution (docs/gap-analysis.md G5's dynamic-shape gap, "
                "not a translation bug)."
            )
        overrides[timeloop_dim] = size

    return overrides
