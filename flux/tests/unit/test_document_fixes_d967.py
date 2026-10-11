"""D967: a command never reads Flux's stdin; a custom agent's string command is split into words;
an inline sub-task reads its own surface words; a folder child keeps the parent's skills; a wrong
`max_subtasks` is the document's error."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def test_a_tool_reads_end_of_file_not_flux_stdin():
    from flux_loop.toolrun import run_tool

    got = run_tool([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], timeout_s=30)
    assert got.ok and got.stdout.strip() == "''"


def test_a_custom_agents_string_command_is_split_into_words():
    from flux_loop.agent import agent_spec

    spec = agent_spec({"command": 'my-agent --brief "{prompt_file}" --out {artifact}', "resume": "my-agent --resume {session}"})
    assert spec.argv == ("my-agent", "--brief", "{prompt_file}", "--out", "{artifact}")
    assert spec.resume == ("my-agent", "--resume", "{session}")
    assert agent_spec({"command": ["my-agent", "{prompt_file}"]}).argv == ("my-agent", "{prompt_file}")


def _parent(tmp_path: Path) -> Path:
    home = tmp_path / "p"
    (home / "kid").mkdir(parents=True)
    (home / "skills" / "s1").mkdir(parents=True)
    (home / "skills" / "s1" / "SKILL.md").write_text("---\nname: s1\ndescription: a skill\n---\nuse it\n")
    (home / "problem.yaml").write_text(
        "statement: parent\nskills: [skills]\nflow:\n  test: 'true'\n  critique: model\n"
        "subtasks:\n  - kid\n  - {id: inline, statement: an inline child, flow: {critique: model}}\n")
    (home / "kid" / "problem.yaml").write_text("statement: the folder child\n")
    return home


def test_inline_and_folder_children_read_surface_words_and_keep_the_parents_skills(tmp_path):
    from flux_loop import load_task

    task = load_task(_parent(tmp_path))
    kid, inline = task.subtasks
    assert (kid.id, inline.id) == ("kid", "inline")
    assert kid.critique and inline.critique, "critique: model, the surface word, in both"
    assert len(kid.skills) == len(inline.skills) == 1 and Path(kid.skills[0]).name == "s1"


@pytest.mark.parametrize("value", ["x", 0, True, 2.5])
def test_a_wrong_max_subtasks_is_the_documents_error(value):
    from flux_loop.document import TaskError
    from flux_loop.document.spec import TaskSpec

    with pytest.raises(TaskError, match="max_subtasks"):
        TaskSpec.from_dict({"id": "a", "statement": "s", "flow": {"test": "true"}, "subtasks": "decompose", "max_subtasks": value})
    assert TaskSpec.from_dict({"id": "a", "statement": "s", "flow": {"test": "true"}, "subtasks": "decompose",
                               "max_subtasks": 6}).max_subtasks == 6
