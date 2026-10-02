"""D775: each box's own settings under `flow` -- test the gate, measure the stages by name, dse its
space and seeds, knowledge what is read and who digests it, select its finalists -- the old places
refused with where they went, and `flux task upgrade` for a document of the earlier layout."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from flux_loop import TaskError, TaskSpec, load_task
from flux_loop.document import upgrade

APPS = Path(__file__).resolve().parents[2] / "applications"

OLD = {"id": "t", "statement": "x", "language": "python",
       "gate": {"test": ["true"]},
       "stages": [{"name": "screen", "command": "echo t=1", "metrics": ["t"]},
                  {"name": "confirm", "command": ["echo", "t=2"], "metrics": ["t"], "timeout_s": 900}],
       "space": {"n": [1, 2]}, "seeds": [{"n": 2}],
       "knowledge": {"text": "a note"},
       "objectives": [{"metric": "t", "direction": "minimize"}],
       "flow": {"dse": "sweep", "knowledge": ["digest"], "calibrate": "off"},
       "budget": {"steps": 2, "finalists": 3}}

NEW = {"id": "t", "statement": "x", "language": "python",
       "objectives": [{"metric": "t", "direction": "minimize"}],
       "budget": {"steps": 2},
       "flow": {"test": {"test": ["true"]},
                "measure": {"screen": {"command": "echo t=1", "metrics": ["t"]},
                            "confirm": {"command": ["echo", "t=2"], "metrics": ["t"], "timeout_s": 900}},
                "dse": {"policy": "sweep", "space": {"n": [1, 2]}, "seeds": [{"n": 2}]},
                "knowledge": {"text": "a note", "digest": True},
                "select": {"finalists": 3},
                "calibrate": "off"}}


def test_the_new_layout_is_the_same_loop_as_the_old_said_the_old_way():
    new = TaskSpec.from_dict(NEW)
    assert [s.name for s in new.stages] == ["screen", "confirm"] and new.stages[1].timeout_s == 900
    assert new.space == {"n": [1, 2]} and new.seeds == ({"n": 2},) and new.budget["finalists"] == 3
    assert new.knowledge == "a note" and new.flow["knowledge"] == ["digest"] and new.budget["calibrate"] is False
    assert TaskSpec.from_dict(upgrade(OLD)) == new, "the upgrade says the same loop"
    assert TaskSpec.from_dict(new.to_dict()) == new and upgrade(new.to_dict()) == new.to_dict(), "written as read"


@pytest.mark.parametrize("key, where", [("gate", "flow.test"), ("stages", "flow.measure"), ("space", "flow.dse.space"),
                                        ("seeds", "flow.dse.seeds"), ("knowledge", "flow.knowledge")])
def test_an_old_place_is_refused_with_where_it_went(key, where):
    with pytest.raises(TaskError, match=rf"`{key}` is said as `{where}` now .*flux task upgrade"):
        TaskSpec.from_dict({**NEW, key: OLD[key]})


def test_the_budget_keeps_only_its_own():
    with pytest.raises(TaskError, match=r"`budget.finalists` is said as `flow.select.finalists`"):
        TaskSpec.from_dict({**NEW, "budget": {"finalists": 2}})
    with pytest.raises(TaskError, match=r"`budget.calibrate` is said as `flow.calibrate`"):
        TaskSpec.from_dict({**NEW, "budget": {"calibrate": False}})


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
            flow(**{agent_box: {"agent": "claude"}})
    assert flow(dse={"agent": "claude", "space": {"n": [1, 2]}}).space == {"n": [1, 2]}
    assert flow(knowledge="off").flow["knowledge"] == ["none"]
    assert yaml.safe_load("k: off")["k"] is False and flow(knowledge=False).flow["knowledge"] == ["none"], \
        "YAML reads a bare off as false"
    assert flow(knowledge={"files": [], "agent": "opencode"}).digest_by == "opencode"
    with pytest.raises(TaskError, match="stands alone"):
        flow(knowledge={"off": True, "agent": "opencode"})
    with pytest.raises(TaskError, match="flow.knowledge keys"):
        flow(knowledge={"papers": "x"})
    assert flow(select={"agent": "claude", "finalists": 1}).flow["select"] == {"agent": "claude"}


def test_every_application_is_in_the_layout():
    for doc in sorted(APPS.glob("*/*.problem.yaml")):
        raw = yaml.safe_load(doc.read_text())
        assert not set(raw) & {"gate", "stages", "space", "seeds", "knowledge"}, doc
        assert upgrade(raw) == raw, f"{doc}: already as `flux task upgrade` writes it"
        load_task(str(doc))


def test_flux_task_upgrade_rewrites_a_document_and_keeps_the_original(tmp_path, capsys):
    from flux_cli.main import main

    path = tmp_path / "t.problem.yaml"
    path.write_text(yaml.safe_dump(OLD, sort_keys=False))
    assert main(["task", "upgrade", "--dry-run", str(path)]) == 0
    assert "flow:" in capsys.readouterr().out and yaml.safe_load(path.read_text()) == OLD, "a dry run writes nothing"
    assert main(["task", "upgrade", str(path)]) == 0
    assert "upgraded" in capsys.readouterr().out
    assert yaml.safe_load((tmp_path / "t.problem.yaml.orig").read_text()) == OLD
    assert load_task(str(path)) == TaskSpec.from_dict(NEW)
    assert main(["task", "upgrade", str(path)]) == 0 and "already in the current layout" in capsys.readouterr().out
