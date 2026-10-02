"""D768: nothing Flux starts outlives what it was for -- a container ends itself when its client is
gone, the server removes one no process runs any more, a login is ended even when it ignores asking."""

from __future__ import annotations

import subprocess
import sys
import time
import types

from flux_cli import sandbox
from flux_web import admin


def _args(tmp_path):
    (tmp_path / "p").mkdir(exist_ok=True)
    doc = tmp_path / "p" / "x.problem.yaml"
    doc.write_text("")
    return types.SimpleNamespace(file=str(doc), db=str(tmp_path / "rec" / "x.db"), out=None, json=None, plan=None,
                                 replies=None, author_replies=None, skill=None, app_dir=None)


def test_a_login_or_test_container_ends_itself_under_podman(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUX_SANDBOX_TIMEOUT", "960")
    cmd = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", None, "podman")
    assert cmd[cmd.index("--timeout") + 1] == "960"
    assert "FLUX_SANDBOX_TIMEOUT" not in sandbox.container_env(cmd), "the client's, not the run's"
    assert "--timeout" not in sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", None, "docker")
    monkeypatch.delenv("FLUX_SANDBOX_TIMEOUT")
    assert "--timeout" not in sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", None, "podman"), "a loop runs on"


def test_a_container_no_process_runs_is_removed_after_its_grace(monkeypatch):
    now = 10_000.0
    rows = [{"name": "flux-aaaaaa", "state": "running", "started": now - 3600, "app": "ada.login"},     # its client gone
            {"name": "flux-bbbbbb", "state": "running", "started": now - 3600, "app": "ada.nlu"},       # a loop, attached
            {"name": "flux-cccccc", "state": "running", "started": now - 10, "app": "ada.agent-test"},  # just started
            {"name": "flux-dddddd", "state": "exited", "started": now - 3600, "app": "bob.x"}]
    killed = []
    monkeypatch.setattr(admin, "containers", lambda: {"containers": rows, "error": None})
    monkeypatch.setattr(admin, "attached", lambda: {"flux-bbbbbb": 999_999})
    monkeypatch.setattr(admin, "kill_container", lambda n: killed.append(n) or "removed")
    gone = admin.reap(now=now)
    assert killed == ["flux-aaaaaa"] and gone[0]["app"] == "ada.login" and gone[0]["said"] == "removed"
    monkeypatch.setattr(admin, "containers", lambda: {"containers": [], "error": "podman is not installed"})
    assert admin.reap(now=now) == []


def test_a_client_is_found_attached_by_its_command_line():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "run", "--rm", "--name", "flux-0123abcd"])
    try:
        time.sleep(0.3)
        assert admin.attached().get("flux-0123abcd") == p.pid
    finally:
        p.kill()
        p.wait()
    assert "flux-0123abcd" not in admin.attached()


def test_a_login_that_ignores_asking_is_ended(tmp_path):
    from flux_web.logins import Logins

    p = subprocess.Popen(["sh", "-c", "trap '' INT TERM; sleep 300"], start_new_session=True)
    time.sleep(0.3)
    sess = types.SimpleNamespace(proc=p)
    t0 = time.monotonic()
    Logins._kill(sess)
    assert p.wait(timeout=10) == -9 and time.monotonic() - t0 < 10


def test_a_login_a_former_server_started_is_removed_with_its_client(monkeypatch):
    """A server restarted: its login's client lives on (a session of its own), read by no one."""
    p = subprocess.Popen(["sh", "-c", "sleep 300"], start_new_session=True)
    try:
        rows = [{"name": "flux-eeeeee", "state": "running", "started": 0.0, "app": "ada.login"},
                {"name": "flux-ffffff", "state": "running", "started": 0.0, "app": "ada.nlu"}]
        killed = []
        monkeypatch.setattr(admin, "containers", lambda: {"containers": rows, "error": None})
        monkeypatch.setattr(admin, "kill_container", lambda n: killed.append(n) or "removed")
        monkeypatch.setattr(admin, "attached", lambda: {"flux-eeeeee": p.pid, "flux-ffffff": p.pid})
        assert admin.reap(now=1e9) == [] and p.poll() is None, "this server's own login: kept"
        monkeypatch.setattr(admin, "_ancestors", lambda pid: {pid, 1})              # as after a restart
        gone = admin.reap(now=1e9)
        assert [g["name"] for g in gone] == ["flux-eeeeee"] and killed == ["flux-eeeeee"], "a loop's run lives on"
        assert p.wait(timeout=5) == -9, "its client ended too"
    finally:
        if p.poll() is None:
            p.kill()
