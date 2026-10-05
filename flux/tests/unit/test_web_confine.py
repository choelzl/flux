"""D852: what the server reads and writes of a loop stays inside the loop. A run writes the loop's
runs/ and out/ and its cache, so it can leave a link there to anything the server's account reaches:
each one is refused -- the raw log, a run pointer, a record, an inbox -- and a file the server
replaces is replaced, never written through a link, an imported document's source kept."""

from __future__ import annotations

import json
import os

import pytest
from test_web import H, _client, _fake_start, server  # noqa: F401 -- the fixture

from flux_web.confine import Escape, append, open_read, replace, within
from flux_web.workspace import Workspace, WorkspaceError


def test_the_helpers_refuse_a_way_out(tmp_path):
    loop = tmp_path / "loop"
    loop.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("the operator's\n")
    (loop / "link").symlink_to(outside)
    with pytest.raises(Escape):
        within(loop / "link", loop)
    with pytest.raises(Escape):
        open_read(loop / "link", loop)
    with pytest.raises(Escape):
        append(loop / "link", "x\n", loop)
    assert outside.read_text() == "the operator's\n"
    replace(loop / "link", "mine\n", loop)                       # the link replaced by a file, not written through
    assert outside.read_text() == "the operator's\n" and not (loop / "link").is_symlink()
    assert (loop / "link").read_text() == "mine\n"
    (loop / "inner").symlink_to(loop / "link")                   # a link inside the loop to inside: fine to read
    with open_read(loop / "inner", loop, text=True) as fh:
        assert fh.read() == "mine\n"


def test_the_raw_log_is_not_followed_out_of_the_loop(server, tmp_path):  # noqa: F811
    """The review's reproduction: runs/loop.log a link to a file outside -- 400 from /file, and now
    from /log/raw too (it was 200 with the outside file's content)."""
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "demo"}, files=[("files", ("problem.yaml", b"statement: s\n"))], headers=H)
    d = app.state.store.data / "users" / "bob" / "apps" / "demo"
    outside = tmp_path / "outside.txt"
    outside.write_text("NOT THE LOOP'S\n")
    (d / "runs").mkdir(exist_ok=True)
    (d / "runs" / "loop.log").symlink_to(outside)
    assert bob.get("/api/apps/demo/file", params={"path": "runs/loop.log"}).status_code == 400
    raw = bob.get("/api/apps/demo/log/raw")
    assert raw.status_code == 400 and "NOT THE LOOP'S" not in raw.text


def test_a_record_or_a_pointer_that_leads_out_is_not_opened(server, tmp_path):  # noqa: F811
    """A run writes out/: its record replaced by a link (another user's record) leaves it without
    one on the pages; its run pointer naming a folder outside is not followed."""
    app, _ = server
    store = app.state.store
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    proc, _rid, d = _fake_start(app, "bob", "x")
    try:
        (d / "out").mkdir(exist_ok=True)
        theirs = tmp_path / "theirs.db"
        theirs.write_bytes(b"SQLite format 3\x00")
        (d / "out" / "x.db").symlink_to(theirs)
        run = app.state.runs.latest(store.user(name="bob"), "x")
        assert run["db"].endswith(".refused") and "link" in run["refused"]
        (d / "out" / "x.db").unlink()
        (d / "out" / "x.db-wal").symlink_to(tmp_path / "anything")     # SQLite would write through it
        assert app.state.runs.latest(store.user(name="bob"), "x")["db"].endswith(".refused")
        (d / "out" / "x.db-wal").unlink()
        (d / "out" / "x.db.runs.json").write_text(json.dumps({"c1": str(tmp_path)}))
        assert app.state.runs.campaign(app.state.runs.latest(store.user(name="bob"), "x")) == ("c1", None)
    finally:
        proc.kill()


def test_a_note_is_not_written_through_a_linked_inbox(server, tmp_path):  # noqa: F811
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    bob.post("/api/apps", data={"name": "x"}, files=[("files", ("x.problem.yaml", b"statement: s\n"))], headers=H)
    proc, _rid, d = _fake_start(app, "bob", "x")
    try:
        target = tmp_path / "victim.txt"
        target.write_text("kept\n")
        (d / "runs" / "inbox.jsonl").symlink_to(target)
        r = bob.post("/api/apps/x/notes", json={"text": "hello"}, headers=H)
        assert r.status_code == 400 and target.read_text() == "kept\n"
    finally:
        proc.kill()


def test_a_replacement_is_never_written_through_a_link(tmp_path):
    """The review's reproductions: replacing a loop's files through an existing symbolic link wrote
    the outside file; replacing an imported (hardlinked) document changed its source."""
    w = Workspace(tmp_path / "data", "bob")
    w.create("x", [("problem.yaml", b"statement: s\n"), ("check.py", b"print(0)\n")])
    d = w.app("x")
    outside = tmp_path / "outside.py"
    outside.write_text("the operator's\n")
    (d / "check.py").unlink()
    (d / "check.py").symlink_to(outside)
    w.create("x", [("problem.yaml", b"statement: s\n"), ("check.py", b"print(1)\n")], replace=True)
    assert outside.read_text() == "the operator's\n" and (d / "check.py").read_text() == "print(1)\n"
    # an imported document: the loop's file a hardlink of the application's source
    source = tmp_path / "apps" / "problem.yaml"
    source.parent.mkdir()
    source.write_text("statement: the original\n")
    (d / "problem.yaml").unlink()
    os.link(source, d / "problem.yaml")
    w.create_from_text("x", "problem.yaml", "statement: edited\n")
    assert source.read_text() == "statement: the original\n", "the source of an import is never edited"
    assert (d / "problem.yaml").read_text() == "statement: edited\n"
    w.write("x", "problem.yaml", "statement: again\n")
    w.add("x", [("check.py", b"print(2)\n")])
    assert source.read_text() == "statement: the original\n" and outside.read_text() == "the operator's\n"
    # a folder link inside the upload that leads out is refused
    (d / "sub").symlink_to(tmp_path)
    with pytest.raises(WorkspaceError):
        w.add("x", [("sub/planted.txt", b"x")])
    assert not (tmp_path / "planted.txt").exists()
