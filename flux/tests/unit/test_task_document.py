"""The standard task document and the problem that runs one (D430): a task enters as a
document, `PromptProblem` needs no code of its own, and `flux task run` is the CLI. Every
test here runs without a model (a scripted proposer) and without external tools (the gate
is a Python one-liner)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from flux_llm import ScriptedProposer
from flux_loop import (LoopRequest, PromptProblem, TaskError, TaskSpec, load_task, request_for,
                       run_loop, task_report_lines)

FLUX_ROOT = Path(__file__).resolve().parents[2]
DIGITS = FLUX_ROOT / "core/loop/examples/digits.task.json"

GOOD = "\n".join(str(i) for i in range(10)) + "\n"
WRONG = GOOD.replace("3", "X")


def _reply(artifact: str) -> str:
    return json.dumps({"artifact": artifact, "why": "as asked"})


def _patch(find: str, replace: str) -> str:
    return json.dumps({"edits": [{"find": find, "replace": replace}], "why": "fix"})


# ---- the document
def test_the_example_task_loads_and_round_trips():
    task = load_task(DIGITS)
    assert task.id == "digits" and task.gate.test and task.gate.count_re == r"(\d+) failing"
    assert task.parts == () and task.stages == () and task.budget["steps"] == 2
    again = TaskSpec.from_dict(task.to_dict())
    assert again == task and again.digest == task.digest


@pytest.mark.parametrize("doc, message", [
    ({}, "`id`"),
    ({"id": "t"}, "`statement`"),
    ({"id": "t", "statement": "x"}, "`gate` needs"),
    ({"id": "t", "statement": "x", "gate": {"test": "not a list"}}, "gate.test must be"),
    ({"id": "t", "statement": "x", "gate": {"test": ["a"], "count_re": "("}}, "not a regex"),
    ({"id": "t", "statement": "x", "gate": {"test": ["a"]}, "parts": ["p", "p"]}, "unique"),
    ({"id": "t", "statement": "x", "gate": {"test": ["a"]}, "stages": [{"name": "r"}]}, "exactly one of"),
    ({"id": "t", "statement": "x", "gate": {"test": ["a"]}, "stages": [{"name": "r", "command": ["m"]}]},
     "needs `metrics_re`"),
    ({"id": "t", "statement": "x", "gate": {"test": ["a"]}, "objectives": [{"metric": "m", "direction": "up"}]},
     "direction must be"),
    ({"id": "t", "statement": "x", "gate": {"test": ["a"]}, "budget": {"turbo": 1}}, "not loop knobs"),
])
def test_the_document_is_validated_with_named_reasons(doc, message):
    with pytest.raises(TaskError, match=message):
        TaskSpec.from_dict(doc)


def test_load_task_reads_yaml_too(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("id: y\nstatement: make it\ngate:\n  build: ['{python}', '-c', 'pass', '{artifact}']\n")
    task = load_task(p)
    assert task.id == "y" and task.gate.build[0] == "{python}"
    with pytest.raises(TaskError, match=".json, .yaml or .yml"):
        load_task(tmp_path / "t.toml")


def test_request_for_layers_the_budget_under_the_caller():
    task = load_task(DIGITS)
    req = request_for(task, db="x.db", steps=5, params={"seed": 3})
    assert isinstance(req, LoopRequest) and req.steps == 5 and req.repair_attempts == 4
    assert req.prototype is False and req.params == {"task": "digits", "seed": 3}


def test_tools_missing_names_the_first_token_of_each_command():
    task = TaskSpec.from_dict({"id": "t", "statement": "x",
                               "gate": {"build": ["no-such-binary-xyz", "{artifact}"],
                                        "test": ["{python}", "-c", "pass"]},
                               "stages": [{"name": "r", "command": ["also-missing-abc"],
                                          "metrics_re": {"m": r"(\d+)"}}]})
    assert PromptProblem(task).tools_missing() == ["no-such-binary-xyz", "also-missing-abc"]
    assert PromptProblem(load_task(DIGITS)).tools_missing() == []


# ---- the problem, end to end, without a model
def test_a_task_runs_from_the_document_alone(tmp_path):
    task = load_task(DIGITS)
    problem = PromptProblem(task)
    proposer = ScriptedProposer([_reply(WRONG), _patch("X", "3")])
    log: list[str] = []
    out = run_loop(problem, request_for(task, db=str(tmp_path / "d.db")), proposer=proposer,
                   log=log.append)
    assert out.decision is not None and out.decision.candidate.artifact == GOOD
    assert out.decision.stage == "gate" and out.decision.metrics == {"failures": 0.0}
    assert list(out.admitted) == ["*"] and not out.refused
    # the prompts carried the document, not code: statement, contract, reply shape, then the
    # failure text with the counted failures
    assert "TASK digits:" in proposer.prompts[0] and "CONTRACT:" in proposer.prompts[0]
    assert "REPLY SHAPE" in proposer.prompts[0] and "artifact" in proposer.prompts[0]
    assert "FAIL line 4: expected 3" in proposer.prompts[1] and "1 failing" in proposer.prompts[1]
    assert any("passes the fast check" in ln for ln in log)
    lines = task_report_lines(task, out)
    assert lines[0].startswith("TASK digits:") and "DECISION" in lines[1]
    # the record is the loop's: a second run resumes it
    from flux_records import Records
    assert Records(str(tmp_path / "d.db"), objective=problem.objective(request_for(task))).resumed


def test_parts_are_generated_one_at_a_time_and_composed_in_order(tmp_path):
    script = ("import sys\nwant = {'head': ['0','1','2','3','4'], 'tail': ['5','6','7','8','9']}[sys.argv[2]]\n"
              "got = [g for g in open(sys.argv[1]).read().split('\\n') if g != '']\n"
              "bad = [i for i, (g, w) in enumerate(zip(got, want)) if g != w] + list(range(min(len(got), 5), 5))\n"
              "print(f'{len(bad)} failing')\nsys.exit(1 if bad else 0)")
    task = TaskSpec.from_dict({
        "id": "digits-in-parts", "statement": "the digits, in two halves",
        "parts": [{"name": "head", "statement": "0 to 4"}, {"name": "tail", "statement": "5 to 9"}],
        "gate": {"test": ["{python}", "-c", script, "{artifact}", "{part}"], "count_re": r"(\d+) failing"},
        "joiner": "\n", "budget": {"steps": 4, "repair_attempts": 2, "prototype": False},
    })
    problem = PromptProblem(task)
    assert problem.subgoals() == ["head", "tail"]
    # the default planner asks the model which part is next; a scripted planner answer first
    proposer = ScriptedProposer([json.dumps({"next": "head"}), _reply("0\n1\n2\n3\n4\n"),
                                 _reply("5\n6\n7\n8\n9\n")])
    out = run_loop(problem, request_for(task, db=""), proposer=proposer, log=lambda m: None)
    assert sorted(out.admitted) == ["head", "tail"]
    assert out.decision is not None and out.decision.candidate.artifact == "0\n1\n2\n3\n4\n\n5\n6\n7\n8\n9\n"
    assert out.decision.candidate.knobs["parts"] == [out.admitted["head"].name, out.admitted["tail"].name]
    assert any("PART head" in p for p in proposer.prompts) and any("PART tail" in p for p in proposer.prompts)


def test_a_build_command_refuses_and_a_stage_command_measures(tmp_path):
    task = TaskSpec.from_dict({
        "id": "lengths", "statement": "a line of text",
        "gate": {"build": ["{python}", "-c",
                           "import sys; t=open(sys.argv[1]).read(); print('need two words') if len(t.split()) < 2 else None; sys.exit(0 if len(t.split()) >= 2 else 3)",
                           "{artifact}"]},
        "stages": [{"name": "screen", "command": ["{python}", "-c",
                                                 "import sys; t=open(sys.argv[1]).read(); print(f'chars={len(t)} words={len(t.split())}')",
                                                 "{artifact}"],
                   "metrics_re": {"chars": r"chars=(\d+)", "words": r"words=(\d+)"}}],
        "objectives": [{"metric": "words", "direction": "maximize"}, {"metric": "chars", "direction": "minimize"}],
        "budget": {"steps": 1, "repair_attempts": 3, "prototype": False},
    })
    problem = PromptProblem(task)
    assert problem.frontier_axes() is not None and problem.stages() == ["screen"]
    # "one" is refused by the build; the patch turn gets a whole artifact instead of edits,
    # so the loop rewrites, and the rewrite (the last scripted reply repeats) builds
    proposer = ScriptedProposer([_reply("one"), _reply("one two three")])
    out = run_loop(problem, request_for(task, db=""), proposer=proposer, log=lambda m: None)
    assert out.decision is not None
    assert out.decision.metrics == {"chars": 13.0, "words": 3.0} and out.decision.stage == "screen"
    assert "was refused" in proposer.prompts[1] and "need two words" in proposer.prompts[1]


def test_parse_design_accepts_json_or_a_fenced_block():
    problem = PromptProblem(load_task(DIGITS))
    cand, why = problem.parse_design(_reply("0\n"), None)
    assert cand is not None and cand.artifact == "0\n" and cand.name == "digits#1" and not why
    cand, _ = problem.parse_design("Here you go:\n```text\n0\n1\n```\nDone.", None)
    assert cand is not None and cand.artifact == "0\n1"
    assert problem.parse_design('{"why": "no artifact"}', None) == (None, "the reply carried no artifact")


# ---- the CLI
def test_flux_task_check_lists_the_document(capsys):
    from flux_cli.main import main

    assert main(["task", "check", str(DIGITS)]) == 0
    out = capsys.readouterr().out
    assert "task digits:" in out and "gate.test:" in out and "tools: all present" in out
    assert "stages: gate" in out and "objectives: none" in out


def test_flux_task_run_without_a_model(tmp_path, capsys):
    from flux_cli.main import main

    replies = tmp_path / "replies.json"
    replies.write_text(json.dumps([_reply(WRONG), _patch("X", "3")]))
    target = tmp_path / "digits.txt"
    code = main(["task", "run", str(DIGITS), "--db", str(tmp_path / "t.db"), "--replies", str(replies),
                 "--out", str(target)])
    out = capsys.readouterr().out
    assert code == 0 and target.read_text() == GOOD
    assert "TASK digits:" in out and "DECISION" in out and "artifact written to" in out


def test_flux_task_check_rejects_a_bad_document(tmp_path, capsys):
    from flux_cli.main import main

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"id": "b", "statement": "x"}))
    assert main(["task", "check", str(bad)]) == 2
    assert "not a task: `gate` needs" in capsys.readouterr().out


# ---- propose: decompose (D431)
def _decomposed_task(**extra):
    script = ("import sys\nwant = {'head': ['0','1','2','3','4'], 'tail': ['5','6','7','8','9']}[sys.argv[2]]\n"
              "got = [g for g in open(sys.argv[1]).read().split('\\n') if g != '']\n"
              "bad = [i for i, (g, w) in enumerate(zip(got, want)) if g != w] + list(range(min(len(got), 5), 5))\n"
              "print(f'{len(bad)} failing')\nsys.exit(1 if bad else 0)")
    return TaskSpec.from_dict({
        "id": "digits-decomposed", "statement": "the ten digits, one per line",
        "parts": "decompose", "max_parts": 3,
        "gate": {"test": ["{python}", "-c", script, "{artifact}", "{part}"], "count_re": r"(\d+) failing"},
        "joiner": "\n", "budget": {"steps": 4, "repair_attempts": 2, "prototype": False},
        **extra})


def test_a_task_may_ask_the_orchestrator_to_decompose_it(tmp_path):
    task = _decomposed_task()
    assert task.decompose and task.parts == () and TaskSpec.from_dict(task.to_dict()) == task
    decomposition = json.dumps({"parts": [{"name": "head", "statement": "digits 0 to 4"},
                                          {"name": "tail", "statement": "digits 5 to 9"}], "why": "two halves"})
    proposer = ScriptedProposer([decomposition, json.dumps({"next": "head"}),
                                 _reply("0\n1\n2\n3\n4\n"), _reply("5\n6\n7\n8\n9\n")])
    log: list[str] = []
    problem = PromptProblem(task)
    out = run_loop(problem, request_for(task, db=str(tmp_path / "d.db")), proposer=proposer, log=log.append)
    assert [p.name for p in problem.parts] == ["head", "tail"]
    assert "Divide this task into 1 to 3 parts" in proposer.prompts[0]
    assert sorted(out.admitted) == ["head", "tail"] and out.decision is not None
    assert out.decision.candidate.artifact == "0\n1\n2\n3\n4\n\n5\n6\n7\n8\n9\n"
    assert any("decompose: 2 part(s): head, tail" in ln for ln in log)
    # a resume reuses the recorded division even if the model would now answer differently
    other = ScriptedProposer([json.dumps({"parts": [{"name": "all", "statement": "everything"}]})])
    again = PromptProblem(task)
    out2 = run_loop(again, request_for(task, db=str(tmp_path / "d.db")), proposer=other, log=log.append)
    assert [p.name for p in again.parts] == ["head", "tail"] and other.prompts == []
    assert sorted(out2.admitted) == ["head", "tail"]           # re-verified from the record
    assert any("resumed from the record" in ln for ln in log)


def test_decompose_refuses_without_a_model_and_checks_the_division(tmp_path):
    task = _decomposed_task()
    with pytest.raises(RuntimeError, match="asks to be decomposed but no model"):
        run_loop(PromptProblem(task), request_for(task, db=""), proposer=None, log=lambda m: None)
    for reply, why in [(json.dumps({"parts": []}), "no parts"),
                       (json.dumps({"parts": [{"name": "a b", "statement": "x"}]}), "not an identifier"),
                       (json.dumps({"parts": [{"name": "a", "statement": "x"}, {"name": "a", "statement": "y"}]}), "repeats"),
                       (json.dumps({"parts": [{"name": n, "statement": "x"} for n in "abcd"]}), "at most 3")]:
        with pytest.raises(RuntimeError, match=why):
            run_loop(PromptProblem(task), request_for(task, db=""), proposer=ScriptedProposer([reply]),
                     log=lambda m: None)
    with pytest.raises(TaskError, match="either a list or"):
        TaskSpec.from_dict({**task.to_dict(), "parts": [{"name": "p"}], "decompose": True})


def test_records_remember_and_recall_typed_decisions(tmp_path):
    from flux_records import Records

    r = Records(str(tmp_path / "r.db"), objective={"s": 1})
    r.remember("decomposition", {"parts": [{"name": "a"}]})
    r.remember("decomposition", {"parts": [{"name": "b"}]})
    r.remember("plan", {"next": "a"})
    assert [d["parts"][0]["name"] for d in r.recall("decomposition")] == ["a", "b"]
    assert r.recall("plan")[0]["next"] == "a" and r.recall("nothing") == []
    assert Records(str(tmp_path / "nodir" / "x.db"), objective={"s": 1}).recall("plan") == []


# ---- propose: brief (D432)
def test_the_orchestrator_briefs_a_part_and_sets_its_budget(tmp_path):
    task = TaskSpec.from_dict({**load_task(DIGITS).to_dict(), "brief": "propose",
                               "budget": {"steps": 1, "repair_attempts": 2, "prototype": False}})
    assert task.brief and TaskSpec.from_dict(task.to_dict()) == task
    brief = json.dumps({"brief": "Ten lines, digits 0-9 ascending, newline-terminated, nothing else.",
                        "repair_attempts": 1, "why": "trivial"})
    # one design, then patches that never fix it: the budget the brief set (1) is what the
    # inner loop spends, not the request's 2
    proposer = ScriptedProposer([brief, _reply(WRONG), _patch("X", "Y"), _patch("Y", "Z")])
    log: list[str] = []
    problem = PromptProblem(task)
    out = run_loop(problem, request_for(task, db=str(tmp_path / "b.db")), proposer=proposer, log=log.append)
    assert "You are briefing the writer" in proposer.prompts[0]
    assert "BRIEF (from the orchestrator):\nTen lines" in proposer.prompts[1]      # in the static prefix
    assert any("brief for digits: 1 line(s), 1 repair attempts" in ln for ln in log)
    assert out.decision is None and len(proposer.prompts) == 3         # brief, design, ONE repair
    # a resume reuses the brief without asking
    other = ScriptedProposer([_reply(GOOD)])
    out2 = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "b.db")), proposer=other,
                    log=log.append)
    assert out2.decision is not None and "BRIEF (from the orchestrator)" in other.prompts[0]
    assert any("resumed from the record" in ln for ln in log)


def test_a_brief_is_help_not_a_gate():
    task = TaskSpec.from_dict({**load_task(DIGITS).to_dict(), "brief": "propose"})
    # no model: no brief, and the task still runs when handed a scripted writer later
    problem = PromptProblem(task)
    from flux_loop import LoopState
    state = LoopState(request=request_for(task, db=""), say=lambda m: None, proposer=None, feedback=None)
    assert problem.plan_part(None, state) == {}
    # a reply without a brief: the statement stays the brief, nothing is remembered
    state = LoopState(request=request_for(task, db=""), say=lambda m: None,
                      proposer=ScriptedProposer(['{"why": "no brief"}']), feedback=None)
    assert problem.plan_part(None, state) == {}
    # an oversized budget is clamped to twice the request's
    state = LoopState(request=request_for(task, db="", repair_attempts=3), say=lambda m: None,
                      proposer=ScriptedProposer([json.dumps({"brief": "b", "repair_attempts": 99})]), feedback=None)
    assert problem.plan_part(None, state) == {"brief": "b", "repair_attempts": 6}


# ---- critique (D433)
def _critic(ok: bool, *issues: str) -> str:
    return json.dumps({"ok": ok, "issues": list(issues), "why": "critic"})


def test_a_critic_sends_a_passing_candidate_back_once_then_the_gate_rules(tmp_path):
    task = TaskSpec.from_dict({**load_task(DIGITS).to_dict(), "critique": "propose",
                               "budget": {"steps": 3, "repair_attempts": 2, "prototype": False}})
    assert task.critique and TaskSpec.from_dict(task.to_dict()) == task
    # design passes the gate; the critic objects; the patch turn carries the objection; the
    # refined design passes again and the critic accepts; then the decision is critiqued
    # (after one send-back the gate rules: the refined candidate is admitted without a
    # second critique, so the next critic reply is the decision's)
    proposer = ScriptedProposer([_reply(GOOD), _critic(False, "trailing newline is not 'nothing else'"),
                                 _patch("9\n", "9"), _critic(False, "the decision ignores width")])
    log: list[str] = []
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "c.db")), proposer=proposer,
                   log=log.append)
    assert "You are the critic" in proposer.prompts[1] and "ALREADY PASSED" in proposer.prompts[1]
    assert "CRITIQUE (the gate passed; refine, do not restart): trailing newline" in proposer.prompts[2]
    assert out.decision is not None and out.decision.candidate.artifact == GOOD.rstrip("\n")
    assert any("[critique] digits: digits#1 sent back" in ln for ln in out.lessons)
    assert any("the critic objects to the decision: the decision ignores width" in ln for ln in out.not_established)
    assert any("critique of digits#1" in ln for ln in log)
    # a critic that objects forever cannot veto: after critique_rounds the gate rules (the
    # writer answers the send-back with the same text; the last reply repeats)
    proposer = ScriptedProposer([_reply(GOOD), _critic(False, "never good enough"), _reply(GOOD)])
    out = run_loop(PromptProblem(task), request_for(task, db="", critique_rounds=1), proposer=proposer,
                   log=lambda m: None)
    assert out.decision is not None and len(out.admitted) == 1
    assert sum("sent back" in ln for ln in out.lessons) == 1


def test_a_critic_sends_a_division_back_and_only_the_accepted_one_is_remembered(tmp_path):
    task = _decomposed_task(critique="propose")
    first = json.dumps({"parts": [{"name": "all", "statement": "everything"}], "why": "one"})
    second = json.dumps({"parts": [{"name": "head", "statement": "0-4"}, {"name": "tail", "statement": "5-9"}]})
    # one critique round: the re-division stands without a second critique; then the
    # planner's choice, each part's candidate and its critique, and the decision's critique
    proposer = ScriptedProposer([first, _critic(False, "one part cannot be checked on its own"), second,
                                 json.dumps({"next": "head"}),
                                 _reply("0\n1\n2\n3\n4\n"), _critic(True), _reply("5\n6\n7\n8\n9\n"),
                                 _critic(True), _critic(True)])
    problem = PromptProblem(task)
    out = run_loop(problem, request_for(task, db=str(tmp_path / "dc.db")), proposer=proposer, log=lambda m: None)
    assert "A critic objected: one part cannot be checked" in proposer.prompts[2]
    assert [p.name for p in problem.parts] == ["head", "tail"] and out.decision is not None
    from flux_records import Records
    r = Records(str(tmp_path / "dc.db"), objective=problem.objective(request_for(task)))
    assert [[p["name"] for p in d["parts"]] for d in r.recall("decomposition")] == [["head", "tail"]]
    kinds = [(c["kind"], c["ok"]) for c in r.recall("critique")]
    assert kinds == [("decomposition", False), ("candidate", True), ("candidate", True), ("decision", True)]


def test_without_a_critic_nothing_changes(tmp_path):
    task = load_task(DIGITS)
    proposer = ScriptedProposer([_reply(GOOD)])
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=proposer, log=lambda m: None)
    assert out.decision is not None and len(proposer.prompts) == 1
    assert not any("critique" in ln for ln in out.lessons + out.not_established)
    task = TaskSpec.from_dict({**task.to_dict(), "critique": "propose"})
    out = run_loop(PromptProblem(task), request_for(task, db="", critique_rounds=0),
                   proposer=ScriptedProposer([_reply(GOOD)]), log=lambda m: None)
    assert out.decision is not None                            # critique_rounds=0: no critic asked


# ---- D519: a document names a WORLD ------------------------------------------------------
class _Tiny:
    """A world of two hooks and one state: what a package provides so a document can run."""

    def __init__(self, problem):
        self.problem = problem
        self.built: list[str] = []
        self.ulp = int(problem.task.params.get("ulp_budget", 0))

    def build(self, cand, subgoal, state):
        self.built.append(cand.name)
        return cand.artifact

    def judge(self, built, cand, subgoal, state):
        from flux_loop import Verdict

        return Verdict(built == "ok", 0.0 if built == "ok" else 1.0, "" if built == "ok" else "not ok")

    def measure(self, cand, stage, state):
        return {"fmax_mhz": 900.0, "area_um2": 1.0}

    def standing(self, state):
        return {"goal": f"within {self.ulp} ULP", "now": "-", "parts": {}}

    def report(self, out):
        return ["  the tiny world's line"]


def _tiny_judge(problem, built, cand, subgoal, state):
    from flux_loop import Verdict

    return Verdict(True, 0.0, "the hook's own judge")


def test_a_document_names_a_world_and_binds_its_hooks(tmp_path):
    """D519 (Cedric: "a simple way to add other tests/problems/dse's into the system without
    having to write a whole application/demo in python"): a document with a `world:` runs
    that package's hooks; what it does not fill stays the document problem's; the world's
    own state is reachable through the problem; `hooks:` replaces one; `campaign:` is the
    record's identity; `ladder:`, `needs:` and `stage: deepest` are read."""
    from flux_loop import Ladder, PromptProblem, TaskError, TaskSpec, task_report_lines
    from flux_records import Records

    doc = {"id": "tiny", "statement": "a tiny thing", "parts": ["a"], "world": __name__ + ":_Tiny",
           "params": {"ulp_budget": 2}, "campaign": {"study": "tiny", "ulp": 2},
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize", "goal": 800, "stage": "deepest", "unit": "MHz"},
                          {"metric": "area_um2", "direction": "minimize"}],
           "ladder": {"sweep": [2, 4], "redesigns": 1},
           "stages": [{"name": "screen", "metrics": ["fmax_mhz", "area_um2"]},
                      {"name": "confirm", "metrics": ["fmax_mhz"], "needs": ["no-such-tool-xyz"]}]}
    task = TaskSpec.from_dict(doc)
    prob = PromptProblem(task)
    assert isinstance(prob.world, _Tiny) and prob.ulp == 2                    # the world's state, through the problem
    assert prob.build.__self__ is prob.world and prob.judge.__self__ is prob.world
    assert prob.validate(None) == []                                           # world stages declare their metrics
    assert prob.stages() == ["screen"], "confirm needs a tool that is not on PATH"
    assert prob.objectives()[0].stage == "screen" and prob.objectives()[0].unit == "MHz"
    assert prob.ladder() == Ladder(sweep=(2, 4), redesigns=1)
    req = LoopRequest(db=str(tmp_path / "t.db"))
    assert prob.objective(req) == {"study": "tiny", "ulp": 2}
    assert Records(req.db, objective=prob.objective(req)).campaign_id == Records(req.db, objective={"study": "tiny", "ulp": 2}).campaign_id
    assert prob.standing(None)["goal"] == "within 2 ULP"
    from flux_loop import Candidate, Scored
    from types import SimpleNamespace

    out = SimpleNamespace(decision=Scored(Candidate("a#1", "ok", subgoal="a"), stage="screen", metrics={"fmax_mhz": 900.0}),
                          decided_by="the most fmax_mhz", confirmed=[], frontier=[], admitted={}, lessons=[],
                          not_established=[], refused=[], notes=[])
    assert "  the tiny world's line" in task_report_lines(task, out, prob)
    # a hook of the document's own replaces the world's, and takes the problem first
    task2 = TaskSpec.from_dict({**doc, "hooks": {"judge": __name__ + ":_tiny_judge"}})
    prob2 = PromptProblem(task2)
    assert prob2.judge("anything", None, "a", None).why == "the hook's own judge"
    # what a document cannot say
    with pytest.raises(TaskError, match="hooks"):
        TaskSpec.from_dict({**doc, "hooks": {"objectives": "x:y"}})
    with pytest.raises(TaskError, match="ladder keys"):
        TaskSpec.from_dict({**doc, "ladder": {"steps_per_day": 3}})
    with pytest.raises(TaskError, match="world"):
        TaskSpec.from_dict({**doc, "world": "flux_loop.task"})
    with pytest.raises(TaskError, match="exactly one of"):
        TaskSpec.from_dict({**{k: v for k, v in doc.items() if k != "world"}, "gate": {"test": ["true"]}})


