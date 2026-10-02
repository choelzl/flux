"""The standard task document and `PromptProblem`, which runs one (D430).

Every test runs without a model (a scripted proposer) and without external tools (the gate is a
Python one-liner)."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from flux_llm import ScriptedProposer
from flux_loop import (LoopRequest, PromptProblem, TaskError, TaskSpec, load_task, request_for,
                       run_loop, task_report_lines)

#: D738: these tests measure what one pass does with a whole search; one design a pass is the default
WHOLE = 10_000

FLUX_ROOT = Path(__file__).resolve().parents[2]
DIGITS = FLUX_ROOT / "core/loop/examples/digits/problem.json"

GOOD = "\n".join(str(i) for i in range(10)) + "\n"
WRONG = GOOD.replace("3", "X")


def _reply(artifact: str) -> str:
    return json.dumps({"artifact": artifact, "why": "as asked"})


def _patch(find: str, replace: str) -> str:
    return json.dumps({"edits": [{"find": find, "replace": replace}], "why": "fix"})


# ---- the document
def test_the_example_task_loads_and_round_trips():
    task = load_task(DIGITS)
    assert task.id == "digits" and task.gate.named("test").count_re == r"(\d+) failing"
    assert task.parts == () and task.stages == () and task.budget["steps"] == 2
    again = TaskSpec.from_dict(task.to_dict())
    assert again == task and again.digest == task.digest


@pytest.mark.parametrize("doc, message", [
    ({}, "`id`"),
    ({"id": "t"}, "`statement`"),
    ({"id": "t", "statement": "x"}, "`flow.test` is a command"),
    ({"id": "t", "statement": "x", "flow": {"test": {"test": 42}}}, "flow.test.test: a command"),
    ({"id": "t", "statement": "x", "flow": {"test": {"test": {"run": ["a"], "count_re": "("}}}}, "not a regex"),
    ({"id": "t", "statement": "x", "parts": ["p", "p"], "flow": {"test": {"test": ["a"]}}}, "unique"),
    ({"id": "t", "statement": "x", "flow": {"test": {"test": ["a"]}, "measure": {"r": None}}}, "exactly one of"),
    ({"id": "t", "statement": "x", "flow": {"test": {"test": ["a"]}, "measure": {"r": ["m"]}}},
     "needs `metrics`"),
    ({"id": "t",
      "statement": "x",
      "objectives": [{"metric": "m", "direction": "up"}],
      "flow": {"test": {"test": ["a"]}}},
     "direction must be"),
    ({"id": "t", "statement": "x", "budget": {"turbo": 1}, "flow": {"test": {"test": ["a"]}}}, "not loop knobs"),
])
def test_the_document_is_validated_with_named_reasons(doc, message):
    with pytest.raises(TaskError, match=message):
        TaskSpec.from_dict(doc)


def test_load_task_reads_yaml_too(tmp_path):
    p = tmp_path / "y" / "t.yaml"
    p.parent.mkdir()
    p.write_text("statement: make it\nflow:\n  test:\n    build: ['{python}', '-c', 'pass', '{artifact}']\n")
    task = load_task(p)
    assert task.id == "y" and task.gate.named("build").run[0] == "{python}"
    with pytest.raises(TaskError, match="a .yaml, .yml or .json file"):
        load_task(tmp_path / "t.toml")


def test_request_for_layers_the_budget_under_the_caller():
    task = load_task(DIGITS)
    req = request_for(task, db="x.db", steps=5, params={"seed": 3})
    assert isinstance(req, LoopRequest) and req.steps == 5 and req.repair_attempts == 4
    assert req.prototype is False and req.params == {"task": "digits", "seed": 3}


def test_tools_missing_names_the_first_token_of_each_command():
    task = TaskSpec.from_dict({"id": "t",
                               "statement": "x",
                               "flow": {"test": {"build": ["no-such-binary-xyz", "{artifact}"],
                                                 "test": ["{python}", "-c", "pass"]},
                                        "measure": {"r": {"command": ["also-missing-abc"],
                                                          "metrics_re": {"m": '(\\d+)'}}}}})
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
    assert problem.open_records(request_for(task, db=str(tmp_path / "d.db")), lambda _m: None).resumed


def test_parts_are_generated_one_at_a_time_and_composed_in_order(tmp_path):
    script = ("import sys\nwant = {'head': ['0','1','2','3','4'], 'tail': ['5','6','7','8','9']}[sys.argv[2]]\n"
              "got = [g for g in open(sys.argv[1]).read().split('\\n') if g != '']\n"
              "bad = [i for i, (g, w) in enumerate(zip(got, want)) if g != w] + list(range(min(len(got), 5), 5))\n"
              "print(f'{len(bad)} failing')\nsys.exit(1 if bad else 0)")
    task = TaskSpec.from_dict({"id": "digits-in-parts",
                               "statement": "the digits, in two halves",
                               "parts": {"head": "0 to 4", "tail": "5 to 9"},
                               "budget": {"steps": 4, "repair_attempts": 2, "prototype": False},
                               "flow": {"test": ["{python}", "-c", script, "{artifact}", "{part}"]}})
    problem = PromptProblem(task)
    assert problem.subgoals() == ["head", "tail"]
    # the default planner asks the model which part is next; a scripted planner answer first
    proposer = ScriptedProposer([json.dumps({"next": "head"}), _reply("0\n1\n2\n3\n4\n"),
                                 _reply("5\n6\n7\n8\n9\n")])
    out = run_loop(problem, request_for(task, db=""), proposer=proposer, log=lambda m: None)
    assert sorted(out.admitted) == ["head", "tail"]
    assert out.decision is not None and out.decision.candidate.artifact == "0\n1\n2\n3\n4\n\n\n5\n6\n7\n8\n9\n"
    assert out.decision.candidate.knobs["parts"] == [out.admitted["head"].name, out.admitted["tail"].name]
    assert any("PART head" in p for p in proposer.prompts) and any("PART tail" in p for p in proposer.prompts)


def test_a_build_command_refuses_and_a_stage_command_measures(tmp_path):
    task = TaskSpec.from_dict({"id": "lengths",
                               "statement": "a line of text",
                               "objectives": [{"metric": "words", "direction": "maximize"}, {"metric": "chars", "direction": "minimize"}],
                               "budget": {"steps": 1, "repair_attempts": 3, "prototype": False},
                               "flow": {"test": {"build": ["{python}", "-c", "import sys; t=open(sys.argv[1]).read(); print('need two words') if len(t.split()) < 2 else None; sys.exit(0 if len(t.split()) >= 2 else 3)", "{artifact}"]},
                                        "measure": {"screen": {"command": ["{python}", "-c", "import sys; t=open(sys.argv[1]).read(); print(f'chars={len(t)} words={len(t.split())}')", "{artifact}"],
                                                               "metrics_re": {"chars": 'chars=(\\d+)',
                                                                              "words": 'words=(\\d+)'}}}}})
    problem = PromptProblem(task)
    assert problem.frontier_axes() is not None and problem.stages() == ["screen"]
    # "one" is refused by the build; the patch turn gets a whole artifact instead of edits, so
    # the loop rewrites, and the rewrite (the last scripted reply repeats) builds
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
    assert "task digits:" in out and "1. test: " in out and "tools: present for every stage" in out
    assert "stages: gate" in out and "objectives: none" in out


def test_flux_task_run_without_a_model(tmp_path, capsys):
    from flux_cli.main import main

    replies = tmp_path / "replies.json"
    replies.write_text(json.dumps([_reply(WRONG), _patch("X", "3")]))
    target = tmp_path / "digits.txt"
    answer = tmp_path / "answer.json"
    code = main(["task", "run", str(DIGITS), "--db", str(tmp_path / "t.db"), "--replies", str(replies),
                 "--out", str(target), "--json", str(answer)])
    out = capsys.readouterr().out
    assert code == 0 and target.read_text() == GOOD
    assert "TASK digits:" in out and "DECISION" in out and "artifact written to" in out
    got = json.loads(answer.read_text())          # --json: the answer, for a script
    assert got["task"] == "digits" and got["artifact"] == str(target) and got["decision"]["name"]
    assert any("DECISION" in line for line in got["report"])


def test_flux_task_check_rejects_a_bad_document(tmp_path, capsys):
    from flux_cli.main import main

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"statement": "x"}))
    assert main(["task", "check", str(bad)]) == 2
    assert "bad.json: `flow.test` is a command" in capsys.readouterr().out


# ---- propose: decompose (D431)
def _decomposed_task(**extra):
    script = ("import sys\nwant = {'head': ['0','1','2','3','4'], 'tail': ['5','6','7','8','9']}[sys.argv[2]]\n"
              "got = [g for g in open(sys.argv[1]).read().split('\\n') if g != '']\n"
              "bad = [i for i, (g, w) in enumerate(zip(got, want)) if g != w] + list(range(min(len(got), 5), 5))\n"
              "print(f'{len(bad)} failing')\nsys.exit(1 if bad else 0)")
    task = TaskSpec.from_dict({"id": "digits-decomposed",
                               "statement": "the ten digits, one per line",
                               "parts": "decompose",
                               "budget": {"steps": 4, "repair_attempts": 2, "prototype": False},
                               **extra,
                               "flow": {**extra.get("flow", {}),
                                        "test": ["{python}", "-c", script, "{artifact}", "{part}"]}})
    return replace(task, max_parts=3)             # D792: the loop's own bound, not a document key


def test_a_task_may_ask_the_orchestrator_to_decompose_it(tmp_path):
    task = _decomposed_task()
    assert task.decompose and task.parts == () and replace(TaskSpec.from_dict(task.to_dict()), max_parts=3) == task
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
    assert out.decision.candidate.artifact == "0\n1\n2\n3\n4\n\n\n5\n6\n7\n8\n9\n"
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
    with pytest.raises(TaskError, match="does not have: decompose"):      # `parts: decompose` is the one spelling (D629)
        TaskSpec.from_dict({**task.to_dict(), "parts": ["p"], "decompose": True})


def test_records_remember_and_recall_typed_decisions(tmp_path):
    from flux_records import Records

    r = Records(str(tmp_path / "r.db"), objective={"s": 1})
    r.remember("decomposition", {"parts": [{"name": "a"}]})
    r.remember("decomposition", {"parts": [{"name": "b"}]})
    r.remember("plan", {"next": "a"})
    assert [d["parts"][0]["name"] for d in r.recall("decomposition")] == ["a", "b"]
    assert r.recall("plan")[0]["next"] == "a" and r.recall("nothing") == []
    assert Records(str(tmp_path / "nodir" / "x.db"), objective={"s": 1}).recall("plan") == []


# ---- critique (D433)
def _critic(ok: bool, *issues: str) -> str:
    return json.dumps({"ok": ok, "issues": list(issues), "why": "critic"})


def test_a_critic_sends_a_passing_candidate_back_once_then_the_gate_rules(tmp_path):
    task = TaskSpec.from_dict({**load_task(DIGITS).to_dict(), "flow": {**load_task(DIGITS).to_dict().get("flow", {}), "critique": "model"},
                               "budget": {"steps": 3, "repair_attempts": 2, "prototype": False}})
    assert task.critique and TaskSpec.from_dict(task.to_dict()) == task
    # design passes the gate; the critic objects; the patch turn carries the objection; the
    # refined design passes and is admitted without a second critique; the next critic reply
    # is the decision's
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
    task = _decomposed_task(flow={"critique": "model"})
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
    r = problem.open_records(request_for(task, db=str(tmp_path / "dc.db")), lambda _m: None)
    assert [[p["name"] for p in d["parts"]] for d in r.recall("decomposition")] == [["head", "tail"]]
    kinds = [(c["kind"], c["ok"]) for c in r.recall("critique")]
    assert kinds == [("decomposition", False), ("candidate", True), ("candidate", True), ("decision", True)]


def test_without_a_critic_nothing_changes(tmp_path):
    task = load_task(DIGITS)
    proposer = ScriptedProposer([_reply(GOOD)])
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=proposer, log=lambda m: None)
    assert out.decision is not None and len(proposer.prompts) == 1
    assert not any("critique" in ln for ln in out.lessons + out.not_established)
    task = TaskSpec.from_dict({**task.to_dict(), "flow": {**task.to_dict().get("flow", {}), "critique": "model"}})
    out = run_loop(PromptProblem(task), request_for(task, db="", critique_rounds=0),
                   proposer=ScriptedProposer([_reply(GOOD)]), log=lambda m: None)
    assert out.decision is not None                            # critique_rounds=0: no critic asked


def _tiny_judge(problem, built, cand, subgoal, state):
    from flux_loop import Verdict

    return Verdict(True, 0.0, "the hook's own judge")


def test_a_record_is_named_by_the_documents_id(tmp_path):
    """An edited document resumes its record: the record is named by the id, not the text (D628)."""
    a = TaskSpec.from_dict({"id": "t", "statement": "count to ten", "flow": {"test": {"test": ["a"]}}})
    b = TaskSpec.from_dict({"id": "t", "statement": "count to twenty", "flow": {"test": {"test": ["a"]}}})
    ra, rb = (PromptProblem(x).campaign_name(request_for(x, db="x.db")) for x in (a, b))
    assert ra == rb == "t"

def test_the_cache_is_always_on_and_keyed_on_what_measures(tmp_path):
    """D790: no `cache:` key -- the cache is always on, and a measurement is the same one only
    for the same candidate measured the same way: the stage's command, the script it names under
    `{home}`, the params. A changed clock measured from the cache was the bug."""
    from flux_loop import Candidate

    (tmp_path / "bench.py").write_text("print('t=1')\n")

    def key(clock="800", params=None):
        doc = {"id": "t", "statement": "x", "params": params or {}, "flow": {"test": "true", "measure": {
            "s": {"command": "{python} {home}/bench.py {artifact} --clock-ps " + clock, "metrics": ["t"]}}}}
        prob = PromptProblem(TaskSpec.from_dict(doc, base=tmp_path))
        assert prob.cache_suffix() == "t.json"
        return prob.cache_key(Candidate("c", "module m; endmodule"), "s", None)

    first = key()
    assert key() == first and key(clock="1000") != first and key(params={"n": 2}) != first
    (tmp_path / "bench.py").write_text("print('t=2')\n")
    assert key() != first, "the script the stage runs is part of the measurement"
    with pytest.raises(TaskError, match="keys a problem document does not have: cache"):
        TaskSpec.from_dict({"id": "t", "statement": "x", "cache": False, "flow": {"test": "true"}})


# ---- the flow (D542): one key per box of the drawing
def _flow_doc(flow, **more):
    return {"id": "t", "statement": "x",
            "flow": {"test": {"test": ["a"]},
                     "measure": {"screen": {"command": ["m"], "metrics_re": {"fmax_mhz": r"(\d+)"}},
                                 "confirm": {"command": ["m"], "metrics_re": {"fmax_mhz": r"(\d+)"}}}, **flow}, **more}


def test_the_flow_block_folds_into_the_rig_and_reads_back():
    from flux_loop.document import describe_flow

    def box(lines, name):
        return next(l for l in lines if l.startswith(name + ":"))

    task = TaskSpec.from_dict(_flow_doc({"orchestrate": "rules", "generate": {"catalog": ["a.txt"]},
                                         "critique": "model", "calibrate": "off",
                                         "knowledge": {"lessons": "mined"}, "feedback": "off", "test": "gate"}))
    assert task.roles == {"orchestrator": "rules", "knowledge": "mined"}
    assert task.generator == {"catalog": ["a.txt"]} and task.critique is True
    assert task.budget["calibrate"] is False
    assert TaskSpec.from_dict(task.to_dict()) == task
    lines = describe_flow(task)
    assert box(lines, "orchestrate").startswith("orchestrate: rules") and box(lines, "generate").startswith("generate: catalog of 1")
    assert box(lines, "critique").startswith("critique: model") and box(lines, "stages") == "stages: screen, confirm"
    assert box(lines, "calibrate") == "calibrate: off" and box(lines, "feedback").startswith("feedback: off")
    plain = describe_flow(TaskSpec.from_dict(_flow_doc({})))
    assert box(plain, "stage confirm") == "stage confirm: its command -- estimate: none (the tool runs on every design)"
    assert box(plain, "calibrate").startswith("calibrate: on")


@pytest.mark.parametrize("flow, more, message", [
    ({"test": {"agent": "claude"}}, {}, "never delegated"),       # D775: flow.test is the gate itself
    ({"measure": {"agent": "claude"}}, {}, "never delegated"),
    ({"validate": "llm"}, {}, r"flow.validate is rules \| model"),
    ({"dse": "sweep"}, {}, "flow.dse is `orchestrate`"),
    ({"orchestrate": "hillclimb"}, {}, "available:"),
    ({"analytical": ["screen"]}, {}, "not a box"),
    ({"knowledge": ["sheet"]}, {}, "is `off` or an object"),
    ({"winner": "llm"}, {}, "not a box"),
    ({"test": {"by": "claude"}}, {}, "never delegated"),
])
def test_a_flow_that_says_a_thing_twice_or_wrong_is_refused(flow, more, message):
    with pytest.raises(TaskError, match=message):
        TaskSpec.from_dict(_flow_doc(flow, **more))


def test_validate_llm_lets_the_model_object_before_a_step_is_spent(tmp_path):
    """`flow: {validate: model}` objections are said and kept as lessons but never stop the run (D556)."""
    import json

    from flux_llm import ScriptedProposer
    from flux_loop import LoopRequest, LoopState, PromptProblem, TaskSpec

    task = TaskSpec.from_dict(_flow_doc({"validate": "model"}))
    prob = PromptProblem(task)
    proposer = ScriptedProposer([json.dumps({"ok": False, "objections": ["fmax_mhz has no goal", "one stage measures nothing new"]})])
    state = LoopState(request=LoopRequest(batch=WHOLE), say=lambda _m: None, proposer=proposer, feedback=None)
    assert prob.objections(state) == ["fmax_mhz has no goal", "one stage measures nothing new"]
    assert "THE DOCUMENT:" in proposer.prompts[0] and '"validate": "model"' in proposer.prompts[0] and "validate: model" in proposer.prompts[0]
    assert PromptProblem(TaskSpec.from_dict(_flow_doc({}))).objections(state) == []        # rules only: no call
    assert prob.objections(LoopState(request=LoopRequest(batch=WHOLE), say=lambda _m: None, proposer=None, feedback=None)) == []


def test_a_document_learns_its_margins_from_the_calibrations(tmp_path):
    """Stage-to-stage calibration becomes the margin on the shallower stage, floored at the
    document's margin; a chain with a missing link keeps the document's margin (D562)."""
    from flux_loop import LoopRequest, LoopState, PromptProblem, TaskSpec
    from flux_loop.calibrate import Bias

    doc = {"id": "m",
           "statement": "m",
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize", "goal": 800, "stage": "deepest", "margin": 0.03}],
           "flow": {"test": {"test": ["true"]},
                    "measure": {"screen": {"command": ["m"], "metrics_re": {"fmax_mhz": '(\\d+)'}},
                                "confirm": {"command": ["m"], "metrics_re": {"fmax_mhz": '(\\d+)'}},
                                "route": {"command": ["m"], "metrics_re": {"fmax_mhz": '(\\d+)'}}}}}
    prob = PromptProblem(TaskSpec.from_dict(doc))
    said = []
    state = LoopState(request=LoopRequest(batch=WHOLE), say=said.append, proposer=None, feedback=None)
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


def test_a_command_is_a_string_or_a_list_and_a_flux_head_runs_this_flux():
    """A command stage names its metrics and the loop reads `name=value` lines, no regex per metric (D580)."""
    import sys

    from flux_loop import TaskSpec

    task = TaskSpec.from_dict({"id": "t",
                               "statement": "x",
                               "flow": {"test": "flux rtl test {artifact} --golden {home}/golden.py",
                                        "measure": {"s": {"command": "flux rtl measure {artifact} --stage synth",
                                                          "metrics": ["fmax_mhz", "area_um2"]},
                                                    "r": {"command": ["{python}", "-c", "print('k=1')"],
                                                          "metrics_re": {"k": 'k=(\\d+)'}}}}})
    assert task.gate.named("test").run == ("{python}", "-W", "ignore", "-m", "flux_cli.main", "rtl", "test", "{artifact}", "--golden", "{home}/golden.py")
    s, r = task.stages
    assert s.command[:5] == ("{python}", "-W", "ignore", "-m", "flux_cli.main") and s.metrics == ("fmax_mhz", "area_um2")
    import re

    out = ("warning: x_area_um2=9\n"               # not a token of its own: never read
           "fmax_mhz=1860.050 area_um2=32.586 power_w=0.000113 cell_count=336 path_ps=537.62\n")
    got = {m: float(re.search(s.metrics_re[m], out).group(1)) for m in s.metrics}
    assert got == {"fmax_mhz": 1860.05, "area_um2": 32.586}, got   # what `flux rtl measure` prints, one line
    assert r.metrics == ("k",) and r.metrics_re == {"k": "k=(\\d+)"}
    assert sys.executable  # the {python} substitution is the loop's, at run time


def test_a_space_and_a_generator_command_are_a_dse_with_no_world(tmp_path):
    """Each `space:` point runs the generator with its knobs as `{knob}`; the gate and stage see them; `dse: sweep` visits every point (D581)."""
    gen = tmp_path / "gen.py"
    gen.write_text("import sys\nout, width, fill = sys.argv[1], int(sys.argv[2]), sys.argv[3]\n"
                   "open(out, 'w').write(fill * width)\n")
    task = TaskSpec.from_dict({"id": "strings",
                               "statement": "a string, as short as the gate allows",
                               "objectives": [{"metric": "size", "direction": "minimize"}],
                               "budget": {"steps": 1, "prototype": False, "batch": 100},
                               "flow": {"orchestrate": {"policy": "sweep", "space": {"width": [4, 1, 2], "fill": ["a", "b"]}},
                                        "generate": {"command": "{python} " + str(gen) + " {artifact} {width} {fill}"},
                                        "test": ["{python}", "-c", "import sys; bad = sys.argv[1] == 'b'; print(f'{int(bad)} failing')", "{fill}"],
                                        "measure": {"screen": {"metrics": ["size"],
                                                               "command": ["{python}", "-c", "import sys; print('size=' + str(len(open(sys.argv[1]).read())))", "{artifact}"]}},
                                        "select": {"finalists": 0}}})
    problem = PromptProblem(task)
    out = run_loop(problem, request_for(task, db=""), log=lambda m: None)
    assert out.decision is not None and out.decision.candidate.knobs == {"width": 1, "fill": "a"}
    assert out.decision.candidate.artifact == "a" and out.decision.metrics["size"] == 1.0


def test_a_placeholder_that_is_no_knob_is_a_load_error():
    with pytest.raises(TaskError, match=r"\{widht\}.*neither a knob"):
        TaskSpec.from_dict({"id": "t",
                            "statement": "x",
                            "flow": {"test": {"test": "check {artifact} --width {widht}"},
                                     "orchestrate": {"space": {"width": [1, 2]}}}})
    # a script's own braces (a token with spaces) are the script's business
    TaskSpec.from_dict({"id": "t",
                        "statement": "x",
                        "flow": {"test": {"test": ["{python}", "-c", "print(f'{len(x)} failing') if x else None"]}}})


def test_a_stage_whose_tool_is_missing_is_said_to_be_skipped(tmp_path, capsys):
    """D590: `task check` and the report say which stage will not run and why."""
    import yaml

    from flux_cli.main import main
    from flux_loop import PromptProblem, TaskSpec, task_report_lines

    doc = {"statement": "x",
           "flow": {"test": {"test": ["true"]},
                    "measure": {"far": {"command": "true", "metrics": ["m"], "needs": ["no-such-tool-for-flux"]}}}}
    (tmp_path / "t.problem.yaml").write_text(yaml.safe_dump(doc))
    assert main(["task", "check", str(tmp_path / "t.problem.yaml")]) == 0
    out = capsys.readouterr().out
    assert "WILL SKIP stage far: needs no-such-tool-for-flux" in out
    prob = PromptProblem(TaskSpec.from_dict({**doc, "id": "t"}))
    assert prob.skipped_stages() == [("far", ["no-such-tool-for-flux"])]

    class Out:
        decision = None
        decided_by = ""
        confirmed = frontier = []
        admitted = {}
        refused, lessons, not_established, notes = [], [], [], []
        provenance = {}

    assert any(l.startswith("  NOT RUN: stage far") for l in task_report_lines(prob.task, Out(), prob))


def test_every_load_failure_names_the_file_and_a_misspelled_key_is_refused(tmp_path):
    """D590: no traceback for a missing file or a syntax error; a key no document has is named."""
    from flux_loop import TaskError, load_task

    with pytest.raises(TaskError, match=r"nope\.yaml: no such file"):
        load_task(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("statement: [unclosed\n")
    with pytest.raises(TaskError, match=r"bad\.yaml: not valid YAML \(line \d+"):
        load_task(bad)
    typo = tmp_path / "typo.yaml"
    typo.write_text("statement: y\nflow: {test: {test: [true]}}\nobjective: [{metric: m, direction: minimize}]\n")
    with pytest.raises(TaskError, match=r"typo\.yaml: keys a problem document does not have: objective \(did you mean objectives\?\)"):
        load_task(typo)


def test_a_test_that_exits_3_is_a_build_failure_run_once(tmp_path):
    """`flux rtl test` exit 3 (does not compile) is a build failure, not every vector failing,
    and the test runs once per candidate: fast_check reads build's run (D594)."""
    from flux_loop import BuildError, Candidate, LoopRequest, LoopState

    (tmp_path / "check.py").write_text(
        "import sys, pathlib\n"
        "p = pathlib.Path(sys.argv[1]); n = pathlib.Path(sys.argv[2])\n"
        "n.write_text(str(int(n.read_text() or 0) + 1) if n.exists() else '1')\n"
        "t = p.read_text()\n"
        "if 'SYNTAX' in t:\n    print('did not compile: line 1'); sys.exit(3)\n"
        "bad = sum(1 for i, ln in enumerate(t.split()) if ln != str(i))\n"
        "print(f'{bad} failing'); sys.exit(1 if bad else 0)\n")
    runs = tmp_path / "runs"
    doc = {"id": "d",
           "statement": "the digits",
           "flow": {"test": ["{python}", "{home}/check.py", "{artifact}", str(runs)]}}
    prob = PromptProblem(TaskSpec.from_dict(doc, base=tmp_path))
    state = LoopState(request=LoopRequest(batch=WHOLE, db=""), say=lambda _m: None, proposer=None, feedback=None, workdir=str(tmp_path))
    broken = Candidate("b", "SYNTAX here")
    with pytest.raises(BuildError, match="did not compile"):
        prob.build(broken, None, state)
    near = Candidate("n", "0 1 2 X")
    built = prob.build(near, None, state)
    fails, text = prob.fast_check(built, near, None, state)
    assert fails == 1 and "1 failing" in text
    assert runs.read_text() == "2", "the test ran once for each candidate, not again in fast_check"


