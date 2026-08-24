"""Independent validity checking (docs/gap-analysis.md G14, docs/ir.md/docs/evaluator-abi.md): computes `Validity`
from the Workload/Architecture IR and a `Result`'s reported metrics, sharing no code with any
evaluator adapter — the anti-reward-hacking guarantee this repo has named as a gap since Phase 1
(every adapter has shipped `Validity(ok=True, checker_version="none-v0.1")` until now).
"""

from __future__ import annotations

from typing import Any

from flux_evaluator_abi import Result, Validity

from .constraints import check_declared_constraints
from .roofline import NotIndependentlyCheckable, check_physical_validity

__all__ = [
    "check_declared_constraints",
    "check_physical_validity",
    "check_independent_validity",
    "merge_validity",
    "NotIndependentlyCheckable",
]


def check_independent_validity(
    workload: dict[str, Any], arch: dict[str, Any] | str | None, result: Result
) -> Validity:
    """Runs every independent check this package implements and combines them.

    The declared-constraints check always runs (it degrades to a vacuous `checked=0/0` pass when
    `arch` has no `constraints` block, not an error). The roofline check is scope-limited (a
    single two-operand `einsum` op, a single-spatial-dim architecture) — when it doesn't apply,
    that's recorded honestly as `roofline-v0.1:not_applicable(...)` in `checker_version` rather
    than silently omitted or treated as a pass on the merits.
    """
    constraints_validity = check_declared_constraints(arch, result)
    try:
        roofline_validity = check_physical_validity(workload, arch, result)
    except NotIndependentlyCheckable as exc:
        roofline_validity = Validity(
            ok=True, violations=(), checker_version=f"roofline-v0.1:not_applicable({exc})"
        )
    return merge_validity(constraints_validity, roofline_validity)


def merge_validity(*validities: Validity) -> Validity:
    """Combine multiple independently-computed `Validity` objects into one: `ok` is the AND of
    all of them, `violations` is their concatenation, `checker_version` joins each one's own —
    preserving every check's finding rather than one overwriting another.
    """
    violations = tuple(v for validity in validities for v in validity.violations)
    return Validity(
        ok=all(validity.ok for validity in validities),
        violations=violations,
        checker_version="+".join(validity.checker_version for validity in validities),
    )
