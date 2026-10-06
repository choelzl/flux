"""D906: creating a loop under a name taken is a conflict -- its document and files untouched; the
same creation sent again (a retry after a lost answer) is answered as made; replacing is the edit's."""

from __future__ import annotations

import threading

from test_web import H, _client, server  # noqa: F401 -- the fixture

TEXT = "statement: s\nlanguage: text\n"


def test_a_taken_name_is_409_and_replaces_nothing(server):  # noqa: F811
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    made = bob.post("/api/apps/from-text", json={"name": "x", "filename": "problem.yaml", "text": TEXT}, headers=H)
    assert made.status_code == 200 and not made.json().get("existing")
    d = app.state.store.data / "users" / "bob" / "apps" / "x"
    (d / "golden.py").write_text("print(1)\n")
    again = bob.post("/api/apps/from-text", json={"name": "x", "filename": "problem.yaml", "text": "statement: other\n"}, headers=H)
    assert again.status_code == 409 and "exists" in again.json()["detail"]
    assert (d / "problem.yaml").read_text() == TEXT and (d / "golden.py").exists()
    retry = bob.post("/api/apps/from-text", json={"name": "x", "filename": "problem.yaml", "text": TEXT}, headers=H)
    assert retry.status_code == 200 and retry.json()["existing"] is True          # the same request: made
    up = bob.post("/api/apps", data={"name": "x"}, files=[("files", ("problem.yaml", b"statement: up\n"))], headers=H)
    assert up.status_code == 409 and (d / "problem.yaml").read_text() == TEXT
    # the explicit edit replaces
    assert bob.put("/api/apps/x/file", params={"path": "problem.yaml"}, json={"text": "statement: edited\n"}, headers=H).status_code == 200
    assert (d / "problem.yaml").read_text() == "statement: edited\n"


def test_two_creations_at_once_one_wins(server):  # noqa: F811
    app, _ = server
    w = app.state.store.data
    from flux_web.workspace import Exists, Workspace

    ws = Workspace(w, "bob")
    go, got = threading.Barrier(2), {}

    def make(text: str) -> None:
        go.wait()
        try:
            ws.create("y", [("problem.yaml", text.encode())])
            got[text] = "made"
        except Exists:
            got[text] = "exists"

    ts = [threading.Thread(target=make, args=(t,)) for t in ("statement: a\n", "statement: b\n")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(got.values()) == ["exists", "made"]
    winner = next(t for t, v in got.items() if v == "made")
    assert (ws.app("y") / "problem.yaml").read_text() == winner
