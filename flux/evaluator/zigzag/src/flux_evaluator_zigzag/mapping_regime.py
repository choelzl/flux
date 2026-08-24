"""Detect the one candidate shape where ZigZag's own latency behaves qualitatively differently
(docs/decisions.md D109/D110).

**The measured fact.** ZigZag's residual against real Verilator RTL is stable at ~+1.9 to +2.1
across a 64x span of architecture widths and across workload shapes — except when the compute
array's spatial width equals the einsum's *reduction* dimension extent, where it drops to ~+0.8
to +0.9:

    lanes=1..16, C=32   +1.91..+1.94        lanes=8,  C=8    +0.772
    lanes=32,    C=64   +1.958              lanes=16, C=16   +0.876
    lanes=64,    C=32   +2.098              lanes=32, C=32   +0.932

Predicted in both directions before measuring, then confirmed (D109). The leading explanation —
inferred from ZigZag's own logged spatial-mapping choices, *not* from reading its mapper — is
that `lanes == C` lets it unroll the reduction loop entirely, a structural win
`evaluators/rtl`'s fixed K-unrolled `mac_array.sv` schedule never gets.

**Why this lives here and not in `calibration/`.** The predicate needs a compute node's `dims`
and an einsum op's reduction extent. Teaching L3 calibration to parse architecture internals is
exactly the coupling the Evaluator ABI exists to prevent, so calibration stays ignorant: callers
hold both documents already, ask this question, and pass the resulting `caveat` string to
`record_conformance_residuals`. The store then excludes those records by default
(`residual_stats(exclude_caveated=True)`), so one unrepresentative point can't drag the pooled
mean or inflate the spread.

Deliberately a *statement about ZigZag alone* — "can ZigZag fully unroll this reduction
dimension spatially?" — with no reference-backend knowledge in it. The fact that RTL happens not
to benefit is what makes the residual anomalous, but that is the caller's comparison to make,
not this module's.
"""

from __future__ import annotations

import re
from typing import Any

# Same bilinear grammar `workload_translator` accepts ("a b, b c -> a c"); the reduction dim is
# the one shared by both inputs and absent from the output.
_EXPR_RE = re.compile(r"^\s*([\w\s]+?)\s*,\s*([\w\s]+?)\s*->\s*([\w\s]+?)\s*$")

CAVEAT = "zigzag-reduction-dim-fully-unrolled"
"""The caveat string callers should record for such a residual — a stable identifier, so records
written by different callers land in one recognisable group."""


def reduction_dims(op: dict[str, Any]) -> list[str]:
    """The einsum's reduction dims: shared by both inputs, absent from the output. `[]` for an op
    this module can't parse — a caller asking a question about an unparseable op gets "no", not
    an exception, since this is an advisory predicate, not a translation step."""
    expr = op.get("expr") if isinstance(op, dict) else None
    match = _EXPR_RE.match(expr if isinstance(expr, str) else "")
    if not match:
        return []
    in1, in2, out = (group.split() for group in match.groups())
    return [d for d in in1 if d in in2 and d not in out]


def fully_unrolls_reduction_dim(workload: dict[str, Any], arch: dict[str, Any] | None) -> bool:
    """True when this (workload, architecture) pair sits on the `lanes == C` diagonal — the
    compute array's single spatial width exactly equals an einsum op's reduction extent, so
    ZigZag can unroll that reduction loop completely.

    Conservative by construction: any shape this can't confidently identify (no architecture, no
    single-dim compute node, an unparseable expr, a non-integer bound) returns `False`, so a
    residual is only ever *excluded* from calibration on a positive, well-understood match.
    Never raises — callers use this to decide whether to attach a caveat, and an advisory
    predicate that can fail a real conformance run would be worse than one that says "no".
    """
    if not isinstance(arch, dict) or not isinstance(workload, dict):
        return False

    # Every access below tolerates a present-but-null key: `.get("attrs", {})` returns None when
    # the key exists with a null value, and the two CHIA callers do no IR validation before asking
    # (docs/decisions.md D112 — the "never raises" contract was not actually met, and the callers
    # guard only ImportError, so a malformed document killed a real conformance run).
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

    # EVERY einsum op must be on the diagonal, not merely one (docs/decisions.md D112). With
    # `any`, a ten-op workload with one matching op had its whole residual excluded from
    # calibration even though nine ops were off-diagonal — a false positive that silently shrinks
    # the pool. The residual is a property of the run as a whole, so only a run that is entirely
    # on the diagonal is unrepresentative of the off-diagonal regime.
    for op in einsum_ops:
        bounds = op.get("bounds") or {}
        if not isinstance(bounds, dict):
            return False
        if not any(bounds.get(dim) == lanes for dim in reduction_dims(op)):
            return False
    return True


def caveat_for(workload: dict[str, Any], arch: dict[str, Any] | None) -> str | None:
    """`CAVEAT` when this pair is on the diagonal, else `None` — the exact shape
    `record_conformance_residuals(caveat=...)` takes, so a caller is one call away from doing the
    right thing."""
    return CAVEAT if fully_unrolls_reduction_dim(workload, arch) else None
