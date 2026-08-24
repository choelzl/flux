"""The prefetcher problem the tests work on, built the way `flux task run` and the CHIA node
build it: `applications/prefetcher/prefetcher.problem.yaml` with its `params:` patched (D519,
D533; review 2 step R3). The simulator is injected (`measure_batch`), so the whole policy chain
runs in a second on a machine with no traces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

PREFETCHER_DOC = (Path(__file__).resolve().parents[2] / "applications" / "prefetcher"
                  / "prefetcher.problem.yaml")


def prefetcher_problem(**params: Any):
    """The document problem with these params. A test's ask is not the document's campaign: no
    name, so the record -- when a test asks for one -- is keyed by the ask."""
    import yaml
    from flux_loop import PromptProblem, TaskSpec

    doc = yaml.safe_load(PREFETCHER_DOC.read_text())
    doc["params"] = {**doc["params"], **params}
    doc["campaign"] = {}
    return PromptProblem(TaskSpec.from_dict(doc, base=PREFETCHER_DOC.parent))


def run_study(*, db: str = "", measure_batch: Any = None, proposer: Any = None, propose: Any = None,
              invent: Any = None, feedback: Any = None, finalists: int = 2, screen_only: bool = False,
              workers: int = 4, steps: int | None = None, log: Any = None, **params: Any):
    """The study end to end, as the old `run_study` ran it: the document with `params`, the
    world's backend and hooks injected, the loop with enough steps for every batch the policies
    yield. Returns the world's `result(out)` dict with the loop's result and the problem beside
    it (`result["out"]`, `result["problem"]`)."""
    from flux_loop import request_for, run_loop

    prob = prefetcher_problem(**params)
    world = prob.world
    world.measure_backend = measure_batch
    world.propose_fn = propose
    world.invent_fn = invent
    r = world.request
    n = steps or (8 + (r.measurements // 2 + 6) * 2 + r.compose_rounds + r.tune_partners + r.measurements)
    request = request_for(prob.task, db=db, steps=n, finalists=max(0, int(finalists)),
                          screen_only=bool(screen_only) or int(finalists) <= 0, workers=int(workers))
    out = run_loop(prob, request, proposer=proposer, feedback=feedback, log=log or (lambda _m: None))
    result = world.result(out)
    result["out"], result["problem"] = out, prob
    return result


def identity(prob, db: str = "") -> dict[str, Any]:
    """The identity document the record was opened under, for `Records(db, objective=...)`."""
    from flux_loop import request_for

    return prob.objective(request_for(prob.task, db=db))
