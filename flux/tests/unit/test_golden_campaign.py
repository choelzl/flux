"""The golden campaign (D531): a two-part document with a tiny check and measurer beside it,
scripted replies; pins which design stands, why, and that a second pass rests. No tool on PATH needed."""

from __future__ import annotations

from flux_llm import ScriptedProposer
from flux_loop import LoopRequest, PromptProblem, TaskSpec, Verdict, run_loop


def _home(tmp_path):
    """Two parts, `front` and `back`, each a short text; a design passes when it says its part's
    name; the whole is the two joined; its "fmax" is its length, its "area" its vowels -- the
    gate and the stage as scripts beside the document (D803)."""
    (tmp_path / "check.py").write_text(
        "import sys\nprint(int(sys.argv[2] not in open(sys.argv[1]).read()), 'failing')\n")
    (tmp_path / "measure.py").write_text(
        "import sys\nt = open(sys.argv[1]).read()\n"
        "print(f'fmax_mhz={len(t) * 10} area_um2={sum(t.count(v) for v in \"aeiou\")}')\n")
    return tmp_path


_FLOW = {"orchestrate": "rules", "test": "{python} {home}/check.py {artifact} {part}",
         "measure": {"screen": {"command": "{python} {home}/measure.py {artifact}", "metrics": ["fmax_mhz", "area_um2"]}}}


def test_the_golden_campaign_stands_on_the_numbers_and_rests(tmp_path):
    doc = {"id": "golden",
           "statement": "two texts that name their part",
           "parts": ["front", "back"],
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize", "goal": 500, "unit": "MHz"}, {"metric": "area_um2", "direction": "minimize"}],
           "budget": {"steps": 6, "repair_attempts": 2, "critique_rounds": 0, "prototype": False},
           "flow": _FLOW}   # the parts in the document's order, no plan turn
    db = str(tmp_path / "golden.db")
    said: list[str] = []
    prob = PromptProblem(TaskSpec.from_dict(doc, base=_home(tmp_path)))
    replies = ['{"artifact": "the front text is here, long enough to clear the goal", "why": "-"}',
               '{"artifact": "back", "why": "-"}']
    req = LoopRequest(db=db, steps=6, repair_attempts=2, critique_rounds=0, prototype=False)
    out = run_loop(prob, req, proposer=ScriptedProposer(replies), log=said.append)
    # both parts proven and admitted; the whole measured on the screen; the decision by the vector
    assert sorted(out.admitted) == ["back", "front"]
    assert out.decision is not None and out.decision.stage == "screen"
    whole = out.decision.metrics
    assert whole["fmax_mhz"] == 10.0 * len(out.decision.candidate.artifact) and whole["fmax_mhz"] >= 500
    assert out.decided_by == "the least area_um2 at fmax_mhz >= 500"
    assert any("decision" in m and "fmax_mhz=" in m for m in out.lessons)
    # the record holds it under the document's name (D524)
    from flux_records import Records

    rec = Records(db, objective={"study": "golden"}, name="golden")
    assert rec.resumed and rec.campaign_id == "golden"
    rec.close("paused")
    # a second pass changes nothing and says so (D518): the parts reload frozen, nothing is due
    said2: list[str] = []
    prob2 = PromptProblem(TaskSpec.from_dict(doc, base=_home(tmp_path)))
    out2 = run_loop(prob2, req, proposer=ScriptedProposer([]), log=said2.append)
    assert out2.at_rest and out2.stopped.startswith("at rest: nothing was due on any part")
    assert sorted(out2.admitted) == ["back", "front"] and out2.decision.candidate.artifact == out.decision.candidate.artifact
    assert any("kept frozen" in m or "reload" in m for m in said2)
    from flux_loop import task_report_lines

    lines = task_report_lines(prob2.task, out2, prob2)
    assert lines[0].startswith("TASK golden") and any(ln.startswith("  DECISION") for ln in lines)


