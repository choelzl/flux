"""The interconnect_mapping study run the way `flux task run` runs it (D800: a document and the
commands of `flux_imapping.steps`): the document copied with its `params:` patched, its passes
run until the search rests, and the result read back in the study's own terms."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP = Path(__file__).resolve().parents[2] / "applications" / "interconnect_mapping"


def imapping_problem(home: Path | None = None, **params: Any):
    """The document problem with these params, in its own folder (`home`, or a fresh one)."""
    import yaml
    from flux_loop import PromptProblem, load_task

    home = home or Path(tempfile.mkdtemp(prefix="imapping-")) / "interconnect_mapping"
    home.mkdir(parents=True, exist_ok=True)
    doc = yaml.safe_load((APP / "problem.yaml").read_text())
    doc["params"] = {**doc["params"], **params}
    (home / "problem.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    return PromptProblem(load_task(home / "problem.yaml"))


@dataclass
class Study:
    """The study in its own terms: every scored pair, the front, its certificates, the refusals,
    the operator's notes, the lessons."""

    scored: list
    front: list
    certificates: list
    refused: list[str]
    notes: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    problem: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"scored": [s.to_dict() for s in self.scored], "front": [s.pair_name for s in self.front],
                "certificates": [{"solution": c.solution, "mode": c.mode, "tile": list(c.tile), "holds": c.holds}
                                 for c in self.certificates],
                "refused": list(self.refused), "notes": list(self.notes)}


def run_study(*, db: str = "", feedback: Any = None, screen_only: bool = True, runs: int = 8, **params: Any) -> Study:
    """The study end to end: a pass a round of its search until it rests, the cycle law only
    unless asked."""
    from flux_imapping.flow import score
    from flux_imapping.steps import _traffic, ask_of, certificates, read_pair
    from flux_imapping.model import Memory
    from flux_loop import request_for, run_loop

    prob = imapping_problem(**params)
    db = db or str(Path(prob.task.home) / "out" / "study.db")
    outs = []
    for _ in range(runs):
        out = run_loop(prob, request_for(prob.task, db=db, screen_only=screen_only), feedback=feedback,
                       log=lambda _m: None)
        outs.append(out)
        if out.stopped and "nothing left" in str(out.stopped) and len(outs) > 1:
            break
    last = outs[-1]                                      # its lessons: the search's conclusion
    ask = ask_of(dict(prob.task.params))
    mem = Memory(m=int(ask["bank_bits"]))
    train, holdout = _traffic(ask)

    def rebuilt(rows) -> list:
        seen, got = set(), []
        for p in rows:
            if p.stage != "analytic" or p.name in seen:
                continue
            seen.add(p.name)
            work = Path(tempfile.mkdtemp()) / "pair.json"
            work.write_text(p.candidate.artifact)
            sol, fabric = read_pair(str(work), mem)
            got.append(score(sol, fabric, train, holdout, mem))
        return got

    from flux_imapping.flow import pareto_front

    scored = rebuilt([p for out in outs for p in out.scored if p.candidate.artifact])
    front = pareto_front(scored)
    return Study(scored=scored, front=front, certificates=certificates(front, ask, mem),
                 refused=[why for out in outs for _n, why in out.refused],
                 notes=[n for out in outs for n in out.notes], lessons=list(last.lessons), problem=prob)


def identity(prob, db: str = "") -> dict[str, Any]:
    """The identity document the record was opened under, for `Records(db, objective=...)`."""
    from flux_loop import request_for

    return prob.objective(request_for(prob.task, db=db))