def test_every_turn_is_in_the_transcript_and_flux_log_reads_it(tmp_path, capsys, monkeypatch):
    """A model turn goes to the run's `turns.jsonl`, and `flux log <record>` lists it, or shows one with --turn (D599)."""
    import io
    import urllib.request

    from flux_cli.main import main
    from flux_llm import OpenAIChatProposer, transcript
    from flux_loop import ops

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    replies = tmp_path / "replies.json"
    replies.write_text(json.dumps([_reply(GOOD)]))
    db = str(tmp_path / "t.db")
    assert main(["task", "run", str(DIGITS), "--db", db, "--replies", str(replies)]) == 0
    assert transcript.path() and transcript.path().endswith("turns.jsonl")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        if req.full_url.endswith("/api/show"):
            raise urllib.error.HTTPError(req.full_url, 404, "no", {}, io.BytesIO(b""))
        return _Resp(json.dumps({"choices": [{"finish_reason": "stop", "message": {"role": "assistant",
                                 "content": "THE REPLY TEXT"}}], "usage": {}}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setenv("FLUX_LLM_REMOTE", "0")
    monkeypatch.setenv("FLUX_LLM_STREAM", "0")                # one message, not a stream
    OpenAIChatProposer("tiny-model", think=False).propose("THE PROMPT TEXT")
    capsys.readouterr()
    assert main(["log", db]) == 0
    out = capsys.readouterr().out
    assert "1 turn(s)" in out and "model tiny-model" in out and "THE REPLY TEXT" in out
    assert main(["log", db, "--turn", "1"]) == 0
    assert "--- prompt ---\nTHE PROMPT TEXT" in capsys.readouterr().out
    assert ops.run_dir  # the record's pointer found the run directory


def test_what_flux_rtl_runs_is_checked_like_any_tool():
    """A command's tools are checked like any command's; a stage that declares `needs:` is skipped on them, not refused (D600)."""
    from flux_loop.document import _flux_rtl_tools

    py = ["{python}", "-W", "ignore", "-m", "flux_cli.main"]
    assert _flux_rtl_tools(py + ["rtl", "test", "{artifact}", "--golden", "g.py"]) == ["verilator"]
    assert _flux_rtl_tools(py + ["rtl", "measure", "{artifact}", "--stage", "synth"]) == ["yosys", "openroad"]   # its timing is OpenROAD's OpenSTA
    assert _flux_rtl_tools(py + ["rtl", "measure", "{artifact}", "--stage", "place"]) == ["yosys", "openroad"]
    assert _flux_rtl_tools(["{python}", "{home}/check.py", "{artifact}"]) == []


def test_a_stage_that_does_not_measure_an_objective_is_refused():
    """Each stage ranks its own rows, so a stage lacking an objective could never pass a design on (D625)."""
    from flux_loop import PromptProblem, TaskSpec, request_for

    doc = {"id": "t",
           "statement": "s",
           "language": "text",
           "objectives": [{"metric": "latency_cycles", "direction": "minimize"}, {"metric": "area_mm2", "direction": "minimize"}],
           "flow": {"test": {"test": "true"},
                    "measure": {"cost": {"command": "echo area_mm2=1", "metrics": ["area_mm2"]},
                                "model": {"command": "echo latency_cycles=1 area_mm2=1",
                                          "metrics": ["latency_cycles", "area_mm2"]}}}}
    task = TaskSpec.from_dict(doc)
    wrong = PromptProblem(task).validate(request_for(task))
    assert len(wrong) == 1 and "the cost stage does not measure latency_cycles" in wrong[0]


@pytest.mark.parametrize("key, value", [("world", "pkg.mod:World"), ("hooks", {"judge": "pkg.mod:judge"})])
def test_a_world_or_a_hook_is_no_key_of_a_document(key, value):
    """D803: what a document cannot say is a command beside it -- a search, a check, a stage, a
    composition -- not a Python object bound to the loop."""
    with pytest.raises(TaskError, match=f"keys a problem document does not have: {key}"):
        TaskSpec.from_dict({"id": "t", "statement": "x", "flow": {"test": "true"}, key: value})