def test_a_world_document_runs_the_loop_end_to_end(tmp_path):
    """The tiny world through `run_loop`: a scripted reply is built and judged by the world,
    the document's screen stage is the world's measurement, and the decision is reported
    with the world's line."""
    from flux_llm import ScriptedProposer
    from flux_loop import LoopRequest, PromptProblem, TaskSpec, run_loop, task_report_lines

    doc = {"id": "tiny", "statement": "a tiny thing", "parts": ["a"], "world": __name__ + ":_Tiny",
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize"}],
           "stages": [{"name": "screen", "metrics": ["fmax_mhz", "area_um2"]}]}
    prob = PromptProblem(TaskSpec.from_dict(doc))
    out = run_loop(prob, LoopRequest(db=str(tmp_path / "tiny.db"), steps=2, prototype=False, critique_rounds=0),
                   proposer=ScriptedProposer(['{"artifact": "ok", "why": "-"}']), log=lambda _m: None)
    assert prob.world.built and out.admitted["a"].artifact == "ok"
    assert out.decision is not None and out.decision.metrics["fmax_mhz"] == 900.0
    assert "  the tiny world's line" in task_report_lines(prob.task, out, prob)


def test_two_documents_with_one_id_do_not_share_a_campaign(tmp_path):
    """D539: the identity document ends with the document's DIGEST. `request_for` puts the
    task id into the params, and it used to overwrite the digest, so two different
    documents with one id opened one campaign."""
    a = TaskSpec.from_dict({"id": "t", "statement": "count to ten", "gate": {"test": ["a"]}})
    b = TaskSpec.from_dict({"id": "t", "statement": "count to twenty", "gate": {"test": ["a"]}})
    oa = PromptProblem(a).objective(request_for(a, db="x.db"))
    ob = PromptProblem(b).objective(request_for(b, db="x.db"))
    assert oa["task"] == a.digest and ob["task"] == b.digest and oa != ob


