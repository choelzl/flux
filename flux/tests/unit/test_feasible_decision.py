"""D900: the decision is a feasible design only. `Objectives.decide` falls back to the design with the
fewest limits missed when none meets them all, and the loop published that as its decision: an
external review reproduced `speed >= 10` with designs at 2, 4 and 8 deciding on 8, and the CLI wrote
its artifact and exited 0. Now a pass with no design meeting every requirement (with the evidence of
the stage that judges it, D899) concludes with no decision and reports the closest apart; the
shortfall ranking still picks the standing design the next pass refines, and the refine/explore
cadence reads that standing design, decided or not."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

from test_decision_history import Drafts

from flux_loop import Candidate, LoopRequest, Objectives, Template, run_loop
from flux_loop.direction import stood


class Goal(Drafts):
    """One whole design a pass of `size` characters; `size <= 2` is required. Each draft notes which
    design it was handed (its parent)."""

    def __init__(self, size, limits=None):
        super().__init__(size)
        self.limits = limits or [{"metric": "size", "direction": "minimize", "goal": 2}]
        self.parents: list[str | None] = []

    def objectives(self):
        return Objectives.from_doc(self.limits)

    def generator(self, subgoal, state):
        def draft(attempt):
            self.parents.append(attempt.prior.name if attempt.prior else None)
            return Candidate(f"d{self.size}", artifact="x" * self.size)

        return Template(draft, name="agent:test")


def _pass(db, size, limits=None):
    prob, said = Goal(size, limits), []
    out = run_loop(prob, LoopRequest(db=db, prototype=False, critique_rounds=0), proposer=None, log=said.append)
    direction = next((m.split()[2] for m in said if "direction:" in m), None)
    return out, prob.parents, direction


def _conclusions(db):
    with sqlite3.connect(db) as c:
        return [json.loads(r[0]) for r in c.execute("SELECT detail_json FROM campaign_events WHERE kind = 'conclusion' ORDER BY id")]


def test_an_all_infeasible_pool_has_no_decision_and_a_closest_candidate(tmp_path):
    db = str(tmp_path / "d.db")
    out, _p, _d = _pass(db, 5)
    assert out.decision is None and out.closest is not None and out.closest.name == "d5"
    assert out.unmet == ["size 5 is above the limit 2 (place)"], out.unmet
    assert any("no design meets every requirement yet" in n for n in out.not_established)
    last = _conclusions(db)[-1]
    assert last["decision"] is None and last["no_decision"] == "no feasible design yet"
    assert last["closest"] == "d5" and last["unmet"] == out.unmet and last["standing"] == "d5"


def test_the_acceptance_scenario_only_the_qualifying_design_decides_and_lineage_is_kept(tmp_path):
    """The review's: A (5) then B refining A (4), both correct and short of the requirement, then C (1)
    qualifies; a later pass's D (3) does not displace it. Each pass one design; each refines the standing one."""
    db = str(tmp_path / "d.db")
    a, pa, _ = _pass(db, 5)
    b, pb, db_dir = _pass(db, 4)
    c, pc, dc_dir = _pass(db, 1)
    d, pd, dd_dir = _pass(db, 3)
    assert [a.decision, b.decision] == [None, None]
    assert [a.closest.name, b.closest.name] == ["d5", "d4"], "the closest is the standing design"
    assert pa == [None] and pb == ["d5"] and pc == ["d4"], "each pass refined the standing design: a parent with no decision"
    assert db_dir == "d5" and dc_dir == "d4"
    assert c.decision is not None and c.decision.name == "d1" and c.closest is None
    assert d.decision is not None and d.decision.name == "d1", "a newer design that does not qualify never displaces the best"
    assert pd == ["d1"] and dd_dir == "d1"
    rows = {s.candidate.name for s in d.scored} | {s.candidate.name for s in d.frontier}
    assert {"d1", "d3"} <= rows
    with sqlite3.connect(db) as con:                    # every design measured stays on the record
        named = {json.loads(r[0]).get("name") for r in con.execute("SELECT candidate_json FROM trials WHERE stage = 'place'")}
    assert {"d5", "d4", "d1", "d3"} <= named
    kinds = [(x["decision"], x.get("closest"), x.get("standing")) for x in _conclusions(db)]
    assert kinds == [(None, "d5", "d5"), (None, "d4", "d4"), ("d1", None, "d1"), ("d1", None, "d1")], kinds


