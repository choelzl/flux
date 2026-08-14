"""`flux_check_validity` — a fifth CHIA node, added alongside docs/agent-surface.md's original four
(docs/decisions.md D9/D10): evaluate a candidate, then overlay an *independently*-computed
`Validity` (`flux_validity`, sharing no code with any evaluator adapter) onto the result — closing
docs/gap-analysis.md G14's gap ("validity computed independently of the cost model") that every evaluator's
own `Validity(ok=True, checker_version="none-v0.1")` self-report left open since Phase 1.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_cli.registry import DEFAULT_METRICS, make_evaluator
from flux_evaluator_abi import Budget, Candidate, Result
from flux_validity import check_independent_validity, merge_validity


@ChiaFunction()
def flux_check_validity(
    backend: str,
    workload: dict[str, Any],
    arch: dict[str, Any] | None = None,
    mapping: dict[str, Any] | None = None,
    metrics: list[str] | None = None,
) -> Result:
    """Evaluate a candidate through a named backend, then merge the evaluator's own self-reported
    `Validity` with `flux_validity.check_independent_validity`'s finding — merged, not replaced,
    so a real evaluator-internal self-check (e.g. `evaluators/rtl`'s comparison against a Python
    golden reference) isn't discarded, but `ok=True` can no longer mean "the evaluator says so"
    alone: `checker_version` names every check that actually ran, and `ok` is `False` if any of
    them found a violation.
    """
    evaluator = make_evaluator(backend)
    candidate = Candidate(workload=workload, arch=arch, mapping=mapping)
    requested_metrics = frozenset(metrics) if metrics is not None else DEFAULT_METRICS
    result = evaluator.evaluate(candidate, Budget(), requested_metrics)

    independent_validity = check_independent_validity(workload, arch, result)
    merged_validity = merge_validity(result.validity, independent_validity)
    return dataclasses.replace(result, validity=merged_validity)
