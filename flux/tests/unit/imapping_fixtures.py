"""The interconnect_mapping problem the tests work on, built the way `flux task run` and the
CHIA node build it: `applications/interconnect_mapping/interconnect_mapping.problem.yaml` with
its `params:` patched (D519, D533; review 2 step R3)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

IMAPPING_DOC = (Path(__file__).resolve().parents[2] / "applications" / "interconnect_mapping"
                / "interconnect_mapping.problem.yaml")


def imapping_problem(**params: Any):
    """The document problem with these params. A different ask than the document's (another
    seed, other traffic) is another campaign, so the campaign block loses its `name` exactly as
    the node's does (D539) and the record is keyed by the ask."""
    import yaml
    from flux_loop import PromptProblem, TaskSpec

    doc = yaml.safe_load(IMAPPING_DOC.read_text())
    doc["params"] = {**doc["params"], **params}
    block = dict(doc.get("campaign") or {})
    ask = {k: doc["params"][k] for k in block if k != "name"}
    if any(block.get(k) != v for k, v in ask.items()):
        block.pop("name", None)
    doc["campaign"] = {**block, **ask}
    return PromptProblem(TaskSpec.from_dict(doc, base=IMAPPING_DOC.parent))


def run_study(*, db: str = "", proposer: Any = None, feedback: Any = None, screen_only: bool = True,
              **params: Any):
    """The study end to end, as the old `run_study` ran it: the document with `params`, the
    loop with enough steps for every batch the world yields, the cycle law only unless asked.
    Returns the world's `Study` (scored, front, certificates, refused, notes) with the loop's
    result and the problem beside it."""
    from flux_loop import request_for, run_loop

    prob = imapping_problem(**params)
    world = prob.world
    steps = 2 + world.llm_rounds + world.coordination_rounds
    request = request_for(prob.task, db=db, steps=steps, screen_only=screen_only)
    out = run_loop(prob, request, proposer=proposer, feedback=feedback, log=lambda _m: None)
    return world.study(out)


def identity(prob, db: str = "") -> dict[str, Any]:
    """The identity document the record was opened under, for `Records(db, objective=...)`."""
    from flux_loop import request_for

    return prob.objective(request_for(prob.task, db=db))
