"""Invariants of `.github/workflows/ci.yml` that nothing else can catch.

CI is the one part of this repo whose failures are invisible by construction: a job that is
cancelled, skipped, or silently covering nothing still leaves a green-ish page. D129's lesson —
an unknown reads as a pass — applies to the workflow file itself, not just to the tests it runs.

These are cheap structural checks, not a substitute for the workflow actually running.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[3]
_WORKFLOW = _REPO / ".github/workflows/ci.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert _WORKFLOW.is_file(), f"no workflow at {_WORKFLOW}"
    # PyYAML parses the `on:` key as the boolean True — YAML 1.1's spelling of it. Left alone
    # rather than worked around: the tests below do not need it, and rewriting the key would be
    # this file inventing a schema the real parser does not use.
    return yaml.safe_load(_WORKFLOW.read_text())


def test_a_scheduled_run_is_never_cancelled_by_a_push(workflow):
    """A push to `main` shares workflow and ref with a nightly running on `main`, so a group
    keyed on those two alone lets the push cancel a 350-minute sweep. A cancelled run reports as
    cancelled rather than failed, so nothing would draw attention to a nightly that never once
    finished."""
    concurrency = workflow["concurrency"]
    assert "github.event_name" in concurrency["group"], (
        "scheduled and push runs share a concurrency group — a push will cancel the nightly"
    )
    cancel = str(concurrency["cancel-in-progress"])
    assert "schedule" in cancel, f"cancel-in-progress={cancel!r} does not exempt scheduled runs"


def test_every_test_file_the_workflow_names_exists(workflow):
    """The nightly jobs name specific paths. A renamed or deleted test would otherwise surface at
    3am as a pytest usage error, in the one job nobody is watching when it runs."""
    named: set[str] = set()
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            for token in (step.get("run") or "").split():
                if token.startswith("tests/") and token.endswith(".py"):
                    named.add(token)

    # Guards the guard: an extraction that silently found nothing would pass the loop below.
    assert len(named) >= 4, f"expected the hermetic job to name several files, found {sorted(named)}"
    missing = sorted(n for n in named if not (_REPO / "flux" / n).is_file())
    assert not missing, f"ci.yml names test files that do not exist: {missing}"


def test_the_integration_sweep_still_globs_rather_than_listing(workflow):
    """The sweep's coverage of new files is the glob. If it ever became an explicit list, adding a
    test would stop adding CI coverage — silently, and only for the new file."""
    sweep = workflow["jobs"]["integration"]["steps"][-1]["run"]
    assert "tests/integration/*.py" in sweep


def test_the_hermetic_job_asserts_on_skips(workflow):
    """Every test in the equivalence file skips itself when no hermetic Timeloop is found, and a
    file whose tests all skip exits 0 — so without this the from-source build could rot completely
    while the job stayed green (docs/decisions.md D207)."""
    steps = workflow["jobs"]["timeloop-hermetic"]["steps"]
    equivalence = next(s for s in steps if "equivalence" in (s.get("name") or "").lower())
    assert 'grep -q "skipped"' in equivalence["run"]
    assert "exit 1" in equivalence["run"]


def test_every_openroad_gated_file_is_in_the_physical_job_or_ollama_gated(workflow):
    """The physical job is an explicit list — exactly the shape the glob test above exists to
    forbid for the sweep, and the failure it predicts already happened once (docs/decisions.md
    D246): the D237 flagship was openroad-gated, absent from the list, and skipped green in the
    nightly for its whole life. This closes the class: every integration file that gates on
    `shutil.which("openroad")` must appear in the physical job's file list, unless it ALSO
    requires an Ollama server — no CI job supplies one (the disclosed hole at the top of
    ci.yml), so listing such a file would only add a guaranteed skip."""
    flux_root = _REPO / "flux"
    physical_run = " ".join(
        s.get("run") or "" for s in workflow["jobs"]["physical"]["steps"])
    unlisted = []
    for path in sorted((flux_root / "tests/integration").glob("test_*.py")):
        text = path.read_text()
        if 'shutil.which("openroad")' not in text:
            continue
        ollama_gated = "requires_ollama" in text or "_ollama_up" in text
        listed = f"tests/integration/{path.name}" in physical_run
        if not listed and not ollama_gated:
            unlisted.append(path.name)
    assert not unlisted, (
        f"openroad-gated files invisible to every CI job: {unlisted} — add them to the "
        "physical job's file list in ci.yml"
    )


def test_pipelines_in_the_workflow_do_not_swallow_exit_status(workflow):
    """`cmd | tee f` reports tee's status, not cmd's — the mistake D199 records, where every
    command in a loop reported rc=0 regardless of what happened."""
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            run = step.get("run") or ""
            if "| tee" in run:
                assert "set -o pipefail" in run, (
                    f"job {name!r} pipes into tee without pipefail — a failing command reads as a pass"
                )
