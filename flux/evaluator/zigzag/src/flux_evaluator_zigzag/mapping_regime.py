"""Detect the candidate shape where ZigZag's latency behaves qualitatively differently (D109/D110).

ZigZag's residual against Verilator RTL is stable (~+1.9 to +2.1) except when the compute
array's spatial width equals the einsum's reduction extent (`lanes == C`), where it drops to
~+0.8 to +0.9, likely because ZigZag then unrolls the reduction loop entirely.

Lives here, not in `calibration/`, so calibration never parses architecture internals: callers
pass the resulting `caveat` to `record_conformance_residuals`, and `residual_stats` excludes
caveated records by default. The predicate is about ZigZag alone.
"""

from __future__ import annotations

from typing import Any

from flux_ir import parse_einsum


CAVEAT = "zigzag-reduction-dim-fully-unrolled"
"""The caveat string callers should record for such a residual — a stable identifier, so records
written by different callers land in one recognisable group."""


def reduction_dims(op: dict[str, Any]) -> list[str]:
    """The einsum's reduction dims: shared by both inputs, absent from the output. `[]` for an
    unparseable op (advisory, never raises)."""
    expr = op.get("expr") if isinstance(op, dict) else None
    # The IR's own grammar (D467), as `workload_translator` accepts.
    parsed = parse_einsum(expr) if isinstance(expr, str) else None
    if parsed is None:
        return []
    return [d for d in parsed.in1 if d in parsed.in2 and d not in parsed.out]


def fully_unrolls_reduction_dim(workload: dict[str, Any], arch: dict[str, Any] | None) -> bool:
    """True when the compute array's single spatial width equals a reduction extent of every
    einsum op (the `lanes == C` diagonal).

    Conservative: any shape it cannot identify returns False, so a residual is excluded only
    on a positive match. Never raises.
    """
    if not isinstance(arch, dict) or not isinstance(workload, dict):
        return False

    # Tolerate present-but-null keys (`or {}`): callers need not validate the IR first (D112).
    hierarchy = arch.get("hierarchy") or []
    if not isinstance(hierarchy, list):
        return False
    compute_nodes = [
        n for n in hierarchy if isinstance(n, dict) and n.get("class") == "compute"
    ]
    if len(compute_nodes) != 1:
        return False
    dims = (compute_nodes[0].get("attrs") or {}).get("dims") or {}
    if not isinstance(dims, dict) or len(dims) != 1:
        return False
    lanes = next(iter(dims.values()))
    if not isinstance(lanes, int) or isinstance(lanes, bool):
        return False

    ops = workload.get("ops") or []
    if not isinstance(ops, list):
        return False
    einsum_ops = [o for o in ops if isinstance(o, dict) and o.get("kind") == "einsum"]
    if not einsum_ops:
        return False

    # Every einsum op must be on the diagonal, not just one: the residual belongs to the whole
    # run (D112).
    for op in einsum_ops:
        bounds = op.get("bounds") or {}
        if not isinstance(bounds, dict):
            return False
        if not any(bounds.get(dim) == lanes for dim in reduction_dims(op)):
            return False
    return True


def caveat_for(workload: dict[str, Any], arch: dict[str, Any] | None) -> str | None:
    """`CAVEAT` when this pair is on the diagonal, else None; pass to
    `record_conformance_residuals(caveat=...)`."""
    return CAVEAT if fully_unrolls_reduction_dim(workload, arch) else None
