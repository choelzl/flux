"""D905: a link deleted is the link, its target kept; the web's document preview reads no knowledge
file outside the loop (the CLI still does); two variable saves at once both persist."""

from __future__ import annotations

import threading

from test_web import H, _client, server  # noqa: F401 -- the fixture

from flux_loop import load_task
from flux_loop.document import confined
from flux_web.configure import views

DOC = b"statement: s\nlanguage: python\n"


def _loop(app, user="bob", name="demo", extra=()):
    c = _client(app, user, "another long secret")
    files = [("files", ("problem.yaml", DOC)), *[("files", (p, b)) for p, b in extra]]
    assert c.post("/api/apps", data={"name": name}, files=files, headers=H).status_code == 200
    return c, app.state.store.data / "users" / user / "apps" / name


def test_deleting_a_link_deletes_the_link_not_its_target(server):  # noqa: F811
    app, _ = server
    bob, d = _loop(app, extra=[("target.txt", b"keep me\n")])
    (d / "alias.txt").symlink_to(d / "target.txt")
    r = bob.delete("/api/apps/demo/file", params={"path": "alias.txt"}, headers=H)
    assert r.status_code == 200, r.text
    assert not (d / "alias.txt").is_symlink() and not (d / "alias.txt").exists()
    assert (d / "target.txt").read_text() == "keep me\n"
    (d / "dangling").symlink_to(d / "nowhere")                       # a dangling link goes too
    assert bob.delete("/api/apps/demo/file", params={"path": "dangling"}, headers=H).status_code == 200
    assert not (d / "dangling").is_symlink()


def test_a_link_to_a_folder_outside_widens_nothing(server, tmp_path):  # noqa: F811
    app, _ = server
    bob, d = _loop(app)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "theirs.txt").write_text("not the loop's\n")
    (d / "door").symlink_to(outside)
    r = bob.delete("/api/apps/demo/file", params={"path": "door/theirs.txt"}, headers=H)
    assert r.status_code == 400
    assert (outside / "theirs.txt").read_text() == "not the loop's\n"
    assert bob.delete("/api/apps/demo/file", params={"path": "door"}, headers=H).status_code == 200
    assert not (d / "door").is_symlink() and (outside / "theirs.txt").exists()


def test_the_preview_reads_no_knowledge_outside_the_loop(server, tmp_path):  # noqa: F811
    app, _ = server
    secret = tmp_path / "secret.txt"
    secret.write_text("THE OPERATOR'S SECRET\n")
    bob, d = _loop(app, extra=[("notes.txt", b"in-loop knowledge\n")])
    for knowledge in (f"{{sheet: {secret}}}", "{files: [../../../../../secret.txt]}", "{files: [link.txt]}"):
        (d / "link.txt").unlink(missing_ok=True)
        (d / "link.txt").symlink_to(secret)
        (d / "problem.yaml").write_text(f"statement: s\nlanguage: text\nflow:\n  test: true {{artifact}}\n  knowledge: {knowledge}\n")
        r = bob.get("/api/apps/demo/document")
        assert r.status_code == 200, r.text
        got = r.json()
        assert "SECRET" not in r.text and got["normal"] is None and "outside the loop" in got["error"], (knowledge, got)
        v = bob.post("/api/apps/demo/validate", json={"text": (d / "problem.yaml").read_text()}, headers=H).json()
        assert not v["ok"] and "outside the loop" in v["error"]
    # the trusted CLI reads where the document says
    assert "SECRET" in load_task(str(d / "problem.yaml")).knowledge
    # in the loop: read
    (d / "problem.yaml").write_text("statement: s\nlanguage: text\nflow:\n  test: true {artifact}\n  knowledge: {files: [notes.txt], sheet: notes.txt}\n")
    got = bob.get("/api/apps/demo/document").json()
    assert got["error"] == "" and got["normal"] is not None
    assert views(d / "problem.yaml")["error"] == ""
    with confined(d):
        assert "in-loop knowledge" in load_task(str(d / "problem.yaml")).knowledge


def test_two_variable_saves_at_once_both_persist(server):  # noqa: F811
    app, _ = server
    store = app.state.store
    go = threading.Barrier(4)

    def save(prefix: str) -> None:
        go.wait()
        for i in range(25):
            store.set_env("loop:bob:demo", f"{prefix}_{i}", str(i))

    def share(user: str) -> None:
        go.wait()
        for i in range(25):
            store.set_share("bob", "demo", f"{user}{i}", "watch")

    threads = [threading.Thread(target=save, args=(p,)) for p in ("A", "B")] + [threading.Thread(target=share, args=(u,)) for u in ("u", "v")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.env("loop:bob:demo")) == 50
    assert len(store.shares("bob", "demo")) == 50
    bob, _d = _loop(app)
    put = [None, None]

    def put_env(i: int, key: str) -> None:
        put[i] = bob.put("/api/apps/demo/env", json={"name": key, "value": key.lower()}, headers=H).status_code

    ts = [threading.Thread(target=put_env, args=(i, k)) for i, k in enumerate(("FIRST", "SECOND"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert put == [200, 200]
    env = store.env("loop:bob:demo")
    assert env["FIRST"]["value"] == "first" and env["SECOND"]["value"] == "second"
