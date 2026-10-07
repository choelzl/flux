"""Owners reset stopped loops without losing inputs or affecting other loops."""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from flux_web.admin import _key, cache_root
from flux_web.workspace import Workspace
from test_web import H, _client, server  # noqa: F401 -- shared fixture


@pytest.fixture()
def reset_loop(server, monkeypatch):  # noqa: F811 -- imported pytest fixture
    app, tmp = server
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp / "cache"))
    bob = _client(app, "bob", "another long secret")
    files = [("files", ("problem.yaml", b"statement: reset example\n")),
             ("files", ("library/source.py", b"# keep this\n"))]
    assert bob.post("/api/apps", data={"name": "x"}, files=files, headers=H).status_code == 200
    w = Workspace(app.state.store.data, "bob")
    d = w.app("x")
    for rel in ("out/x.db", "runs/loop.log", "runs/loop.log.1.gz", "runs/asks/old/answer.md",
                "workbench/notes.md", ".author-work/problem.yaml", ".attachments/input.txt", ".git/config"):
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(rel)
    cache = cache_root() / _key("bob", "x")
    cache.mkdir(parents=True)
    (cache / "trace.json").write_text("trace")
    return app, bob, w, d, cache


def test_reset_clears_generated_data_and_history_preserving_inputs_and_settings(reset_loop):
    app, bob, w, d, cache = reset_loop
    store = app.state.store
    user = store.user(name="bob")
    first = store.add_run(user, "x", str(d / "out/x.db"), str(d / "runs/loop.log"), [], {})
    store.set_run(first, ended=time.time(), rc=0)
    other = store.add_run(user, "other", "", "", [], {})
    store.set_run(other, ended=time.time(), rc=0)
    ada = store.user(name="ada")
    another_user = store.add_run(ada, "x", "", "", [], {})
    store.set_run(another_user, ended=time.time(), rc=0)
    other_cache = cache_root() / _key("bob", "other")
    other_cache.mkdir()
    (other_cache / "keep").touch()
    store.set_env("loop:bob:x", "MY_VAR", "keep", False)
    store.server_set("adv:bob:x", {"raw_network": True})
    store.server_set("share:bob:x", {"ada": "watch"})
    w.set_meta("x", last_check={"ok": True}, last_start_digest="old", last_options={"passes": 2})

    preview = bob.get("/api/apps/x/reset")
    assert preview.status_code == 200
    assert {f["path"] for f in preview.json()["folders"]} == {
        f"{d / n}/" for n in ("out", "runs", "workbench", ".author-work")
    } | {f"{cache}/"}
    assert (d / "out/x.db").exists(), "preview is read-only"
    got = bob.post("/api/apps/x/reset", headers=H)
    assert got.status_code == 200, got.text
    assert all(not (d / n).exists() for n in ("out", "runs", "workbench", ".author-work"))
    assert not cache.exists() and (other_cache / "keep").exists()
    assert (d / "problem.yaml").read_text() == "statement: reset example\n"
    assert (d / "library/source.py").read_text() == "# keep this\n"
    assert (d / ".attachments/input.txt").is_file() and (d / ".git/config").is_file()
    assert w.meta("x") == {"document": "problem.yaml", "id": "x"}
    assert store.env("loop:bob:x")["MY_VAR"]["value"] == "keep"
    assert store.server_get("adv:bob:x") == {"raw_network": True}
    assert store.shares("bob", "x") == {"ada": "watch"}
    assert store.runs(user, "x") == []
    assert len(store.runs(user, "other")) == len(store.runs(ada, "x")) == 1
    preflight = bob.get("/api/apps/x/preflight").json()
    assert not preflight["checked"] and preflight["changed"] and preflight["options"] is None
    state = bob.get("/api/apps/x/state").json()
    assert not state["running"]
    assert any(a["action"] == "reset app" and str(cache) in a["detail"] for a in store.audit_log())
    assert bob.post("/api/apps/x/reset", headers=H).status_code == 200, "already empty is fine"


@pytest.mark.parametrize("busy", ["run", "author", "ask", "maintenance"])
def test_reset_refuses_active_or_starting_work(reset_loop, busy):
    app, bob, _w, d, cache = reset_loop
    if busy == "run":
        app.state.store.add_run(app.state.store.user(name="bob"), "x", "", "", [], {})
    elif busy == "author":
        (d / "runs/author.json").write_text(json.dumps({"pid": os.getpid(), "ended": None}))
    elif busy == "ask":
        (d / "runs/asks/active").mkdir()
        (d / "runs/asks/active/ask.json").write_text(json.dumps({"pid": os.getpid(), "ended": None}))
    else:
        app.state.maintenance._lock.acquire()
    try:
        assert bob.post("/api/apps/x/reset", headers=H).status_code == 409
        assert (d / "out/x.db").exists() and cache.exists()
    finally:
        if busy == "maintenance":
            app.state.maintenance._lock.release()


