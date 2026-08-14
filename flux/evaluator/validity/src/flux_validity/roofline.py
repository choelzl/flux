"""An independent, first-principles physical-plausibility check: no evaluator can report a
latency lower than the compute-bound minimum implied by the workload's own total MAC count and
the architecture's own declared lane count — `total_macs / lanes` cycles, at best one
multiply-accumulate per lane per cycle. Below that is not "optimistic," it's physically
impossible, regardless of which cost model produced the number.

This is the same arithmetic docs/phase1-exit-criterion-report.md already did by hand for
`mlp-gemm0.yaml` on an 8-wide array (`4 * 32 * 32 / 8 = 512` cycles — Timeloop's mapper found
exactly that; ZigZag's LOMA search found 1554, still comfortably above the bound; real Verilator
RTL measured 529, a few cycles above the ideal bound for pipeline fill) — formalised here as a
real, callable check instead of a one-off calculation in a report.

Deliberately does not import any evaluator adapter (`evaluators/rtl`, `evaluators/systemc`, ...)
even though they extract the same shape from Workload/Architecture IR — re-implemented minimally
here so this check shares no code path with anything it might be checking. Same narrow v0.1 scope
those adapters already impose (a single two-operand `einsum` op; a single-spatial-dim
architecture) — not a new limitation, the same one this repo has used since Phase 2.
"""

from __future__ import annotations

from typing import Any

from flux_evaluator_abi import Constraint, Result, Validity


class NotIndependentlyCheckable(Exception):
    """Raised when `workload`/`arch` fall outside this check's scope, or `result` doesn't carry
    the metric this check needs — fail loudly rather than silently reporting a pass for a
    candidate this check never actually looked at.
    """


def check_physical_validity(
    workload: dict[str, Any], arch: dict[str, Any] | str | None, result: Result
) -> Validity:
    """Raises `NotIndependentlyCheckable` if out of scope; otherwise returns a `Validity` whose
    `ok` is `False` only if the result's `latency_cycles` value is below the compute-bound
    minimum cycle count — a genuine physical impossibility, not a quality judgement.
    """
    if "latency_cycles" not in result.metrics:
        raise NotIndependentlyCheckable(
            "result has no latency_cycles metric — nothing for this check to compare against"
        )
    total_macs = _extract_total_macs(workload)
    lanes = _extract_lanes(arch)
    lower_bound = total_macs / lanes

    value = result.value_of("latency_cycles")
    if value < lower_bound:
        return Validity(
            ok=False,
            violations=(
                Constraint(
                    kind="latency_cycles_roofline",
                    detail=(
                        f"reported latency_cycles={value} is below the compute-bound minimum "
                        f"{lower_bound:.1f} cycles for {total_macs} total MACs at {lanes} "
                        "lanes/cycle — physically impossible at one MAC per lane per cycle"
                    ),
                ),
            ),
            checker_version=f"roofline-v0.1:lower_bound={lower_bound:.1f}",
        )
    return Validity(ok=True, violations=(), checker_version=f"roofline-v0.1:lower_bound={lower_bound:.1f}")


def _extract_total_macs(workload: dict[str, Any]) -> int:
    ops = workload.get("ops", [])
    einsum_ops = [op for op in ops if op.get("kind") == "einsum"]
    if len(einsum_ops) != 1:
        raise NotIndependentlyCheckable(
            f"expected exactly one 'einsum' op, found {len(einsum_ops)} — this check's v0.1 scope "
            "matches evaluators/rtl's own single-op limit, not a new restriction"
        )
    bounds = einsum_ops[0].get("bounds", {})
    if len(bounds) != 3:
        raise NotIndependentlyCheckable(
            f"expected a 3-dim two-operand contraction (e.g. 'B C, C K -> B K'), found "
            f"{len(bounds)} bound dims — total-MAC-count-by-product only holds for this shape"
        )
    total = 1
    for extent in bounds.values():
        total *= extent
    return total


def _extract_lanes(arch: dict[str, Any] | str | None) -> int:
    if not isinstance(arch, dict):
        raise NotIndependentlyCheckable(
            "no inline Architecture IR document (arch is None or an unresolved content hash) — "
            "cannot form an independent opinion on lane/PE count"
        )
    hierarchy = arch.get("hierarchy", [])
    compute_nodes = [n for n in hierarchy if n.get("class") == "compute"]
    if len(compute_nodes) != 1:
        raise NotIndependentlyCheckable(
            f"expected exactly one compute hierarchy node, found {len(compute_nodes)}"
        )
    dims = compute_nodes[0].get("attrs", {}).get("dims", {})
    if len(dims) != 1:
        raise NotIndependentlyCheckable(
            f"expected exactly one spatial array dimension, found {len(dims)}"
        )
    (_dim_name, lanes), = dims.items()
    return lanes