def test_a_campaign_at_rest_explores_and_keeps_the_goal(tmp_path):
    """After a rest, the next pass sends each admitted design back with its numbers and what
    better means from here; a better design passes the same gate and objectives (D593)."""
    doc = {"id": "golden",
           "statement": "two texts that name their part",
           "parts": ["front", "back"],
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize", "goal": 500, "unit": "MHz"}, {"metric": "area_um2", "direction": "minimize"}],
           "budget": {"steps": 6, "repair_attempts": 2, "critique_rounds": 0, "prototype": False},
           "flow": _FLOW}
    db = str(tmp_path / "golden.db")
    req = LoopRequest(db=db, steps=6, repair_attempts=2, critique_rounds=0, prototype=False)
    first = ['{"artifact": "the front text is here, long enough to clear the goal", "why": "-"}',
             '{"artifact": "back", "why": "-"}']
    out = run_loop(PromptProblem(TaskSpec.from_dict(doc, base=_home(tmp_path))), req, proposer=ScriptedProposer(first), log=lambda _m: None)
    rest = run_loop(PromptProblem(TaskSpec.from_dict(doc, base=_home(tmp_path))), req, proposer=ScriptedProposer([]), log=lambda _m: None)
    assert rest.at_rest and rest.explorable, "a model drafts for this campaign: at rest is where exploring starts"
    import dataclasses

    said: list[str] = []
    model = ScriptedProposer(['{"artifact": "front back ' + "x" * 45 + '", "why": "fewer vowels, as long"}'])
    explored = run_loop(PromptProblem(TaskSpec.from_dict(doc, base=_home(tmp_path))), dataclasses.replace(req, explore=1),
                        proposer=model, log=said.append)
    assert any("exploring: 2 design(s) go back" in m for m in said)
    asked = "\n".join(model.prompts)
    assert "The campaign is at rest" in asked and "It meets the goal (fmax_mhz at least 500 (screen))" in asked and "area_um2" in asked
    assert not explored.at_rest
    before, after = out.decision.metrics, explored.decision.metrics
    assert after["fmax_mhz"] >= 500 and after["area_um2"] < before["area_um2"]


def test_between_passes_stops_only_when_told(monkeypatch):
    """A cap, a stop, or a spent script ends a run; a rest never does, and with nothing to draft
    the run waits for a note or a stop (D593)."""
    from types import SimpleNamespace as NS

    from flux_feedback import scripted_channel
    from flux_loop import ops
    from flux_loop.passes import between_passes

    quiet = lambda _m: None  # noqa: E731
    rested = NS(at_rest=True, explorable=True)
    assert between_passes(rested, 1, passes=0, say=quiet)[:2] == (True, 1)
    assert between_passes(rested, 2, passes=0, rests=1, say=quiet)[:2] == (True, 2)
    assert between_passes(NS(at_rest=False, explorable=True), 3, rests=2, say=quiet)[:2] == (True, 0)
    assert between_passes(rested, 3, passes=3, say=quiet)[0] is False
    assert between_passes(rested, 1, proposer=(None, ScriptedProposer(["x"])), say=quiet)[0] is True
    spent = ScriptedProposer(["x"])
    spent.propose("p")
    assert between_passes(rested, 1, proposer=(None, spent), say=quiet)[0] is False
    # nothing drafts: wait -- a note wakes it and reaches the next pass, a stop ends it
    stuck = NS(at_rest=True, explorable=False)
    go, rests, fb = between_passes(stuck, 1, feedback=scripted_channel("try a Booth recoding"), say=quiet, sleep=quiet)
    assert go and rests == 0 and [n.text for n in fb.drain()] == ["try a Booth recoding"]
    asked: dict[str, str] = {}
    monkeypatch.setattr(ops, "stop_requested", lambda *_a: asked.get("why"))
    monkeypatch.setattr(ops, "clear_stop", lambda *_a: asked.clear())
    ticks = {"n": 0}

    def tick(_s):                                  # the operator types `flux stop` on the 2nd tick
        ticks["n"] += 1
        if ticks["n"] == 2:
            asked["why"] = "flux stop"

    assert between_passes(stuck, 1, feedback=scripted_channel(), say=quiet, sleep=tick)[0] is False
    assert ticks["n"] == 2 and not asked
    # a stop asked for while passes run ends the run at the boundary
    asked["why"] = "enough"
    assert between_passes(NS(at_rest=False, explorable=True), 1, say=quiet)[0] is False