def test_reset_is_owner_only_and_requires_csrf(reset_loop):
    app, bob, _w, d, cache = reset_loop
    ada = _client(app, "ada", "correct horse battery")
    app.state.store.server_set("share:bob:x", {"ada": "edit"})
    for method in (ada.get, ada.post):
        assert method("/api/apps/x/reset", params={"owner": "bob"}, headers=H).status_code == 403
    assert bob.post("/api/apps/x/reset").status_code == 403
    assert bob.post("/api/apps/missing/reset", headers=H).status_code == 404
    assert (d / "out/x.db").is_file() and cache.exists()


def test_reset_never_removes_symlink_targets(reset_loop, tmp_path):
    from flux_web.reset import remove

    _app, bob, _w, d, cache = reset_loop
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    for p in (d / "out", d / "workbench", cache):
        remove(p)
        p.symlink_to(outside, target_is_directory=True)
    assert bob.post("/api/apps/x/reset", headers=H).status_code == 200
    assert (outside / "keep").read_text() == "keep"
    assert all(not p.is_symlink() for p in (d / "out", d / "workbench", cache))


def test_reset_refuses_linked_app_or_external_run_folder(reset_loop, tmp_path):
    from flux_web.reset import remove

    _app, bob, w, d, _cache = reset_loop
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").touch()
    remove(d / "runs")
    (d / "runs").symlink_to(outside, target_is_directory=True)
    assert bob.post("/api/apps/x/reset", headers=H).status_code == 400
    assert (d / "out/x.db").exists() and (outside / "keep").exists()
    (w.root / "linked").symlink_to(d, target_is_directory=True)
    assert bob.post("/api/apps/linked/reset", headers=H).status_code == 400
    assert (d / "out/x.db").exists()


def test_completed_agent_callbacks_do_not_recreate_reset_files(reset_loop):
    app, bob, w, d, _cache = reset_loop
    assert bob.post("/api/apps/x/reset", headers=H).status_code == 200
    app.state.authoring._finish(d, w, "x", 0)
    app.state.asks._finish(d / "runs/asks/old", 0)
    assert not (d / "runs").exists()
    assert w.meta("x") == {"document": "problem.yaml", "id": "x"}


def test_reset_refuses_cache_keys_shared_by_long_names(reset_loop):
    app, _bob, _w, _d, _cache = reset_loop
    user = app.state.store.add_user("a" * 40, "another long secret")
    w = Workspace(app.state.store.data, user.name)
    names = ["b" * 59 + suffix for suffix in ("1", "2")]
    for name in names:
        w.create(name, [("problem.yaml", b"statement: keep\n")])
    assert _key(user.name, names[0]) == _key(user.name, names[1])
    shared = cache_root() / _key(user.name, names[0])
    shared.mkdir()
    (shared / "keep").touch()
    client = _client(app, user.name, "another long secret")
    assert client.post(f"/api/apps/{names[0]}/reset", headers=H).status_code == 409
    assert (shared / "keep").exists()


def test_reset_reports_file_removal_failure_without_forgetting_history(reset_loop, monkeypatch):
    from flux_web import reset

    app, bob, _w, d, cache = reset_loop
    store = app.state.store
    user = store.user(name="bob")
    ident = store.add_run(user, "x", "", "", [], {})
    store.set_run(ident, ended=time.time(), rc=0)

    def refused(path):
        raise PermissionError(f"cannot remove {path}")

    monkeypatch.setattr(reset, "remove", refused)
    response = bob.post("/api/apps/x/reset", headers=H)
    assert response.status_code == 500 and "could not clear all files" in response.text
    assert store.runs(user, "x") and (d / "out/x.db").exists() and cache.exists()
    assert not any(a["action"] == "reset app" for a in store.audit_log())
    assert not app.state.maintenance._lock.locked()


def test_reset_refuses_metadata_linked_outside_the_loop(reset_loop, tmp_path):
    _app, bob, _w, d, cache = reset_loop
    outside = tmp_path / "metadata.json"
    outside.write_text('{"document":"problem.yaml","id":"x"}')
    (d / ".flux-app.json").unlink()
    (d / ".flux-app.json").symlink_to(outside)
    assert bob.post("/api/apps/x/reset", headers=H).status_code == 400
    assert outside.read_text() == '{"document":"problem.yaml","id":"x"}'
    assert (d / "out/x.db").exists() and cache.exists()


def test_reset_excludes_a_concurrent_new_start(reset_loop, monkeypatch):
    from flux_web import reset

    app, bob, _w, d, _cache = reset_loop
    entered, release, starting, launched = (threading.Event() for _ in range(4))
    clear = reset.clear

    def slow_clear(*args):
        entered.set()
        assert release.wait(10)
        clear(*args)

    def start():
        starting.set()
        app.state.runs.start(app.state.store.user(name="bob"), "x", d, "problem.yaml", "x", {})

    def launch(*args):
        assert not (d / "out").exists() and not (d / "runs").exists()
        launched.set()

    monkeypatch.setattr(reset, "clear", slow_clear)
    monkeypatch.setattr(app.state.runs, "_start", launch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        resetting = pool.submit(bob.post, "/api/apps/x/reset", headers=H)
        try:
            assert entered.wait(10)
            new_start = pool.submit(start)
            assert starting.wait(10)
            assert not launched.wait(0.1), "new starts wait until reset completes"
        finally:
            release.set()
        assert resetting.result().status_code == 200
        new_start.result()
        assert launched.is_set()
