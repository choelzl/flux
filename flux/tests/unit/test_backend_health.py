"""`flux_backend_health` (docs/decisions.md D156) — the probe must *detect* unavailability, not
just report success when everything happens to be installed.

A health check that cannot fail is worse than none: it tells an agent to proceed and takes the
blame for the adapter's error later. The cases below are the ones this session actually hit —
a Docker client present with a daemon refusing connections, and a missing external tool.
"""

from __future__ import annotations

import subprocess

import pytest
from flux_chia_nodes.health import BackendHealth, _docker_reachable, _probe, flux_backend_health


def test_a_present_client_with_a_dead_daemon_is_not_reachable(monkeypatch):
    """`which docker` succeeding says nothing about the daemon. This is exactly what happened
    mid-session: the binary was there and the socket refused the connection."""
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], 1, stdout="", stderr="permission denied while trying to connect to the docker API"))

    ok, detail = _docker_reachable()

    assert ok is False
    assert "unreachable" in detail and "permission denied" in detail


def test_a_missing_client_is_reported_before_any_subprocess(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)

    def _boom(*a, **k):
        raise AssertionError("must not shell out when the client is absent")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert _docker_reachable() == (False, "docker not on PATH")


def test_a_backend_whose_tool_is_missing_is_reported_unusable(monkeypatch):
    """`rtl` needs verilator. Without it the backend is not usable, and the reason names the tool
    rather than surfacing as a compile error inside the harness later."""
    monkeypatch.setattr("flux_chia_nodes.health.shutil.which", lambda _: None)

    health = _probe("rtl")

    assert health.importable is True and health.tool_available is False
    assert health.usable is False and "verilator" in health.detail


def test_an_unimportable_adapter_is_reported_rather_than_raising(monkeypatch):
    """A backend whose Python dependencies are missing must come back as a *finding*, not an
    exception — the whole point is answering "what can I run" without crashing."""
    import flux_cli.registry as registry

    def _fail(name):
        raise ImportError("No module named 'zigzag'")

    monkeypatch.setattr(registry, "make_evaluator", _fail)

    health = _probe("zigzag")

    assert health.usable is False
    assert "adapter unavailable" in health.detail and "ImportError" in health.detail


def test_the_report_lists_every_registered_backend_and_says_what_it_checked():
    report = flux_backend_health.__wrapped__()

    from flux_cli.registry import available_backends

    # Guards the guard: both sides come from the registry, so an empty registry would satisfy the
    # equality below while checking nothing (docs/decisions.md D198).
    assert len(available_backends()) >= 12
    assert [b.name for b in report.backends] == available_backends()
    # The honesty clause travels with the data: prerequisites present is not a promise of success,
    # which matters because a tool can be installed and broken (D145's CACTI segfault).
    assert "NOT a guarantee" in report.to_dict()["checked"]


def test_usable_backends_is_derived_not_stored():
    report_dict = flux_backend_health.__wrapped__().to_dict()
    usable = [b["name"] for b in report_dict["backends"] if b["usable"]]
    assert report_dict["usable_backends"] == usable


def _timeloop_probe(monkeypatch, *, use_local, docker_ok, hermetic_installed):
    """Drive `_probe("timeloop")` with the three independent facts that decide its answer."""
    class _Evaluator:
        pass

    ev = _Evaluator()
    ev.use_local = use_local
    monkeypatch.setattr("flux_cli.registry.make_evaluator", lambda _n: ev)
    monkeypatch.setattr(
        "flux_chia_nodes.health._docker_reachable",
        lambda: (docker_ok, "docker daemon reachable" if docker_ok else "docker daemon unreachable: x"),
    )
    monkeypatch.setattr(
        "flux_chia_nodes.health._hermetic_timeloop_installed", lambda: hermetic_installed
    )
    return _probe("timeloop")


def test_a_dead_docker_daemon_does_not_condemn_a_hermetic_timeloop(monkeypatch):
    """Since docs/decisions.md D206 the hermetic runner needs no daemon, so reporting `timeloop`
    unusable because Docker is down would send an agent to a different backend for no reason."""
    health = _timeloop_probe(monkeypatch, use_local=True, docker_ok=False, hermetic_installed=True)
    assert health.usable
    assert "hermetic" in health.detail
    # Guards the guard: the probe must not have consulted Docker at all — a detail mentioning the
    # daemon would mean the reachability check still gates the answer.
    assert "docker" not in health.detail.lower()


def test_an_installed_but_unselected_hermetic_timeloop_reads_as_unusable_and_says_why(monkeypatch):
    """Unusable *as configured* is the honest answer — the adapter will not silently switch runners
    (D206). But stopping at "docker unreachable" would hide that the fix is an environment
    variable, not a daemon."""
    health = _timeloop_probe(monkeypatch, use_local=False, docker_ok=False, hermetic_installed=True)
    assert not health.usable
    assert "FLUX_TIMELOOP_LOCAL=1" in health.detail
    assert "docker daemon unreachable" in health.detail


def test_the_docker_verdict_is_unchanged_where_no_hermetic_build_exists(monkeypatch):
    """The pre-D206 behaviour, which is still what most environments see."""
    down = _timeloop_probe(monkeypatch, use_local=False, docker_ok=False, hermetic_installed=False)
    assert not down.usable and down.detail == "docker daemon unreachable: x"

    up = _timeloop_probe(monkeypatch, use_local=False, docker_ok=True, hermetic_installed=False)
    assert up.usable and up.detail == "docker daemon reachable"
