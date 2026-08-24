"""D512: the loop-level report -- how a campaign moved, from the record and the objective
vector on it. A two-part problem with two objectives runs two passes; the page shows the
fronts by pass with their hypervolume, the best so far, the parts, and the passes."""

from __future__ import annotations

from flux_llm import Reply
from flux_loop import BuildError, Candidate, LoopRequest, Objective, Objectives, Problem, Verdict, run_loop
from flux_loop.report import best_so_far, fronts_by_pass, load, render, write


class Pair(Problem):
    """Two parts, each an int; the whole is their sum; measured: `value` (the sum, higher is
    better, goal 10) and `cost` (the product)."""

    name = "pair"

    def subgoals(self):
        return ["a", "b"]

    def objectives(self):
        return Objectives([Objective("value", "maximize", goal=10.0, stage="screen"), Objective("cost", "minimize")])

    def design_prompt(self, subgoal, method, state, human, prior, prior_why):
        return f"design {subgoal}", None

    def parse_design(self, reply, subgoal):
        return Candidate(f"{subgoal}-v", reply.strip(), subgoal=subgoal), ""

    def build(self, cand, subgoal, state):
        try:
            return int(cand.artifact)
        except ValueError as exc:
            raise BuildError(str(exc)) from exc

    def judge(self, built, cand, subgoal, state):
        return Verdict(built > 0, 0.0 if built > 0 else 1.0, "" if built > 0 else "not positive")

    def compose(self, admitted, state):
        if set(admitted) != {"a", "b"}:
            return None
        a, b = int(admitted["a"].artifact), int(admitted["b"].artifact)
        return Candidate("whole", str(a + b), knobs={"a": a, "b": b})

    def stages(self):
        return ["screen"]

    def measure(self, cand, stage, state):
        if "a" in cand.knobs:
            return {"value": float(cand.knobs["a"] + cand.knobs["b"]), "cost": float(cand.knobs["a"] * cand.knobs["b"])}
        v = int(cand.artifact)
        return {"value": float(v), "cost": float(v * v)}


class Answers:
    """The design for each part by its prompt; anything else the loop asks gets "{}"."""

    def __init__(self, a, b):
        self.by = {"design a": str(a), "design b": str(b)}

    def propose(self, prompt, *, schema=None, tools=None, budget=None):
        return Reply(next((v for k, v in self.by.items() if k in prompt), "{}"))


def _pass(db, a, b):
    return run_loop(Pair(), LoopRequest(db=db, steps=3, prototype=False, critique_rounds=0, regenerate=("*",)),
                    proposer=Answers(a, b), log=lambda _m: None)


def test_the_report_reads_the_vector_from_the_record_and_draws_the_passes(tmp_path):
    db = str(tmp_path / "pair.db")
    out1 = _pass(db, 2, 3)
    assert out1.decision is not None and out1.decision.metrics["value"] == 5.0
    out2 = _pass(db, 4, 7)                       # the second pass: a better whole (11 >= the goal)
    assert out2.decision.metrics["value"] == 11.0
    rep = load(db)
    assert rep.objectives.describe() == "value >= 10 (screen), then least cost"   # from the record, no flag
    assert len(rep.passes) == 2 and rep.stage == "screen"
    wholes = [r for r in rep.rows if r.whole]
    assert [r.metrics["value"] for r in wholes] == [5.0, 11.0] and all(r.parts == ("a", "b") for r in wholes)
    fam = fronts_by_pass(rep)
    assert len(fam) == 2 and fam[0][1] == [(-5.0, 6.0)] and fam[1][2] > fam[0][2] >= 0.0   # the hypervolume grew
    assert fam[1][1] == [(-11.0, 28.0), (-5.0, 6.0)]              # both stand on the front (value up, cost up)
    assert [v for _t, v in best_so_far(rep, None, rep.objectives[0])] == [5.0, 11.0]
    assert best_so_far(rep, "a", rep.objectives[0]) == []       # no part is measured alone here (the NLU's are)
    page = render(rep)
    for text in ("Frontier evolution", "hypervolume dominated", "the whole: best value so far", "the whole: best cost so far",
                 "The passes", "goal 10"):
        assert text in page, text
    assert page.count("<svg") == 4 and "best value so far, and what moved it" not in page   # fronts, hypervolume, 2 best-so-far


def test_a_record_without_the_vector_takes_the_flag_or_the_first_metrics(tmp_path):
    db = str(tmp_path / "pair.db")
    _pass(db, 2, 3)
    # strip the vector the loop wrote, as a record from before D512 would lack it
    import sqlite3

    con = sqlite3.connect(db)
    con.execute("DELETE FROM campaign_events WHERE kind = 'decided:objectives'")
    con.commit(); con.close()
    rep = load(db)
    assert rep.objectives.describe() in ("most value, then most cost", "most cost, then most value")
    assert any("first two metrics measured stand in" in n for n in rep.notes)
    given = Objectives([Objective("cost", "minimize"), Objective("value")])
    rep2 = write(db, str(tmp_path / "r.html"), objectives=given)
    assert rep2.objectives is given and (tmp_path / "r.html").read_text().count("<svg") >= 2


def test_flux_report_writes_the_page(tmp_path, capsys):
    import argparse

    from flux_cli.commands import cmd_report

    db = str(tmp_path / "pair.db")
    _pass(db, 2, 3)
    out = str(tmp_path / "page.html")
    args = argparse.Namespace(db=db, campaign=None, objective=["value:max:10:screen", "cost:min"], out=out)
    assert cmd_report(args) == 0
    said = capsys.readouterr().out
    assert "objective value >= 10 (screen), then least cost" in said and "wrote" in said
    assert "<h1>" in open(out).read()


def test_the_report_draws_every_pair_beside_the_first(tmp_path):
    """D520 (Cedric: "both are valid, it depends on the experiment"): fmax against area AND
    fmax against power, one chart each, from the same rows."""
    from flux_loop import Objective, Objectives
    from flux_loop.report import Report, _svg_fronts, fronts_by_pass

    objs = Objectives([Objective("fmax_mhz", "maximize", goal=800, stage="confirm"), Objective("area_um2", "minimize"),
                       Objective("power_w", "minimize")])
    from flux_loop.report import Row

    rows = [Row(when=1.0 + i, stage="confirm", part=None, whole=True, name=f"c{i}", metrics={"fmax_mhz": 700 + 50 * i, "area_um2": 100 + 10 * i, "power_w": 0.01 * (3 - i)})
            for i in range(3)]
    rep = Report("abc", None, objs, rows, [(2.5, {}), (4.5, {})], [], [])
    assert fronts_by_pass(rep, 1) and fronts_by_pass(rep, 2) and fronts_by_pass(rep, 3) == []
    page = render(rep)
    assert page.count("frontier evolution:") == 2 and "fmax_mhz against power_w" in page
    assert "power_w" in _svg_fronts(rep, 2) and "area_um2" in _svg_fronts(rep, 1)
