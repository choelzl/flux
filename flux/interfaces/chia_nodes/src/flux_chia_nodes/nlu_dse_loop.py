"""`flux_nlu_dse_loop` -- the FP16 non-linear-unit study as a dispatchable CHIA node (D408).

Same shape as the other studies' nodes: `run_study` unchanged, its result mapped to
JSON so a parent orchestrator gets the decision -- PPA, fmax, per-operator error
rates, and the winning design's declared knobs -- without importing `flux_nlu`.
The model role is constructed here exactly as the demo constructs it; `op_steps=0`
re-judges the campaign record's designs with no model at all.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction


def _scored_payload(problem: Any):
    """The loop's `Scored` (D519: the document problem's result) as the node's JSON row --
    the design's style, method and latency, its numbers, and the world's per-operator
    exhaustive report."""
    def payload(x) -> dict[str, Any]:
        c, m = x.candidate, x.metrics or {}
        per_op = getattr(problem, "per_op", {}).get(c.name, {})
        rates = [float(r.get("error_rate", 0.0)) for r in per_op.values()]
        ulps = [r.get("max_ulp", 0) for r in per_op.values()]
        return {
            "name": c.name, "style": c.knobs.get("style", "shared"),
            "method": c.knobs.get("method", "?"), "latency": int((c.meta or {}).get("latency", 0)),
            "area_um2": round(float(m.get("area_um2", 0.0)), 1), "fmax_mhz": round(float(m.get("fmax_mhz", 0.0)), 1),
            "power_w": float(m.get("power_w", 0.0)),
            "flow_depth": "placement" if x.stage == "confirm" else "synthesis",
            "max_ulp": max(ulps, key=lambda v: (isinstance(v, str), v)) if ulps else 0,
            "error_rate": max(rates) if rates else 0.0,
            "per_op": {op: {"max_ulp": r.get("max_ulp"), "error_rate": r.get("error_rate")}
                       for op, r in per_op.items()},
        }
    return payload


@ChiaFunction()
def flux_nlu_dse_loop(
    db_path: str = "demo-nlu.db",
    *,
    ops: list[str] | None = None,
    ulp_budget: int = 1,
    test_rounds: int = 1,
    repair_attempts: int = 12,
    explore_every: int = 4,
    op_steps: int = 24,
    patching: bool = True,
    structured: bool = True,
    clock_period_ps: float = 1250.0,
    target_mhz: float | None = None,
    decide_on_finalists: int = 3,
    screen_only: bool = False,
    llm_model: str | None = None,
    num_predict: int = 6000,
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
    from pathlib import Path

    import yaml
    from flux_loop import PromptProblem, TaskSpec, request_for, run_loop
    from flux_nlu.fp16 import OPCODES

    # the NLU's document (D519) with this call's ask: its operators, gate and clock
    doc_path = Path(__file__).resolve().parents[4] / "applications" / "nlu" / "nlu.problem.yaml"
    doc = yaml.safe_load(doc_path.read_text())
    chosen = tuple(ops) if ops else tuple(OPCODES)
    order = [str(p) for p in doc["parts"]]
    doc["parts"] = [o for o in order if o in chosen] + [o for o in chosen if o not in order]
    doc["params"] = {**doc["params"], "ops": list(chosen), "ulp_budget": int(ulp_budget),
                     "clock_period_ps": float(clock_period_ps), "test_rounds": int(test_rounds)}
    # D539: the document's own campaign block, its identity keys set to this ask; the campaign
    # NAME stays only when the ask is the document's, so this node and `flux task run` open
    # the same record for the same ask and a different ask gets a record of its own
    ask = {"ops": list(chosen), "ulp_budget": int(ulp_budget), "clock_period_ps": float(clock_period_ps)}
    block = dict(doc.get("campaign") or {})
    if any(block.get(k) != v for k, v in ask.items()):
        block.pop("name", None)
    doc["campaign"] = {**block, **ask}
    doc["objectives"][0]["goal"] = float(target_mhz) if target_mhz else 1e6 / float(clock_period_ps)
    problem = PromptProblem(TaskSpec.from_dict(doc, base=doc_path.parent))
    request = request_for(problem.task, db=db_path, steps=int(op_steps), repair_attempts=int(repair_attempts),
                          explore_every=int(explore_every), patching=bool(patching), structured=bool(structured),
                          finalists=int(decide_on_finalists), screen_only=bool(screen_only))
    from ._loop_glue import loop_report, ollama_proposer, optional_proposer

    proposer = optional_proposer(
        max(op_steps, test_rounds),
        lambda: ollama_proposer(llm_model, num_predict=int(num_predict)))
    result = run_loop(problem, request, proposer=proposer, feedback=feedback, log=lambda _m: None)
    payload = _scored_payload(problem)
    return loop_report(result, payload, refused_key="who", frontier_from=None, extra={
        "frontier": [payload(x) for x in (result.confirmed or result.frontier)],
    })
