"""A step of the evaluation chain is a stage, not a rung (D466).

The loop's surface says `stage`, and the old word survives nowhere in code or current docs (the
decision log keeps its past wording). Records written before the rename are abandoned.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

FLUX = pathlib.Path(__file__).resolve().parents[2]
ROOT = FLUX.parent


def test_the_loops_vocabulary_is_stage():
    import flux_loop

    assert hasattr(flux_loop, "StageNames") and not hasattr(flux_loop, "RungNames")
    assert flux_loop.Problem.stages(flux_loop.Problem()) == ["screen"]
    assert not hasattr(flux_loop.Problem, "rungs")
    scored = flux_loop.Scored(flux_loop.Candidate("c", ""), "screen", {"x": 1.0}, {})
    assert scored.stage == "screen" and not hasattr(scored, "rung")
    for hook in ("analytic_stages", "evaluator_name", "cutoff", "route"):
        assert hasattr(flux_loop.Problem, hook), hook


def test_a_document_declares_stages():
    from flux_loop import PromptProblem, TaskSpec

    task = TaskSpec.from_dict({"id": "renamed",
                               "statement": "write it",
                               "flow": {"test": {"test": ["true"]},
                                        "measure": {"size": {"command": ["wc", "-c", "{artifact}"],
                                                             "metrics_re": {"bytes": '(\\d+)'}}}}})
    assert [s.name for s in task.stages] == ["size"]
    assert PromptProblem(task).stages() == ["size"]
    assert "measure" in task.to_dict()["flow"] and "stages" not in task.to_dict() and "rungs" not in task.to_dict()


def test_the_old_word_is_gone_from_the_code_and_the_current_docs():
    """The old word appears nowhere outside the decision log."""
    kept = {ROOT / "docs" / "decisions.md"}
    skip_dirs = {"__pycache__", ".git", "site", "vendor", "result", ".nix-bin"}
    left: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if path.suffix not in {".py", ".md", ".json", ".yaml", ".yml", ".nix", ".toml"}:
            continue
        if path in kept or any(part in skip_dirs for part in path.parts):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if "rung" in text.lower() and path.name != "test_stage_rename.py":
            left.append(str(path.relative_to(ROOT)))
    assert not left, f"the old word survives in: {left}"
