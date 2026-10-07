"""D936: an admin's per-loop mounts into the sandbox -- kept in the loop's Advanced settings, checked
(the host path there and nobody's data, the path inside not the sandbox's own), handed to the
loop's runs, Check and agent Tests, in the container's argv with their modes, said in the run's log
and audited."""

from __future__ import annotations

import json
import types

import pytest
from fastapi.testclient import TestClient

from flux_cli import sandbox
from flux_web import create_app
from flux_web.runs import sandbox_env
from flux_web.store import Store

H = {"X-Flux": "1"}


def _args(tmp_path):
    doc = tmp_path / "p" / "x.problem.yaml"
    doc.parent.mkdir(exist_ok=True)
    doc.write_text("")
    return types.SimpleNamespace(file=str(doc), db=str(tmp_path / "rec" / "x.db"), out=None, json=None, plan=None,
                                 replies=None, skill=[], no_sandbox=False)


def test_the_argv_carries_each_mount_with_its_mode_and_leaves_out_the_sandboxs_own(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FLUX_SANDBOX_HOME", str(tmp_path / "home"))
    (tmp_path / "ds").mkdir()
    (tmp_path / "scratch").mkdir()
    monkeypatch.setenv("FLUX_SANDBOX_MOUNTS", json.dumps([
        {"host": str(tmp_path / "ds"), "inside": "/mnt/ds", "mode": "ro"},
        {"host": str(tmp_path / "scratch"), "inside": "/mnt/scratch/", "mode": "rw"},
        {"host": str(tmp_path / "ds"), "inside": "/tmp/x", "mode": "rw"},          # the sandbox's own /tmp
        {"host": str(tmp_path / "ds"), "inside": "/home/flux", "mode": "ro"},      # its HOME
        {"host": str(tmp_path / "gone"), "inside": "/mnt/gone", "mode": "ro"},
        {"host": str(tmp_path / "ds"), "inside": "/etc/ssl", "mode": "ro"}]))
    cmd = sandbox.container_argv(["flux"], _args(tmp_path), "task run", "flux-t", None, "docker")
    vols = [c for c, prev in zip(cmd[1:], cmd) if prev == "-v"]
    assert f"{tmp_path / 'ds'}:/mnt/ds:ro" in vols and f"{tmp_path / 'scratch'}:/mnt/scratch:rw" in vols
    assert not any(v.startswith((f"{tmp_path / 'ds'}:/tmp", f"{tmp_path / 'ds'}:/home", f"{tmp_path / 'ds'}:/etc", f"{tmp_path / 'gone'}"))
                   for v in vols), vols
    said = capsys.readouterr().err
    assert "admin mounts:" in said and "/mnt/ds (read-only)" in said and "/mnt/scratch (read-write)" in said
    assert "/tmp/x left out" in said and "/mnt/gone left out" in said
    assert str(tmp_path) not in said, "mount summaries use the container's destinations"
    assert "FLUX_SANDBOX_MOUNTS" not in sandbox.container_env(cmd), "the host side is the admin's"
    env = {"FLUX_SANDBOX_MOUNTS": "stale"}
    sandbox_env(env, True, {})
    assert "FLUX_SANDBOX_MOUNTS" not in env
    sandbox_env(env, True, {"mounts": [{"host": "/srv/a", "inside": "/mnt/a", "mode": "ro"}]})
    assert json.loads(env["FLUX_SANDBOX_MOUNTS"])[0]["inside"] == "/mnt/a"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    store = Store(tmp_path / "data")
    store.add_user("ada", "ada has a long secret", "admin")
    store.add_user("bob", "bob has a long secret")
    app = create_app(tmp_path / "data", sandbox=True)
    out = {}
    for n in ("ada", "bob"):
        c = TestClient(app)
        assert c.post("/api/login", json={"name": n, "password": f"{n} has a long secret"}, headers=H).status_code == 200
        out[n] = c
    out["bob"].post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\nlanguage: python\n"))], headers=H)
    out["ada"].post("/api/apps", data={"name": "y"}, files=[("files", ("y.problem.yaml", b"statement: s\nlanguage: python\n"))], headers=H)
    return out, store


@pytest.fixture()
def ds():
    """A host folder outside /dev (the tests' tmp_path may be on /dev/shm, a system path here)."""
    import os
    import shutil
    import tempfile
    from pathlib import Path

    base = next(d for d in (os.environ.get("TMPDIR"), str(Path.home() / ".cache"), "/tmp") if d and not d.startswith("/dev"))
    Path(base).mkdir(parents=True, exist_ok=True)
    d = Path(tempfile.mkdtemp(prefix="flux-d936-", dir=base))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_an_admin_sets_a_loops_mounts_and_the_refusals_say_why(server, tmp_path, ds):
    cs, store = server
    ada, bob = cs["ada"], cs["bob"]
    put = lambda mounts: ada.put("/api/apps/x/advanced?owner=bob", json={"mounts": mounts}, headers=H)   # noqa: E731
    assert bob.put("/api/apps/x/advanced", json={"mounts": [{"host": str(ds), "inside": "/mnt/ds"}]}, headers=H).status_code == 403
    loop = tmp_path / "data" / "users" / "bob"
    refused = {
        "relative": [{"host": "datasets", "inside": "/mnt/ds"}],
        "missing": [{"host": str(tmp_path / "nope"), "inside": "/mnt/ds"}],
        "data": [{"host": str(tmp_path / "data"), "inside": "/mnt/ds"}],
        "another user's": [{"host": str(tmp_path / "data" / "users" / "ada"), "inside": "/mnt/ds"}],
        "holds the data": [{"host": str(tmp_path), "inside": "/mnt/ds"}],
        "the loop's caches": [{"host": str(tmp_path / "xdg"), "inside": "/mnt/ds"}] if (tmp_path / "xdg").exists() else [{"host": "/", "inside": "/mnt/ds"}],
        "root": [{"host": "/", "inside": "/mnt/ds"}],
        "proc": [{"host": "/proc", "inside": "/mnt/ds"}],
        "etc": [{"host": "/etc", "inside": "/mnt/ds"}],
        "inside /tmp": [{"host": str(ds), "inside": "/tmp/ds"}],
        "inside HOME": [{"host": str(ds), "inside": "/home/flux/ds"}],
        "inside loop alias": [{"host": str(ds), "inside": "/sandbox/x/data"}],
        "inside cache alias": [{"host": str(ds), "inside": "/sandbox-cache/data"}],
        "inside Flux source": [{"host": str(ds), "inside": "/opt/flux/core"}],
        "inside /": [{"host": str(ds), "inside": "/"}],
        "inside /usr": [{"host": str(ds), "inside": "/usr/share/ds"}],
        "inside relative": [{"host": str(ds), "inside": "mnt/ds"}],
        "twice": [{"host": str(ds), "inside": "/mnt/ds"}, {"host": str(ds), "inside": "/mnt/ds/"}],
        "mode": [{"host": str(ds), "inside": "/mnt/ds", "mode": "rwx"}],
    }
    assert loop.exists()
    for what, rows in refused.items():
        r = put(rows)
        assert r.status_code in (400, 422), (what, r.text)
    loop_dir = next(p for p in loop.rglob("x.problem.yaml")).parent
    assert put([{"host": str(loop_dir), "inside": "/mnt/ds"}]).status_code == 400, "the loop's own folder"
    assert put([{"host": str(ds), "inside": str(loop_dir)}]).status_code == 400, "over the loop's folder inside"
    r = put([{"host": str(ds), "inside": "/mnt/ds", "mode": "ro"}, {"host": str(ds), "inside": "/mnt/out", "mode": "rw"}])
    assert r.status_code == 200, r.text
    assert r.json()["advanced"]["mounts"] == [{"host": str(ds.resolve()), "inside": "/mnt/ds", "mode": "ro"},
                                              {"host": str(ds.resolve()), "inside": "/mnt/out", "mode": "rw"}]
    seen = bob.get("/api/apps/x/env").json()["advanced"]
    assert seen["mounts"][0]["inside"] == "/mnt/ds", "the loop's owner sees them (admin only to change)"
    said = next(x for x in store.audit_log() if x["action"] == "sandbox mounts")
    assert said["user"] == "ada" and "bob/x" in said["detail"] and "/mnt/out (read-write)" in said["detail"]
    # the loop's runs get them, said in the run's log; its Check too
    from flux_web.runs import advanced, mounts_said

    adv = advanced(store, "bob", "x")
    env: dict[str, str] = {}
    sandbox_env(env, True, adv)
    assert json.loads(env["FLUX_SANDBOX_MOUNTS"])[1]["mode"] == "rw"
    assert mounts_said(adv) == "mounts: /mnt/ds (read-only), /mnt/out (read-write)"
    sandbox_env(env, True, advanced(store, "ada", "y"))
    assert "FLUX_SANDBOX_MOUNTS" not in env, "another loop: none"
    assert put([]).status_code == 200 and "mounts" not in advanced(store, "bob", "x")


def test_each_refusal_names_its_reason(ds):
    from flux_web.runs import check_mounts

    data, caches = ds / "data", ds / "caches"
    loop = data / "users" / "bob" / "x"
    for d in (loop, data / "users" / "ada", caches, ds / "shared"):
        d.mkdir(parents=True)

    def why(host, inside="/mnt/d", mode="ro"):
        try:
            check_mounts([{"host": str(host), "inside": inside, "mode": mode}], data, loop, caches)
        except ValueError as exc:
            return str(exc)
        return ""

    assert "server's data" in why(data / "users" / "ada") and "server's data" in why(ds), "another user's; a folder holding it"
    assert "caches" in why(caches) and "system path" in why("/proc") and "system path" in why("/etc/ssl")
    assert "not there" in why(ds / "gone") and "absolute" in why("shared")
    assert "sandbox's own" in why(ds / "shared", "/tmp/d") and "sandbox's own" in why(ds / "shared", str(loop))
    assert why(ds / "shared", "/mnt/d", "rw") == ""
