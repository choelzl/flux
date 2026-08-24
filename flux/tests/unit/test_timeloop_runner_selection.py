"""Choosing between the Docker image and the hermetic Nix build (docs/decisions.md D206).

The property that matters here is that the choice is never made *for* the caller. A hermetic
Timeloop on PATH is now a normal thing to have (`nix develop .#timeloop`), and auto-detecting it
would mean the same call produces numbers from a different tool depending on which shell it ran
in, with `provenance.evaluator` the only trace. Every other backend that gained a local path took
the same opt-in shape (D147's `CACTI_BIN`).

These run without either backend installed: the selection logic is what's under test, not Timeloop.
"""

from __future__ import annotations

import pytest
from flux_evaluator_timeloop.adapter import _driver_script, local_timeloop_available
from flux_evaluator_timeloop import TimeloopEvaluator


def test_the_default_is_docker_with_no_environment_set(monkeypatch):
    monkeypatch.delenv("FLUX_TIMELOOP_LOCAL", raising=False)
    ev = TimeloopEvaluator()
    assert ev.use_local is False
    assert ev.evaluator_id.startswith("timeloop-docker@")


@pytest.mark.parametrize("value", ["", "0", "false"])
def test_the_off_spellings_stay_on_docker(monkeypatch, value):
    """`FLUX_TIMELOOP_LOCAL=0` reads as "off" to anyone who writes it, and a bare `export` leaves
    it empty. Treating either as truthy would opt callers in by accident."""
    monkeypatch.setenv("FLUX_TIMELOOP_LOCAL", value)
    assert TimeloopEvaluator().use_local is False


def test_asking_for_local_without_one_installed_fails_loudly(monkeypatch):
    """The alternative is falling back to Docker silently, which is how a run that was *supposed*
    to be hermetic ends up recorded as hermetic while an image produced the numbers."""
    monkeypatch.setattr(
        "flux_evaluator_timeloop.adapter.local_timeloop_available", lambda: False
    )
    with pytest.raises(RuntimeError, match="hermetic Timeloop"):
        TimeloopEvaluator(use_local=True)


def test_the_environment_selects_local_when_one_is_available(monkeypatch):
    monkeypatch.setattr("flux_evaluator_timeloop.adapter.local_timeloop_available", lambda: True)
    monkeypatch.setenv("FLUX_TIMELOOP_LOCAL", "1")
    ev = TimeloopEvaluator()
    assert ev.use_local is True
    assert ev.evaluator_id == "timeloop-nix@local"


def test_an_explicit_use_local_false_overrides_the_environment(monkeypatch):
    """An argument the caller wrote beats an inherited environment — otherwise a shell setting
    could silently override code that asked for Docker by name."""
    monkeypatch.setenv("FLUX_TIMELOOP_LOCAL", "1")
    assert TimeloopEvaluator(use_local=False).use_local is False


def test_availability_needs_both_the_binary_and_the_front_end(monkeypatch):
    """The adapter drives Timeloop through `timeloopfe`, never the binary directly (D149). A
    binary alone would satisfy a `which` check and then fail at import."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert local_timeloop_available() is False


def test_the_two_runners_execute_the_same_driver_under_different_roots():
    """The equivalence claim (D206) only means anything if both paths run the same script. The
    single difference is where the files live: a container mount vs. a working directory."""
    docker = _driver_script(include_mapping_constraints=False)
    local = _driver_script(include_mapping_constraints=False, prefix="/scratch/xyz")

    assert docker.replace("/work/", "/scratch/xyz/") == local
    assert "/work/problem.yaml" in docker and "/scratch/xyz/problem.yaml" in local
    # Guards the guard: a prefix that never appeared would make the equality above trivially true.
    assert docker != local


def test_mapping_constraints_reach_both_runners():
    for prefix in ("/work", "/scratch/xyz"):
        script = _driver_script(include_mapping_constraints=True, prefix=prefix)
        assert f"{prefix}/mapping_constraints.yaml" in script
        assert f"{prefix}/mapping_constraints.yaml" not in _driver_script(
            include_mapping_constraints=False, prefix=prefix
        )
