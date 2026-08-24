"""`flux_nlu_dse_loop` -- the FP16 non-linear-unit study as a dispatchable CHIA node (D408).

Same shape as the other studies' nodes: `run_study` unchanged, its result mapped to
JSON so a parent orchestrator gets the decision -- PPA, fmax, per-operator error
rates, and the winning design's declared knobs -- without importing `flux_nlu`.
The model role is constructed here exactly as the demo constructs it; `llm_rounds=0`
re-judges the campaign record's designs with no model at all.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction


def _scored_payload(x) -> dict[str, Any]:
    return {
        "name": x.name, "style": x.candidate["style"],
        "method": x.candidate["method"], "latency": x.candidate["latency"],
        "area_um2": round(x.area_um2, 1), "fmax_mhz": round(x.fmax_mhz, 1),
        "power_w": x.power_w, "flow_depth": x.flow_depth,
        "max_ulp": x.max_ulp, "error_rate": x.error_rate,
        "per_op": {op: {"max_ulp": r["max_ulp"], "error_rate": r["error_rate"]}
                   for op, r in x.per_op.items()},
    }


@ChiaFunction()
def flux_nlu_dse_loop(
    db_path: str = "demo-nlu.db",
    *,
    ops: list[str] | None = None,
    ulp_budget: int = 1,
    llm_rounds: int = 4,
    test_rounds: int = 1,
    clock_period_ps: float = 1250.0,
    target_mhz: float | None = None,
    decide_on_finalists: int = 3,
    screen_only: bool = False,
    llm_model: str | None = None,
    feedback: Any | None = None,
) -> dict[str, Any]:
    """Design an FP16 non-linear unit (exp/log/sigmoid/tanh/gelu/recip/rsqrt).

    The model chooses the computation method per operator (LUT, piecewise
    polynomial, interpolation, Newton-Raphson, CORDIC, ...), shared vs per-op
    hardware, and combinational vs pipelined depth; it also authors adversarial
    unit-test vectors. The gate is exhaustive: every operator within `ulp_budget`
    ULP of the FP16 reference on all 65536 inputs, or refused with the failing
    inputs. Survivors are screened by yosys/STA and finalists placed by OpenROAD
    for PPA (area, fmax, power) on ASAP7. Resume re-judges the record's designs
    and reads its conclusions, duels, refusals and authored tests back.
    """
    from flux_nlu import DEFAULT_OPS, NluRequest, run_study

    request = NluRequest(
        db=db_path, ops=tuple(ops) if ops else DEFAULT_OPS,
        ulp_budget=int(ulp_budget), llm_rounds=int(llm_rounds),
        test_rounds=int(test_rounds), clock_period_ps=float(clock_period_ps),
        target_mhz=target_mhz, decide_on_finalists=int(decide_on_finalists),
        screen_only=bool(screen_only))
    proposer = None
    if llm_rounds > 0 or test_rounds > 0:
        try:
            from flux_llm import NativeOllamaProposer

            proposer = NativeOllamaProposer(model=llm_model, num_ctx=16384)
        except Exception:  # noqa: BLE001 -- no model: the record-replay path still runs
            proposer = None
    result = run_study(request, proposer=proposer, feedback=feedback)
    return {
        "decision": _scored_payload(result.decision) if result.decision else None,
        "decided_by": result.decided_by,
        "frontier": [_scored_payload(x) for x in (result.confirmed or result.frontier)],
        "refused": [{"who": n, "why": w} for n, w in result.refused],
        "lessons": result.lessons,
        "not_established": result.not_established,
        "provenance": result.provenance,
    }
