"""`flux_interconnect_mapping_dse_loop` -- the banked-L1 conflict study as one agent-callable node.

Twelve tensor storage modes against 32 single-ported banks under 28R+24W ports: map
policies (hash + placement + schedule) crossed with twelve interconnect topologies,
judged on a four-cost Pareto (area, padding, latency, throughput) with per-scope
conflict breakdowns, certificates by exhaustion, and a train/holdout split against
overfitting (docs/decisions.md D378-D383). Optional model rounds propose XOR hashes;
every proposal passes an exact GF(2) injectivity gate or is refused with the reason.
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
    db_path: str | None = None,
) -> dict[str, Any]:
    """Run the interconnect_mapping study end to end and return its full record:
    every map-policy x fabric design point with train AND holdout metrics, the 4-cost
    Pareto front (pair names), certificates (PROVED by exhaustion over all tile
    origins, or refuted with the exact counterexample and whether the bank or the
    fabric failed), and any refused hash proposals with reasons.

    Args:
        seed: Workload-generation seed; train and holdout use disjoint ranges of it.
        ops: MU operations per workload (each contributes several system steps).
        climb_rounds: Injectivity-gated XOR tap hill-climb rounds (0 disables).
        llm_rounds: Model-proposed hash rounds via a local Ollama (0 disables; the
            study runs fully without a model).
        llm_model: Ollama tag for proposals, or omit for the default local model.
        vu_probability: Chance VU traffic joins a system step (regime knob).
        dma_probability: Chance a DMA stream joins an operation (regime knob).
        db_path: Campaign record (SQLite): trials, refusals, conclusions; a resumed
            campaign's proposer starts from what the record shows (D397).
    """
    from flux_imapping import run_study
    from flux_imapping.flow import conclude

    proposer = None
    if llm_rounds > 0:
        from flux_llm import NativeOllamaProposer

        proposer = NativeOllamaProposer(model=llm_model)
    study = run_study(seed=seed, ops=ops, climb_rounds=climb_rounds,
                      llm_rounds=llm_rounds, proposer=proposer,
                      vu_probability=vu_probability,
                      dma_probability=dma_probability, db_path=db_path)
    return {**study.to_dict(), "conclusion": conclude(study)}
