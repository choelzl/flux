"""Choosing between the Docker image and the hermetic Nix build (D206).

The choice is never made for the caller: auto-detecting a hermetic Timeloop on PATH would make the
same call produce numbers from a different tool per shell. Opt-in, like D147's `CACTI_BIN`. Runs
without either backend installed.
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
    """`FLUX_TIMELOOP_LOCAL` empty, "0" or "false" reads as off, so nobody opts in by accident."""
    monkeypatch.setenv("FLUX_TIMELOOP_LOCAL", value)
    assert TimeloopEvaluator().use_local is False


def test_asking_for_local_without_one_installed_fails_loudly(monkeypatch):
    """Opting in without a local build is an error, not a silent Docker fallback."""
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
    """An explicit argument beats the inherited environment."""
    monkeypatch.setenv("FLUX_TIMELOOP_LOCAL", "1")
    assert TimeloopEvaluator(use_local=False).use_local is False


def test_availability_needs_both_the_binary_and_the_front_end(monkeypatch):
    """Local availability needs both the binary and `timeloopfe`, which the adapter drives (D149)."""
    monkeypatch.setattr("shutil.which", lambda _: None)
    assert local_timeloop_available() is False


def test_the_two_runners_execute_the_same_driver_under_different_roots():
    """Both paths run the same script; only where the files live differs (container mount vs. working directory)."""
    docker = _driver_script(include_mapping_constraints=False)
    local = _driver_script(include_mapping_constraints=False, prefix="/scratch/xyz")

    assert docker.replace("/work/", "/scratch/xyz/") == local
    assert "/work/problem.yaml" in docker and "/scratch/xyz/problem.yaml" in local
    # guards the guard: a prefix that never appeared would make the equality trivially true
    assert docker != local


def test_mapping_constraints_reach_both_runners():
    for prefix in ("/work", "/scratch/xyz"):
        script = _driver_script(include_mapping_constraints=True, prefix=prefix)
        assert f"{prefix}/mapping_constraints.yaml" in script
        assert f"{prefix}/mapping_constraints.yaml" not in _driver_script(
            include_mapping_constraints=False, prefix=prefix
        )
