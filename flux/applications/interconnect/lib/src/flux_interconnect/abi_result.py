"""The ABI `Result` the interconnect stages return (D440): one builder for the structural screen
and the physical stage, which had carried it byte for byte each."""

from __future__ import annotations

from flux_evaluator_abi import (Bottleneck, Constraint, Domain, Escalation, Estimate, Limiter,
                                Provenance, Result, Validity)

__all__ = ["abi_result"]


def abi_result(metrics: dict[str, Estimate], evaluator: str, *, limiter: Limiter,
               inputs: dict[str, str], ok: bool = True, detail: str = "") -> Result:
    return Result(
        metrics=metrics,
        validity=Validity(
            ok=ok, checker_version="interconnect@0.1",
            violations=() if ok else (Constraint(kind="fmax_mhz", detail=detail),),
        ),
        domain=Domain(in_domain=True),
        bottleneck=Bottleneck(limiter=limiter),
        provenance=Provenance(evaluator=evaluator, inputs=inputs),
        escalation=Escalation(recommended=False),
    )
