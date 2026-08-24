"""`flux_interconnect_mapping_dse_loop` -- the banked-L1 conflict study as one agent-callable node.

Twelve tensor storage modes against 32 single-ported banks under 28R+24W ports: map
policies (hash + placement + schedule) crossed with the interconnects the little loop finds,
judged on a four-cost Pareto (area, padding, latency, throughput) with certificates by
exhaustion and a train/holdout split against overfitting (docs/decisions.md D378-D386).
The study is a DOCUMENT (`applications/interconnect_mapping/interconnect_mapping.problem.yaml`,
review 2 step R3): this node builds it with the call's ask, runs the loop and returns the
study's record. Optional model rounds propose XOR hashes; every proposal passes an exact
GF(2) injectivity gate or is refused with the reason.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction


@ChiaFunction()
def flux_interconnect_mapping_dse_loop(
    seed: int = 0,
    *,
    ops: int = 8,
    climb_rounds: int = 40,
    llm_rounds: int = 0,
    llm_model: str | None = None,
    vu_probability: float = 0.7,
    dma_probability: float = 0.6,
    coordination_rounds: int = 2,
    screen_only: bool = True,
    db_path: str | None = None,
    feedback: Any | None = None,
) -> dict[str, Any]:
    """Run the interconnect_mapping study end to end and return its full record:
    every map-policy x fabric design point with train AND holdout metrics, the 4-cost
    Pareto front (pair names), certificates (PROVED by exhaustion over all tile
    origins, or refuted with the exact counterexample), the decision-first conclusion,
    and any refused hash proposals with reasons.

    Args:
        seed: Workload-generation seed; train and holdout use disjoint ranges of it.
        ops: MU operations per workload (each contributes several system steps).
        climb_rounds: Injectivity-gated XOR tap hill-climb rounds (0 disables).
        llm_rounds: Model-proposed hash rounds (0 disables; the study runs fully
            without a model).
        llm_model: The model tag for proposals, or omit for the default.
        vu_probability: Chance VU traffic joins a system step (regime knob).
        dma_probability: Chance a DMA stream joins an operation (regime knob).
        coordination_rounds: Big-loop rounds alternating the two little loops (D386).
        screen_only: Stop at the cycle law; False also runs the finalists' hash blocks
            and fabric elements through Yosys + OpenSTA (the document's `phys` stage).
        db_path: Campaign record (SQLite): trials, refusals, conclusions; a resumed
            campaign's proposer starts from what the record shows (D397).
        feedback: An operator channel (D388), when a caller has one.
    """
    from pathlib import Path

    import yaml
    from flux_loop import PromptProblem, TaskSpec, request_for, run_loop

    from ._loop_glue import ollama_proposer, optional_proposer

    # the study's document with this call's ask; another ask than the document's is another
    # campaign, so the block keeps its `name` only for the document's own ask (D539)
    doc_path = (Path(__file__).resolve().parents[4] / "applications" / "interconnect_mapping"
                / "interconnect_mapping.problem.yaml")
    doc = yaml.safe_load(doc_path.read_text())
    doc["params"] = {**doc["params"], "seed": int(seed), "ops": int(ops), "climb_rounds": int(climb_rounds),
                     "llm_rounds": int(llm_rounds), "vu_probability": float(vu_probability),
                     "dma_probability": float(dma_probability), "coordination_rounds": int(coordination_rounds)}
    block = dict(doc.get("campaign") or {})
    ask = {k: doc["params"][k] for k in block if k != "name"}
    if any(block.get(k) != v for k, v in ask.items()):
        block.pop("name", None)
    doc["campaign"] = {**block, **ask}
    prob = PromptProblem(TaskSpec.from_dict(doc, base=doc_path.parent))
    request = request_for(prob.task, db=db_path or "", steps=2 + int(llm_rounds) + int(coordination_rounds),
                          screen_only=bool(screen_only))
    proposer = optional_proposer(llm_rounds, lambda: ollama_proposer(llm_model))
    out = run_loop(prob, request, proposer=proposer, feedback=feedback, log=lambda _m: None)
    world = prob.world
    return {**world.study(out).to_dict(), "conclusion": world.conclude(out),
            "decision": out.decision.name if out.decision is not None else None,
            "decided_by": out.decided_by, "lessons": list(out.lessons),
            "not_established": list(out.not_established), "report": world.report(out)}
