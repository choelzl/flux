"""Every application is a document on the one loop (D519, D541).

Every `applications/<name>/` holds a `<name>.problem.yaml` that loads, names its campaign, and
declares its stages and objectives; what it cannot say is a command beside it (D798-D803); the loop's entry has the feedback seam; and no application
carries a `demo.py` or its own `Problem` subclass.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]
APPLICATIONS = sorted(p.name for p in (FLUX_ROOT / "applications").iterdir() if p.is_dir())


def test_every_application_is_a_document():
    assert APPLICATIONS == ["adder16", "bankmap", "gelu_fp16", "interconnect_mapping", "macarray", "mul8", "nlu", "npu_gemm", "prefetcher", "primes"]
    for app in APPLICATIONS:
        doc = FLUX_ROOT / "applications" / app / "problem.yaml"
        assert doc.is_file(), f"{app}: no problem document"
        assert not (FLUX_ROOT / "applications" / app / "demo.py").exists(), f"{app}: a demo beside the document"


@pytest.mark.parametrize("app", APPLICATIONS)
def test_the_document_loads_and_names_its_campaign_and_gate(app):
    from flux_loop import PromptProblem, load_task

    task = load_task(FLUX_ROOT / "applications" / app / "problem.yaml")
    assert task.id == app
    assert task.record == app, f"{app}: the campaign is not named after the document"
    assert task.stages, f"{app}: no stages"
    problem = PromptProblem(task)
    if task.gate:
        lib = FLUX_ROOT / "applications" / app / "lib"
        if lib.exists():                           # D798: a package beside it is its phases' commands
            assert not any((p / "world.py").exists() for p in (lib / "src").iterdir()), f"{app}: a world beside a document"
            assert app.split("_")[0] in " ".join(str(c.run) for c in task.gate) or any(
                ".steps" in " ".join(st.command or ()) for st in task.stages), f"{app}: a package no phase runs"
    else:
        assert task.subtasks, f"{app}: no gate and no sub-loops (D579, D801)"
    assert problem.objective(__import__("flux_loop").request_for(task, db=""))["study"] == app


def test_no_application_subclasses_the_problem_any_more():
    """No application subclasses `Problem`: an application is a document and its commands (D803)."""
    for app in APPLICATIONS:
        for src in (FLUX_ROOT / "applications" / app / "lib" / "src").rglob("*.py"):
            text = src.read_text()
            assert "(Problem)" not in text.replace("(Problem)\n", "(Problem)"), f"{src}: a Problem subclass"


def test_the_loop_entry_accepts_the_operator_channel():
    from flux_loop import run_loop

    assert "feedback" in set(inspect.signature(run_loop).parameters), "no feedback seam (D398)"
