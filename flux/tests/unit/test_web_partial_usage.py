"""Known partial tokens reach API totals; missing usage is not a reported zero."""

import json

from test_web import H, _client, server  # noqa: F401 -- fixture


def test_partial_usage_is_preserved_in_turns_loop_and_account_totals(server):  # noqa: F811
    app, _ = server
    bob = _client(app, "bob", "another long secret")
    ada = _client(app, "ada", "correct horse battery")
    assert bob.post("/api/apps/from-text", json={"name": "partial", "filename": "problem.yaml", "text": "statement: tokens\n"}, headers=H).status_code == 200
    store = app.state.store
    folder = store.data / "users/bob/apps/partial"
    trace = folder / "out/trace"
    trace.mkdir(parents=True)
    db, log = folder / "out/record.db", folder / "runs/loop.log"
    store.add_run(store.user(name="bob"), "partial", str(db), str(log), ["flux"], {})
    (folder / "out/record.db.runs.json").write_text(json.dumps({"campaign": str(trace)}))
    rows = [{"kind": "agent", "agent": "claude", "tokens_in": 166, "tokens_out": 20, "tokens_complete": True},
            {"kind": "agent", "agent": "claude", "tokens_in": 300, "tokens_out": 30, "tokens_complete": False, "rc": 124},
            {"kind": "agent", "agent": "codex", "tokens_complete": False, "rc": 124},
            {"kind": "model", "tokens_in": 20, "tokens_out": 2}]
    (trace / "turns.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    total = bob.get("/api/apps/partial/usage").json()["total"]
    assert total["tokens_in"] == 486 and total["tokens_out"] == 52
    assert (total["turns"], total["counted"], total["partial"]) == (4, 3, 2)
    turns = bob.get("/api/apps/partial/turns").json()["turns"]
    assert turns[1]["tokens_complete"] is False and turns[1]["tokens_in"] == 300
    assert turns[2]["tokens_complete"] is False and "tokens_in" not in turns[2] and "tokens_out" not in turns[2]
    for total in [bob.get("/api/usage").json(), next(t for t in ada.get("/api/admin/usage").json() if t["user"] == "bob")]:
        assert total["partial"] == 2 and total["counted"] == 3 and total["tokens_in"] == 486 and total["tokens_out"] == 52
