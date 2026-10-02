"""D775, D783: each box's own settings under `flow` -- test the gate, measure the stages by name,
dse its space and seeds, knowledge what is read and who digests it, select its finalists. The only
layout: the fields themselves are not a document's keys."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from flux_loop import TaskError, TaskSpec, load_task

APPS = Path(__file__).resolve().parents[2] / "applications"

NEW = {"id": "t", "statement": "x", "language": "python",
       "objectives": [{"metric": "t", "direction": "minimize"}],
       "budget": {"steps": 2},
       "flow": {"test": {"test": ["true"]},
                "measure": {"screen": {"command": "echo t=1", "metrics": ["t"]},
                            "confirm": {"command": ["echo", "t=2"], "metrics": ["t"], "timeout_s": 900}},
                "orchestrate": {"policy": "sweep", "space": {"n": [1, 2]}, "seeds": [{"n": 2}]},
                "knowledge": {"text": "a note", "by": "opencode"},
                "select": {"finalists": 3},
                "calibrate": "off"}}


def test_each_box_is_read_into_the_loops_fields_and_written_back_as_read():
    new = TaskSpec.from_dict(NEW)
    assert [s.name for s in new.stages] == ["screen", "confirm"] and new.stages[1].timeout_s == 900
    assert new.space == {"n": [1, 2]} and new.seeds == ({"n": 2},) and new.budget["finalists"] == 3
    assert new.knowledge == "a note" and new.flow["knowledge"] == {"agent": "opencode"} and new.budget["calibrate"] is False
    assert TaskSpec.from_dict(new.to_dict()) == new and list(new.to_dict()["flow"]["measure"]) == ["screen", "confirm"], "as read"


@pytest.mark.parametrize("key", ["gate", "stages", "space", "seeds", "knowledge"])
def test_a_field_is_not_a_documents_key(key):
    """D783: the one layout -- the fields `flow` is read into are no keys of a document."""
    with pytest.raises(TaskError, match=rf"keys a problem document does not have: {key}"):
        TaskSpec.from_dict({**NEW, key: {"x": 1}})


def test_the_budget_keeps_only_its_own():
    for knob in ("finalists", "calibrate"):
        with pytest.raises(TaskError, match=rf"budget keys \['{knob}'\] are not loop knobs"):
            TaskSpec.from_dict({**NEW, "budget": {knob: 2}})


def test_each_box_says_its_settings_in_the_forms_it_has():
    flow = lambda **f: TaskSpec.from_dict({**NEW, "flow": {**NEW["flow"], **f}})   # noqa: E731
    rtl = "flux rtl measure {artifact} --stage synth --clock-ps 1000"           # a tool Flux knows: the command alone
    t = flow(measure={"a": rtl, "b": rtl.split(), "c": {"command": "echo t=3", "metrics": ["t"], "timeout_s": 5}},
             )
    assert [s.name for s in t.stages] == ["a", "b", "c"] and t.stages[0].command == t.stages[1].command
    assert t.stages[2].timeout_s == 5
    with pytest.raises(TaskError, match=r"flow.measure.d: a command stage needs `metrics`"):
        flow(measure={"d": "echo t=1"})
    with pytest.raises(TaskError, match="a stage's name is its key"):
        flow(measure={"a": {"name": "a", "command": "x"}})
    with pytest.raises(TaskError, match="flow.measure is a map"):
        flow(measure=[{"name": "a", "command": "x"}])
    for agent_box in ("test", "measure"):
        with pytest.raises(TaskError, match="never delegated"):
            flow(**{agent_box: {"by": "claude"}})
    assert flow(orchestrate={"by": "claude", "space": {"n": [1, 2]}}).space == {"n": [1, 2]}
    assert flow(knowledge="off").flow["knowledge"] == ["none"]
    assert yaml.safe_load("k: off")["k"] is False and flow(knowledge=False).flow["knowledge"] == ["none"], \
        "YAML reads a bare off as false"
    assert flow(knowledge={"files": [], "by": "opencode"}).digest_by == "opencode"
    with pytest.raises(TaskError, match="stands alone"):
        flow(knowledge={"off": True, "by": "opencode"})
    with pytest.raises(TaskError, match="flow.knowledge keys"):
        flow(knowledge={"papers": "x"})
    assert flow(select={"by": "claude", "finalists": 1}).flow["select"] == {"agent": "claude"}


def test_every_application_is_in_the_layout():
    for doc in sorted([*APPS.glob("*/problem.yaml"), *APPS.glob("*/*.problem.yaml")]):
        raw = yaml.safe_load(doc.read_text())
        assert not set(raw) & {"gate", "stages", "space", "seeds", "knowledge"}, doc
        load_task(str(doc))


def test_a_design_admitted_under_todays_judge_is_not_judged_again(tmp_path):
    """D778: a document's judge is a version -- its gate, the files the gate names, the tools and
    this Flux. A resumed run keeps what today's judge admitted; a changed checker re-verifies it once."""
    import json

    from flux_llm import ScriptedProposer
    from flux_loop import LoopRequest, PromptProblem, run_loop

    (tmp_path / "check.py").write_text(
        "import sys\ngot = open(sys.argv[1]).read().split()\nprint(f'{int(got != [str(i) for i in range(10)])} failing')\n")
    doc = {"id": "j", "statement": "the ten digits, one per line", "language": "text",
           "budget": {"steps": 1, "prototype": False},
           "flow": {"test": "{python} {home}/check.py {artifact}"}}
    db = str(tmp_path / "j.db")
    good = json.dumps({"artifact": "\n".join(str(i) for i in range(10)) + "\n", "why": "as asked"})

    def run():
        said: list[str] = []
        problem = PromptProblem(TaskSpec.from_dict(doc, base=tmp_path))
        assert problem.versions()["judge"]
        run_loop(problem, LoopRequest(db=db, steps=1, prototype=False), proposer=ScriptedProposer([good] * 4),
                 log=said.append)
        return said

    run()
    second = run()
    assert any("kept frozen (its row was made by today's" in m for m in second), second
    assert not any("re-verified" in m for m in second)
    (tmp_path / "check.py").write_text((tmp_path / "check.py").read_text() + "# stricter now\n")
    third = run()
    assert any("re-verified" in m for m in third), third
    assert not any("re-verified" in m for m in run()), "once: recorded with today's judge"