def test_the_document_says_whether_it_wants_a_measurement_cache():
    """D541: `cache: false` -- a world that measures in microseconds keeps no sidecar."""
    base = {"id": "t", "statement": "x", "gate": {"test": ["a"]}}
    assert PromptProblem(TaskSpec.from_dict(base)).cache_suffix() == "t.json"
    assert PromptProblem(TaskSpec.from_dict({**base, "cache": False})).cache_suffix() is None
    assert PromptProblem(TaskSpec.from_dict({**base, "cache": "shared.json"})).cache_suffix() == "shared.json"
    task = TaskSpec.from_dict({**base, "cache": False})
    assert TaskSpec.from_dict(task.to_dict()) == task
    with pytest.raises(TaskError, match="`cache`"):
        TaskSpec.from_dict({**base, "cache": 3})


# ---- the flow (D542): one key per box of the drawing
def _flow_doc(flow, **more):
    return {"id": "t", "statement": "x", "gate": {"test": ["a"]},
            "stages": [{"name": "screen", "command": ["m"], "metrics_re": {"fmax_mhz": r"(\d+)"}},
                       {"name": "confirm", "command": ["m"], "metrics_re": {"fmax_mhz": r"(\d+)"}}],
            "flow": flow, **more}


def test_the_flow_block_folds_into_the_rig_and_reads_back():
    from flux_loop.task import describe_flow

    def box(lines, name):
        return next(l for l in lines if l.startswith(name + ":"))

    task = TaskSpec.from_dict(_flow_doc({"orchestrate": "rules", "generate": {"catalog": ["a.txt"]},
                                         "critique": "llm", "analytical": ["screen"], "calibrate": "off",
                                         "knowledge": ["mined"], "feedback": "none", "test": "gate"}))
    assert task.roles == {"orchestrator": "rules", "knowledge": "mined"}
    assert task.generator == {"catalog": ["a.txt"]} and task.critique is True
    assert task.budget["calibrate"] is False and task.flow["analytical"] == ["screen"]
    assert TaskSpec.from_dict(task.to_dict()) == task
    assert PromptProblem(task).analytic_stages() == frozenset({"screen"})
    lines = describe_flow(task)
    assert box(lines, "orchestrate").startswith("orchestrate: rules") and box(lines, "generate").startswith("generate: catalog of 1")
    assert box(lines, "critique").startswith("critique: llm") and box(lines, "analytical") == "analytical: screen"
    assert box(lines, "simulation") == "simulation: confirm" and box(lines, "calibrate") == "calibrate: off" and box(lines, "feedback") == "feedback: none"
    plain = describe_flow(TaskSpec.from_dict(_flow_doc({})))
    assert box(plain, "simulation") == "simulation: screen, confirm" and box(plain, "calibrate").startswith("calibrate: on")


