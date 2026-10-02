"""Skills (D588): the agents' SKILL.md folders, for the loop's model and the coding agents."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from flux_loop import PromptProblem, TaskError, TaskSpec, request_for, run_loop
from flux_loop.skills import AGENT_DIRS, SkillError, install, load_skills, skill_index, skill_text

DIGITS = Path(__file__).resolve().parents[2] / "core" / "loop" / "examples" / "digits" / "problem.json"


def make_skill(root: Path, name: str, description: str = "when writing digits", body: str = "Write one digit per line.",
               files: dict | None = None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n")
    for rel, text in (files or {}).items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    return d


def test_skills_load_from_a_folder_or_a_folder_of_them_and_refuse_what_is_not_one(tmp_path):
    one = make_skill(tmp_path / "lib", "digits", files={"ref/table.txt": "0..9"})
    make_skill(tmp_path / "lib", "hex", "when writing hexadecimal")
    assert [s.name for s in load_skills([one])] == ["digits"]
    assert sorted(s.name for s in load_skills([tmp_path / "lib"])) == ["digits", "hex"]
    assert load_skills([one])[0].files() == ["ref/table.txt"]
    (tmp_path / "empty").mkdir()
    with pytest.raises(SkillError, match="no SKILL.md"):
        load_skills([tmp_path / "empty"])
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "SKILL.md").write_text("---\nname: bad\n---\nno description\n")
    with pytest.raises(SkillError, match="no `description`"):
        load_skills([bad])
    make_skill(tmp_path / "other", "digits")
    with pytest.raises(SkillError, match="two skills are named 'digits'"):
        load_skills([one, tmp_path / "other" / "digits"])


def test_the_index_the_tool_and_the_agents_folders(tmp_path):
    skills = load_skills([make_skill(tmp_path, "digits", files={"ref/table.txt": "0..9"})])
    assert "Write one digit per line." in skill_index(skills), "small skills are in the prompt whole"
    assert "ref/table.txt" in skill_index(skills), "and their files are named for the tool"
    big = skill_index(skills, inline_chars=5)
    assert "digits: when writing digits" in big and "Write one digit" not in big, "a large library is an index"
    assert "Write one digit per line." in skill_index(skills, tools=False, inline_chars=5), "without the tool, whole"
    text = skill_text(skills, "digits")
    assert "Write one digit per line." in text and "ref/table.txt" in text
    assert skill_text(skills, "digits", "ref/table.txt") == "0..9"
    assert "has no file" in skill_text(skills, "digits", "../../etc/passwd")
    assert "no skill named 'nope'" in skill_text(skills, "nope")
    work = tmp_path / "work"
    install(skills, work)
    for rel in AGENT_DIRS:
        assert (work / rel / "digits" / "SKILL.md").is_file() and (work / rel / "digits" / "ref" / "table.txt").is_file()


def test_a_document_names_its_skills_and_the_model_reads_the_index_and_loads_one(tmp_path):
    make_skill(tmp_path / "skills", "digits")
    doc = {**{"id": "digits", **json.loads(DIGITS.read_text())}, "skills": ["skills"]}
    task = TaskSpec.from_dict(doc, base=tmp_path)
    assert task.skills == (str((tmp_path / "skills" / "digits").resolve()),)
    prob = PromptProblem(task)
    from flux_loop import LoopState, LoopRequest
    from flux_loop.tools import loop_tools

    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    prefix = prob.prompt_prefix(None, state)
    assert "SKILLS" in prefix and "Write one digit per line." in prefix and prefix.find("SKILLS") < prefix.find("REPLY SHAPE")
    tool = next(t for t in loop_tools(prob, None, state) if t.name == "skill")
    assert "Write one digit per line." in tool.run({"name": "digits"})
    with pytest.raises(TaskError, match="skills: .*no SKILL.md"):
        TaskSpec.from_dict({**doc, "skills": ["."]}, base=tmp_path)
    assert TaskSpec.from_dict(task.to_dict(), base=tmp_path).skills == task.skills, "round-trips"


def test_a_coding_agent_finds_the_skills_where_it_looks(tmp_path):
    make_skill(tmp_path / "skills", "digits")
    fake = tmp_path / "agent.py"
    fake.write_text("import sys\nfrom pathlib import Path\nart = Path(sys.argv[1])\n"
                    "skill = Path.cwd() / '.claude' / 'skills' / 'digits' / 'SKILL.md'\n"
                    "assert skill.is_file(), 'the skill is where Claude Code and OpenCode look'\n"
                    "art.write_text('\\n'.join(str(i) for i in range(10)) + '\\n')\n")
    doc = {**{"id": "digits", **json.loads(DIGITS.read_text())}, "skills": ["skills"],
           "flow": {**{"id": "digits", **json.loads(DIGITS.read_text())}.get("flow", {}), "generate": {"by": {"command": ["{python}", str(fake), "{artifact}"], "timeout_s": 60}}},
           "budget": {"steps": 1, "repair_attempts": 1, "prototype": False}}
    task = TaskSpec.from_dict(doc, base=tmp_path)
    out = run_loop(PromptProblem(task), request_for(task, db=""), proposer=None, log=lambda _m: None)
    assert out.decision is not None, out.refused


def test_the_asks_skills_go_to_the_author_and_into_the_problem(tmp_path):
    from flux_llm import ScriptedProposer
    from flux_loop.author import Ask, drive, workspace_skills

    make_skill(tmp_path / "given", "digits", body="Always check the digits with a script.")
    work = tmp_path / "w"
    skills = workspace_skills([tmp_path / "given"], work)
    assert [s.name for s in skills] == ["digits"] and (work / "skills" / "digits" / "SKILL.md").is_file()
    check = "import sys\nprint('0 failing')\n"
    doc = {"statement": "digits",
           "language": "text",
           "budget": {"steps": 1, "prototype": False},
           "flow": {"test": "{python} {home}/check.py {artifact}"}}
    reply = f"FILE problem.yaml\n```\n{yaml.safe_dump(doc)}```\nFILE check.py\n```\n{check}```\nWHY: ok\n"
    author = ScriptedProposer([reply])
    got = drive(Ask(prompt="digits", workdir=work, passes=1, skills=skills), proposer=author,
                run_pass=lambda t, p: run_loop(p, request_for(t, db=""), proposer=ScriptedProposer([json.dumps({"artifact": "0\n"})]),
                                               log=lambda _m: None), say=lambda _m: None)
    assert not got["error"]
    assert "Always check the digits with a script." in author.prompts[0], "a model author reads the skill whole"
    assert yaml.safe_load((work / "problem.yaml").read_text())["skills"] == ["skills"]
    assert got["task"].skills and got["problem"].skill_list()[0].name == "digits"


def test_flux_task_run_hands_the_skill_to_the_model(tmp_path, capsys):
    """The command line's --skill reaches the prompt (it once parsed and dropped it)."""
    from flux_cli.main import main

    make_skill(tmp_path / "lib", "magic-word", "when a task asks for the magic word", "The magic word is PERIWINKLE-42.")
    (tmp_path / "check.py").write_text("import sys\nprint(('0' if open(sys.argv[1]).read().strip() == 'PERIWINKLE-42' else '1') + ' failing')\n")
    (tmp_path / "magic.problem.yaml").write_text(yaml.safe_dump({"statement": "The magic word.",
                                                                 "language": "text",
                                                                 "budget": {"steps": 1,
                                                                            "repair_attempts": 1,
                                                                            "prototype": False},
                                                                 "flow": {"test": "{python} {home}/check.py {artifact}"}}))
    seen: list[str] = []
    import flux_llm

    real = flux_llm.ScriptedProposer

    class Spy(real):
        def propose(self, prompt, **kw):
            seen.append(prompt)
            return super().propose(prompt, **kw)

    flux_llm.ScriptedProposer = Spy
    try:
        replies = tmp_path / "r.json"
        replies.write_text(json.dumps([json.dumps({"artifact": "PERIWINKLE-42"})]))
        rc = main(["task", "run", str(tmp_path / "magic.problem.yaml"), "--skill", str(tmp_path / "lib"),
                   "--replies", str(replies), "--db", str(tmp_path / "m.db")])
    finally:
        flux_llm.ScriptedProposer = real
    assert rc == 0 and any("The magic word is PERIWINKLE-42." in p for p in seen)
