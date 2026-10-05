"""Structural invariants of `.github/workflows/ci.yml`.

A cancelled, skipped or empty CI job still looks green, so these check the workflow file itself.
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
    # PyYAML parses the `on:` key as the boolean True (YAML 1.1); the tests below do not need it.
    return yaml.safe_load(_WORKFLOW.read_text())


def test_a_scheduled_run_is_never_cancelled_by_a_push(workflow):
    """The concurrency group separates pushes from the nightly, so a push to `main` cannot cancel
    the nightly sweep."""
    concurrency = workflow["concurrency"]
    assert "github.event_name" in concurrency["group"], (
        "scheduled and push runs share a concurrency group — a push will cancel the nightly"
    )
    cancel = str(concurrency["cancel-in-progress"])
    assert "schedule" in cancel, f"cancel-in-progress={cancel!r} does not exempt scheduled runs"


def test_every_test_file_the_workflow_names_exists(workflow):
    """Every path the nightly jobs name exists."""
    named: set[str] = set()
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            for token in (step.get("run") or "").split():
                if token.startswith("tests/") and token.endswith(".py"):
                    named.add(token)

    # An extraction that found nothing would pass the loop below vacuously.
    assert len(named) >= 4, f"expected the hermetic job to name several files, found {sorted(named)}"
    missing = sorted(n for n in named if not (_REPO / "flux" / n).is_file())
    assert not missing, f"ci.yml names test files that do not exist: {missing}"


def test_the_integration_sweep_globs_test_files_and_only_test_files(workflow):
    """The sweep selects files by the glob `test_*.py`, so a new test is covered. Not `*.py`: a
    file with no tests (conftest.py, _helpers.py) makes pytest exit 5 (D371)."""
    sweep = workflow["jobs"]["integration"]["steps"][-1]["run"]
    assert "tests/integration/test_*.py" in sweep
    assert "tests/integration/*.py" not in sweep.replace("tests/integration/test_*.py", "")


def test_the_hermetic_job_asserts_on_skips(workflow):
    """The equivalence step fails when its tests skip (no hermetic Timeloop), since an all-skipped
    file passes (D207)."""
    steps = workflow["jobs"]["timeloop-hermetic"]["steps"]
    equivalence = next(s for s in steps if "equivalence" in (s.get("name") or "").lower())
    assert 'grep -q "skipped"' in equivalence["run"]
    assert "exit 1" in equivalence["run"]


def test_every_openroad_gated_file_is_in_the_physical_job_or_ollama_gated(workflow):
    """Every integration file gated on `shutil.which("openroad")` is in the physical job's
    explicit list (D246), unless it also needs an Ollama server, which no CI job provides."""
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
    """A `cmd | tee f` step keeps cmd's status (pipefail), not tee's (D199)."""
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            run = step.get("run") or ""
            if "| tee" in run:
                assert "set -o pipefail" in run, (
                    f"job {name!r} pipes into tee without pipefail — a failing command reads as a pass"
                )


def test_every_flux_command_the_workflow_runs_is_one_the_cli_takes():
    """D831: the job that installs with pip ran `flux new primes --kind sweep` and
    `primes/primes.problem.yaml` long after D786 and D825 changed both -- red for days, unseen.
    Every `flux ...` line of the workflow must parse with the CLI as it is, and name no document
    of the old layout."""
    import shlex

    from flux_cli.main import build_parser

    parser = build_parser()
    lines = [ln.strip() for ln in _WORKFLOW.read_text().splitlines()]
    calls = []
    for ln in lines:
        if ln.startswith("#") or "flux" not in ln:
            continue
        for part in ln.split("&&"):
            words = shlex.split(part.split(" #")[0], posix=True)
            for i, w in enumerate(words):
                if w.endswith("/bin/flux") or w == "flux":
                    calls.append(words[i + 1:])
                    break
    assert calls, "the workflow runs flux"
    for args in calls:
        if not args or args[0].startswith("-") or args[0] in ("--help",):
            continue
        try:
            parser.parse_args([a for a in args if a not in (">", "|")])
        except SystemExit as exc:
            raise AssertionError(f"the workflow runs `flux {' '.join(args)}`, which the CLI refuses") from exc
        assert not any(a.endswith(".problem.yaml") and "/" in a and a.split("/")[-1].split(".")[0] == a.split("/")[-2]
                       for a in args), f"`flux {' '.join(args)}` names a document of the old layout (D786)"