def test_a_mixed_pool_decides_on_the_feasible_design(tmp_path):
    """A qualifying design first, then a pass whose new design is closer on nothing: the feasible stands."""
    db = str(tmp_path / "d.db")
    _pass(db, 2)
    out, _p, _d = _pass(db, 7)
    assert out.decision.name == "d2" and out.closest is None


def test_a_required_number_no_stage_measured_leaves_no_decision(tmp_path):
    """`power <= 1` on the screen stage, which never reports power: the ranking lets that limit wait
    (D878) and picks a design to build on; the decision needs the number, so there is none."""
    db = str(tmp_path / "d.db")
    out, _p, _d = _pass(db, 3, [{"metric": "power", "direction": "minimize", "goal": 1, "stage": "screen"},
                                {"metric": "size", "direction": "minimize"}])
    assert out.decision is None and out.closest.name == "d3" and out.unmet == ["power not measured (screen)"]


def test_the_cadence_reads_the_standing_design_when_nothing_is_decided():
    rows = [{"decision": None, "standing_key": "k1"}, {"decision": None, "standing_key": "k1"},
            {"decision": None, "standing_key": "k1"}, {"decision": "old", "decision_key": "k0"}]
    state = SimpleNamespace(records=SimpleNamespace(conclusions=lambda limit=50: rows))
    assert stood(state) == 2, "three passes kept k1 standing, with no decision"
    state = SimpleNamespace(records=SimpleNamespace(conclusions=lambda limit=50: [{"decision": "x", "decision_key": "k"}] * 2))
    assert stood(state) == 1, "a conclusion from before D900 names its decision"


def _sweep_doc(home, goal):
    home.mkdir()
    (home / "gen.py").write_text("import sys; open(sys.argv[1], 'w').write('x=' + sys.argv[2])")
    (home / "bench.py").write_text("import sys; print('speed=' + open(sys.argv[1]).read().split('=')[1])")
    doc = {"statement": "the fastest", "language": "text",
           "flow": {"orchestrate": {"policy": "sweep", "space": {"x": [2, 4, 8]}},
                    "generate": {"command": "{python} {home}/gen.py {artifact} {x}"},
                    "test": {"test": ["true"]},
                    "measure": {"bench": {"command": "{python} {home}/bench.py {artifact}", "metrics": ["speed"]}}},
           "objectives": [{"metric": "speed", "direction": "maximize", "goal": goal}], "budget": {"steps": 3, "batch": 99}}
    (home / "problem.yaml").write_text(json.dumps(doc))
    return home


def test_the_cli_tells_a_qualifying_answer_from_a_correct_design_that_does_not_qualify(tmp_path, monkeypatch, capsys):
    """The review's ranking fixture: speeds 2, 4 and 8 against `speed >= 10` decided 8 and exited 0. Now
    exit 3, no artifact, the closest in the JSON answer; `speed >= 5` exits 0 with the decision."""
    from flux_cli.main import main

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    short = _sweep_doc(tmp_path / "short", 10)
    assert main(["task", "run", str(short), "--passes", "1", "--json", str(tmp_path / "a.json")]) == 3
    said = capsys.readouterr().out
    assert "NO FEASIBLE DESIGN YET" in said and "CLOSEST x=8" in said and "speed 8 is below the limit 10" in said
    ans = json.loads((tmp_path / "a.json").read_text())
    assert ans["decision"] is None and ans["feasible"] is False and ans["artifact"] is None
    assert ans["closest"]["name"] == "x=8" and ans["closest"]["unmet"] == ["speed 8 is below the limit 10 (bench)"]
    met = _sweep_doc(tmp_path / "met", 5)
    assert main(["task", "run", str(met), "--passes", "1", "--json", str(tmp_path / "b.json")]) == 0
    ans = json.loads((tmp_path / "b.json").read_text())
    assert ans["feasible"] is True and ans["decision"]["name"] == "x=8" and ans["closest"] is None
