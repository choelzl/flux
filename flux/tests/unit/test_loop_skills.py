"""A loop's skills (D1152): its `skills/` folder read without a word in the document, the built-in
`flux-loop-commands` handed to every coding agent of the loop, `skills: {off: [...]}` and `skills: off`
turning them off, and the workbench's digest said under `flow.knowledge`."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from flux_loop import PromptProblem, TaskError, load_task
from flux_loop.boxes import box_turn
from flux_loop.skills import AGENT_DIRS, LOOP_SKILLS, builtin_skills

DOC = {"statement": "Make it.", "language": "python", "flow": {"test": "true"}}


def _loop(tmp_path: Path, **said) -> Path:
    home = tmp_path / "loop"
    (home / "skills" / "s1").mkdir(parents=True)
    (home / "skills" / "s1" / "SKILL.md").write_text("---\nname: s1\ndescription: how to make it\n---\nMake it well.\n")
    (home / "problem.yaml").write_text(yaml.safe_dump({**DOC, **said}, sort_keys=False))
    return home / "problem.yaml"


def _names(skills) -> list[str]:
    return sorted(s.name for s in skills)


def test_the_built_in_skill_is_found_in_the_repository():
    assert [s.name for s in builtin_skills()] == list(LOOP_SKILLS)


def test_skills_beside_the_document_are_read_unsaid_and_every_agent_gets_the_built_in_one(tmp_path):
    task = load_task(str(_loop(tmp_path)))
    assert [Path(p).name for p in task.skills] == ["s1"] and task.skills_on
    problem = PromptProblem(task)
    assert _names(problem.skill_list()) == ["s1"], "the model's skill tool: the loop's own"
    assert _names(problem.agent_skill_list()) == ["flux-loop-commands", "s1"]
    assert "skills" not in task.to_dict(), "unsaid, not written"


def test_a_loop_without_skills_folder_still_gives_its_agents_the_built_in_one(tmp_path):
    home = tmp_path / "bare"
    home.mkdir()
    (home / "problem.yaml").write_text(yaml.safe_dump(DOC))
    problem = PromptProblem(load_task(str(home / "problem.yaml")))
    assert problem.skill_list() == [] and _names(problem.agent_skill_list()) == ["flux-loop-commands"]


@pytest.mark.parametrize("said, own, agents", [
    ({"off": ["flux-loop-commands"]}, ["s1"], ["s1"]),
    ({"off": ["s1"]}, [], ["flux-loop-commands"]),
    ("off", [], []),
    (False, [], []),
    (["skills/s1"], ["s1"], ["flux-loop-commands", "s1"]),
])
def test_skills_turned_off_one_by_one_or_all(tmp_path, said, own, agents):
    task = load_task(str(_loop(tmp_path, skills=said)))
    problem = PromptProblem(task)
    assert _names(problem.skill_list()) == own and _names(problem.agent_skill_list()) == agents
    assert task.to_dict()["skills"] == ("off" if said is False else said), "written as said"
    again = tmp_path / "again" / "problem.yaml"
    again.parent.mkdir()
    again.write_text(yaml.safe_dump({k: v for k, v in task.to_dict().items() if k != "id"}, sort_keys=False))
    (tmp_path / "again" / "skills").symlink_to(tmp_path / "loop" / "skills")
    assert load_task(str(again)).skills_off == task.skills_off


@pytest.mark.parametrize("said", [{"of": ["x"]}, {"off": "  "}, {"off": [1]}, 3])
def test_a_wrong_skills_setting_is_the_documents_error(tmp_path, said):
    with pytest.raises(TaskError, match="skills"):
        load_task(str(_loop(tmp_path, skills=said)))


FAKE = r'''import json, sys
from pathlib import Path
Path(sys.argv[2]).write_text(json.dumps({"ok": True}))
'''


def test_a_box_agent_finds_the_skills_in_its_work_directory_and_its_brief_points_at_them(tmp_path):
    problem = PromptProblem(load_task(str(_loop(tmp_path))))
    fake = tmp_path / "fake.py"
    fake.write_text(FAKE)
    st = SimpleNamespace(workdir=str(tmp_path / "work"), say=lambda _m: None, records=None, proposer=None, lessons=[])
    got = box_turn("critique", {"command": [sys.executable, str(fake), "{prompt_file}", "{artifact}"], "timeout_s": 60},
                   "Q?", {"type": "object"}, st, skills=problem.agent_skill_list())
    assert got == {"ok": True}
    turn = tmp_path / "work" / "agents" / "critique" / "001"
    for d in AGENT_DIRS:
        for name in ("flux-loop-commands", "s1"):
            assert (turn / d / name / "SKILL.md").is_file(), (d, name)
    brief = (turn / "BRIEF.md").read_text()
    assert "your skill `flux-loop-commands`" in brief and "Never open the record database" in brief
    bare = box_turn("critique", {"command": [sys.executable, str(fake), "{prompt_file}", "{artifact}"], "timeout_s": 60},
                    "Q?", {"type": "object"}, st, skills=[])
    assert bare == {"ok": True}
    assert "`flux loop tried --json`" in (tmp_path / "work" / "agents" / "critique" / "002" / "BRIEF.md").read_text(), \
        "no skill: the commands said in full"


@pytest.mark.parametrize("said, kept", [("off", "off"), (False, "off"), ("model", "model"), ("claude", {"agent": "claude"}),
                                        ({"by": "claude", "timeout_s": 99}, {"agent": {"preset": "claude", "timeout_s": 99}})])
def test_the_workbench_digest_is_said_under_knowledge_and_round_trips(tmp_path, said, kept):
    doc = {**DOC, "flow": {**DOC["flow"], "knowledge": {"digest": "codex", "lessons": "mined", "workbench": said}}}
    task = load_task(str(_loop(tmp_path, **doc)))
    assert task.bench_by == kept
    know = task.to_dict()["flow"]["knowledge"]
    assert know["workbench"] == ("off" if said is False else said) and know["digest"] == "codex" and know["lessons"] == "mined"


def test_the_workbench_digest_stays_beside_a_library_turned_off(tmp_path):
    task = load_task(str(_loop(tmp_path, flow={"test": "true", "knowledge": {"off": True, "workbench": "off"}})))
    assert task.bench_by == "off" and task.to_dict()["flow"]["knowledge"] == {"off": True, "workbench": "off"}
    assert load_task(str(_loop(tmp_path / "b", flow={"test": "true", "knowledge": "off"}))).bench_by is None


def test_a_workbench_digest_that_is_no_one_is_refused(tmp_path):
    with pytest.raises(TaskError, match="workbench"):
        load_task(str(_loop(tmp_path, flow={"test": "true", "knowledge": {"workbench": "somebody"}})))


def test_the_workbench_off_writes_its_file_list_only(tmp_path):
    from flux_loop.agent import WORKBENCH_README

    task = load_task(str(_loop(tmp_path, flow={"test": "true", "knowledge": {"workbench": "off"}})))
    bench = Path(task.workbench)
    (bench / "tools").mkdir(parents=True)
    (bench / "tools" / "a.py").write_text("# sums things\n")
    asked = []
    st = SimpleNamespace(say=lambda _m: None, proposer=SimpleNamespace(propose=lambda *a, **k: asked.append(a)))
    got = PromptProblem(task).bench_readme(st)
    assert not asked and "off" in got["described by"] and "tools/a.py" in (bench / WORKBENCH_README).read_text()
    assert json.dumps(got)