@pytest.mark.parametrize("flow, more, message", [
    ({"orchestrate": "rules"}, {"roles": {"orchestrator": "llm"}}, "said twice"),
    ({"generate": "model"}, {"generator": {"command": ["x"]}}, "said twice"),
    ({"critique": "llm"}, {"critique": "propose"}, "said twice"),
    ({"test": "llm"}, {}, "never delegated"),
    ({"validate": "model"}, {}, "one of rules, llm"),
    ({"dse": "hillclimb"}, {}, "no such DSE policy"),
    ({"analytical": ["nowhere"]}, {}, "does not declare"),
    ({"analytical": ["screen"], "simulation": ["screen"]}, {}, "not both"),
    ({"knowledge": ["sheet"]}, {}, "names none"),
    ({"winner": "llm"}, {}, "not a box"),
])
def test_a_flow_that_says_a_thing_twice_or_wrong_is_refused(flow, more, message):
    with pytest.raises(TaskError, match=message):
        TaskSpec.from_dict(_flow_doc(flow, **more))


def test_a_worlds_child_inherits_it_and_is_its_own_campaign(tmp_path):
    """D555 (review 2, R7): a `subtasks:` child of a `world:` document runs in that world
    unless it names its own, with the parent's hooks, ladder, cache and flow; the caller's
    role overrides reach it; a child of a named campaign is `<parent>/<child>`."""
    from flux_loop import PromptProblem, TaskSpec
    from flux_loop.roles import Roles, Rules

    doc = {"id": "whole", "statement": "the whole", "world": __name__ + ":_Tiny", "params": {"ulp_budget": 3},
           "campaign": {"name": "whole"}, "flow": {"validate": "llm"},
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize"}],
           "stages": [{"name": "screen", "metrics": ["fmax_mhz", "area_um2"]}],
           "subtasks": [{"id": "left", "statement": "the left half", "parts": ["a"]},
                        {"id": "right", "statement": "the right half", "parts": ["b"], "world": "", "campaign": {"name": "own"},
                         "gate": {"test": ["true"]},
                         "stages": [{"name": "screen", "command": ["m"], "metrics_re": {"fmax_mhz": r"(\d+)"}}]}]}
    task = TaskSpec.from_dict(doc)
    left, right = task.subtasks
    assert left.world == task.world and left.campaign == {"name": "whole/left"} and left.flow.get("validate") == "llm"
    assert right.world == "" and right.campaign == {"name": "own"}
    mine = Rules(name="mine")
    parent = PromptProblem(task, roles=Roles(orchestrator=mine))
    kids = {sub.name: sub.problem for sub in parent.subproblems(None)}
    assert isinstance(kids["left"].world, _Tiny) and kids["left"].ulp == 3
    assert kids["left"].roles().orchestrator is mine and kids["right"].roles().orchestrator is mine


