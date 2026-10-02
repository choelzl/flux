"""The Flux skill (`skills/flux/`) that coding agents install to drive Flux: it must load as a
skill, and the document it shows must be one Flux accepts."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from flux_loop.document import task_in
from flux_loop.skills import load_skills

SKILL = Path(__file__).resolve().parents[3] / "skills" / "flux"


def test_the_skill_loads_with_a_name_and_a_description():
    (skill,) = load_skills([SKILL])
    assert skill.name == "flux" and "design-space exploration" in skill.description
    assert "flux task check" in skill.body and "--json" in skill.body


def test_the_example_document_in_the_skill_is_a_valid_problem(tmp_path):
    body = (SKILL / "SKILL.md").read_text()
    example = re.search(r"```yaml\n(.*?)```", body, re.S).group(1)
    doc = yaml.safe_load(example)
    home = tmp_path / "mul8"                       # D786: the folder is the id
    home.mkdir()
    (home / "golden.py").write_text("PORTS = []\n")
    task = task_in(doc, home)
    assert task.id == "mul8" and [s.name for s in task.stages] == ["screen", "confirm"]


def test_the_reference_the_skill_points_at_exists():
    body = (SKILL / "SKILL.md").read_text()
    for rel in re.findall(r"`(flux/[\w/.]+\.md)`", body):
        assert (SKILL.parents[1] / rel).is_file(), rel