def test_validate_llm_lets_the_model_object_before_a_step_is_spent(tmp_path):
    """D556 (review 2, R8): `flow: {validate: llm}` asks the model to read the document and
    object; the objections are said and kept as lessons, and they never stop the run."""
    import json

    from flux_llm import ScriptedProposer
    from flux_loop import LoopRequest, LoopState, PromptProblem, TaskSpec

    task = TaskSpec.from_dict(_flow_doc({"validate": "llm"}))
    prob = PromptProblem(task)
    proposer = ScriptedProposer([json.dumps({"ok": False, "objections": ["fmax_mhz has no goal", "one stage measures nothing new"]})])
    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=proposer, feedback=None)
    assert prob.objections(state) == ["fmax_mhz has no goal", "one stage measures nothing new"]
    assert "THE DOCUMENT:" in proposer.prompts[0] and '"validate": "llm"' in proposer.prompts[0] and "validate: llm" in proposer.prompts[0]
    assert PromptProblem(TaskSpec.from_dict(_flow_doc({}))).objections(state) == []        # rules only: no call
    assert prob.objections(LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)) == []


def test_a_document_learns_its_margins_from_the_calibrations(tmp_path):
    """D562: the calibration between two stages becomes the margin on the shallower one, the
    links multiplied up to the objective's stage, the document's margin the floor; a chain
    with a missing link keeps the document's margin there."""
    from flux_loop import LoopRequest, LoopState, PromptProblem, TaskSpec
    from flux_loop.calibrate import Bias

    doc = {"id": "m", "statement": "m", "gate": {"test": ["true"]},
           "stages": [{"name": "screen", "command": ["m"], "metrics_re": {"fmax_mhz": r"(\d+)"}},
                      {"name": "confirm", "command": ["m"], "metrics_re": {"fmax_mhz": r"(\d+)"}},
                      {"name": "route", "command": ["m"], "metrics_re": {"fmax_mhz": r"(\d+)"}}],
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize", "goal": 800, "stage": "deepest", "margin": 0.03}]}
    prob = PromptProblem(TaskSpec.from_dict(doc))
    said = []
    state = LoopState(request=LoopRequest(), say=said.append, proposer=None, feedback=None)
    stages = ["screen", "confirm", "route"]
    prob.calibrated([Bias("fmax_mhz", "confirm", "route", 0.878, 0.01, 7)], state)
    o = prob.objectives()[0]
    assert abs(o.margin_at("confirm") - (1 / 0.878 - 1)) < 1e-9 and o.margin_at("screen") == 0.03   # screen->confirm unmeasured
    assert abs(o.goal_at("confirm", stages) - 800 / 0.878) < 1e-6
    assert any("the margin on the confirm stage for fmax_mhz is 13.9% from the measured route/confirm ratio 0.878" in m for m in said)
    prob.calibrated([Bias("fmax_mhz", "screen", "confirm", 0.9, 0.0, 5), Bias("fmax_mhz", "confirm", "route", 0.878, 0.01, 7)], state)
    assert abs(prob.objectives()[0].margin_at("screen") - (1 / (0.9 * 0.878) - 1)) < 1e-9
    prob.calibrated([Bias("fmax_mhz", "confirm", "route", 1.05, 0.0, 3)], state)     # routing GAINS: the floor holds
    assert prob.objectives()[0].margin_at("confirm") == 0.03
