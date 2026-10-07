"""Old starts retain their logs, nested tasks and agent conversations; results stay campaign-scoped."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from test_web import H, _client, server  # noqa: F401 -- fixture

from flux_records import Records
from flux_web.runs import loop_files


def _record(db, label, value, at):
    stamp = datetime.fromtimestamp(at + 5, timezone.utc).isoformat()
    with patch("flux_store.campaign._now", return_value=stamp), patch("flux_store.store._now", return_value=stamp):
        rec = Records(db, objective={"study": label}, name=label)
        campaign = rec.campaign_id
        rec.remember("objectives", {"objectives": [{"metric": "time_ms", "direction": "minimize", "goal": value + 1, "stage": "bench"}]})
        rec.trial({"name": "same-name", "artifact": label + " source"}, label, stage="bench", strategy="loop", metrics={"time_ms": value}, evaluator="bench")
        rec.conclude({"decision": "same-name", "decided_by": label + " decision"})
        rec.close("paused")
        return campaign


def _history(app):
    bob = _client(app, "bob", "another long secret")
    assert bob.post("/api/apps", data={"name": "past"}, files=[("files", ("problem.yaml", b"statement: history\n"))], headers=H).status_code == 200
    store = app.state.store
    d = store.data / "users/bob/apps/past"
    (d / "out").mkdir(exist_ok=True)
    files = loop_files(d)
    files["log"].parent.mkdir(exist_ok=True)
    old_log = f"\n── started {datetime.fromtimestamp(1000):%Y-%m-%d %H:%M:%S} by bob ──\nOLD tool output\n".encode()
    new_log = f"\n── started {datetime.fromtimestamp(2000):%Y-%m-%d %H:%M:%S} by bob ──\nNEW tool output\n".encode()
    files["log"].write_bytes(old_log + new_log)
    db = str(d / "out/history.db")
    ids, campaigns = [], []
    for at, offset, label, value in ((1000, 0, "old", 5), (2000, len(old_log), "new", 20)):
        rid = store.add_run(store.user(name="bob"), "past", db, str(files["log"]), ["flux"], {"log_offset": offset})
        store.set_run(rid, started=at, ended=at + 100, rc=0)
        ids.append(rid)
        campaigns.append(_record(db, label, value, at))
    trace = d / "out/trace"
    trace.mkdir()
    (d / "out/history.db.runs.json").write_text(json.dumps(dict.fromkeys(campaigns, str(trace))))
    events, turns = [], []
    for at, label in ((1000, "old"), (2000, "new")):
        events.extend([{"ev": "hello", "t": at + 1}, {"ev": "mark", "name": "pass", "n": 1, "t": at + 2},
                       {"ev": "start", "id": 1, "name": label + " design", "parent": None, "t": at + 3},
                       {"ev": "start", "id": 2, "name": "agent: claude", "parent": 1, "t": at + 4},
                       {"ev": "end", "id": 2, "output": {"reply": label + " reply", "steps": [{"k": "tool", "name": "bash", "out": label + " tool"}]}, "t": at + 5},
                       {"ev": "end", "id": 1, "t": at + 6}])
        turns.append({"ts": at + 5, "kind": "agent", "agent": "claude", "ok": True, "rc": 0, "reply": label + " reply",
                      "prompt": label + " prompt", "steps": [{"k": "tool", "name": "bash", "out": label + " tool"}]})
    (trace / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (trace / "turns.jsonl").write_text("".join(json.dumps(e) + "\n" for e in turns))
    files["answer"].write_text('{"decision": {"name": "CURRENT ANSWER"}}')
    return bob, d, ids, campaigns, old_log


def test_older_logs_and_complete_task_and_agent_output_are_readable(server):  # noqa: F811
    app, _ = server
    bob, _d, ids, campaigns, old_log = _history(app)
    history = bob.get("/api/apps/past/runs").json()
    assert [s["id"] for s in history["starts"]] == ids[::-1]
    assert {c["campaign_id"] for c in history["campaigns"]} == set(campaigns)
    r = bob.get("/api/apps/past/log/raw", params={"run_id": ids[0], "download": False})
    assert r.status_code == 200 and r.content == old_log and r.headers["content-disposition"].startswith("inline")
    args = {"run_id": ids[1], "start_id": ids[0], "campaign": campaigns[0]}
    events = bob.get("/api/apps/past/run-data", params={**args, "kind": "events"})
    rows = [json.loads(line) for line in events.text.splitlines()]
    assert len(rows) == 6 and rows[1]["name"] == "pass" and rows[3]["name"] == "agent: claude"
    assert "old tool" in events.text and "new tool" not in events.text
    turns = bob.get("/api/apps/past/turns", params=args).json()["turns"]
    assert len(turns) == 1 and turns[0]["reply"] == "old reply"
    full = bob.get("/api/apps/past/turns", params={**args, "k": turns[0]["k"]}).json()["turns"][0]
    assert full["steps"][0]["out"] == "old tool" and full["prompt"] == "old prompt"
    assert bob.get("/api/apps/past/turns", params={**args, "k": 2}).json()["turns"] == []
    transcript = bob.get("/api/apps/past/run-data", params={**args, "kind": "turns"})
    assert "old prompt" in transcript.text and "new prompt" not in transcript.text


def test_historical_results_and_source_do_not_leak_the_newer_campaign(server):  # noqa: F811
    app, _ = server
    bob, _d, ids, campaigns, _old_log = _history(app)
    for campaign, label, value in zip(campaigns, ("old", "new"), (5, 20)):
        args = {"run_id": ids[1], "campaign": campaign}
        r = bob.get("/api/apps/past/results", params=args).json()
        assert r["campaign"] == campaign and r["answer"] is None and r["decided_by"] == label + " decision"
        assert len(r["designs"]) == 1 and r["designs"][0]["numbers"]["time_ms"] == value
        assert r["designs"][0]["verdict"] == "accepted", "use this campaign's recorded objective"
        full = bob.get("/api/apps/past/design", params={**args, "design": "same-name"}).json()
        assert full["artifact"] == label + " source" and len(full["trials"]) == 1
        report = bob.get("/api/apps/past/report", params=args)
        assert report.status_code == 200


def test_preexisting_logs_without_saved_offsets_are_found_by_their_marker(server):  # noqa: F811
    app, _ = server
    bob, _d, ids, _campaigns, _old_log = _history(app)
    app.state.store.set_run(ids[0], options="{}")
    r = bob.get("/api/apps/past/log/raw", params={"run_id": ids[0]})
    assert r.status_code == 200 and "OLD tool output" in r.text and "NEW tool output" not in r.text


def test_resumed_campaign_results_stop_at_the_selected_starts_end(server):  # noqa: F811
    app, _ = server
    bob, d, ids, campaigns, _old_log = _history(app)
    db = str(d / "out/history.db")
    assert _record(db, "old", 50, 2000) == campaigns[0], "a campaign resumes across starts"
    args = {"run_id": ids[1], "campaign": campaigns[0], "start_id": ids[0]}
    older = bob.get("/api/apps/past/results", params=args).json()
    assert older["designs"][0]["numbers"]["time_ms"] == 5
    assert older["designs"][0]["verdict"] == "accepted" and older["objective_list"][0]["goal"] == 6
    assert len(older["passes"]) == 1
    design = bob.get("/api/apps/past/design", params={**args, "design": "same-name"}).json()
    assert [t["metrics"]["time_ms"] for t in design["trials"]] == [5]
    newer = bob.get("/api/apps/past/results", params={**args, "start_id": ids[1]}).json()
    assert newer["designs"][0]["numbers"]["time_ms"] == 50 and newer["objective_list"][0]["goal"] == 51
    assert len(newer["passes"]) == 2


def test_historical_rank_uses_the_objective_direction_at_that_time(server):  # noqa: F811
    app, _ = server
    bob, d, ids, campaigns, _old_log = _history(app)
    db = str(d / "out/history.db")
    for at, later in ((1010, False), (2010, True)):
        stamp = datetime.fromtimestamp(at, timezone.utc).isoformat()
        with patch("flux_store.campaign._now", return_value=stamp), patch("flux_store.store._now", return_value=stamp):
            rec = Records(db, objective={"study": "old"}, name="old")
            if later:
                rec.remember("objectives", {"objectives": [{"metric": "time_ms", "direction": "maximize", "goal": 6, "stage": "bench"}]})
            else:
                rec.trial({"name": "other", "artifact": "other source"}, "other", stage="bench", strategy="loop",
                          metrics={"time_ms": 7}, evaluator="bench")
            rec.close("paused")
    args = {"run_id": ids[1], "campaign": campaigns[0], "start_id": ids[0], "raw": True}
    older = bob.get("/api/apps/past/results", params=args).json()
    assert {d["base"]: d["rank"] for d in older["designs"]} == {"same-name": 1, "other": 2}
    assert len(older["rows"]) == older["rows_total"] == 2
    newer = bob.get("/api/apps/past/results", params={**args, "start_id": ids[1]}).json()
    assert {d["base"]: d["rank"] for d in newer["designs"]} == {"same-name": 2, "other": 1}


def test_history_remains_confined_to_the_authorized_loop(server, tmp_path):  # noqa: F811
    app, _ = server
    bob, d, ids, campaigns, _old_log = _history(app)
    app.state.store.add_user("cy", "cy has a long secret")
    cy = _client(app, "cy", "cy has a long secret")
    assert cy.get("/api/apps/past/runs", params={"owner": "bob"}).status_code == 403
    other = app.state.store.add_run(app.state.store.user(name="ada"), "past", "other.db", "other.log", [], {})
    assert bob.get("/api/apps/past/log/raw", params={"run_id": other}).status_code == 404
    assert bob.get("/api/apps/past/results", params={"run_id": ids[0], "campaign": "not-found"}).status_code == 404
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "events.jsonl").write_text("SECRET")
    (d / "out/history.db.runs.json").write_text(json.dumps({campaigns[0]: str(outside)}))
    assert bob.get("/api/apps/past/run-data", params={"run_id": ids[0], "campaign": campaigns[0], "kind": "events"}).status_code == 400
    assert bob.get("/api/apps/past/run-data", params={"run_id": ids[0], "campaign": campaigns[0], "kind": "../secret"}).status_code == 400


def test_raw_text_is_complete_and_plain_even_for_html_and_large_files(server):  # noqa: F811
    app, _ = server
    bob, d, _ids, _campaigns, _old_log = _history(app)
    text = "<script>alert('untrusted')</script>\n" + "é\n" * (1 << 20)
    (d / "large.html").write_text(text)
    raw = bob.get("/api/apps/past/file", params={"path": "large.html", "raw": True})
    assert raw.status_code == 200 and raw.text == text and raw.headers["content-type"].startswith("text/plain")
    assert raw.headers["content-disposition"].startswith("inline")
    assert "x-flux-truncated" not in raw.headers
    (d / "blob").write_bytes(b"\0\1")
    binary = bob.get("/api/apps/past/file", params={"path": "blob", "raw": True})
    assert binary.headers["content-type"] == "application/octet-stream" and binary.headers["content-disposition"].startswith("attachment")


@pytest.mark.parametrize("missing", ["log", "trace", "turns"])
def test_missing_history_is_reported_instead_of_showing_latest_data(server, missing):  # noqa: F811
    app, _ = server
    bob, d, ids, campaigns, _old_log = _history(app)
    if missing == "log":
        loop_files(d)["log"].unlink()
        response = bob.get("/api/apps/past/log/raw", params={"run_id": ids[0]})
    elif missing == "trace":
        (d / "out/trace/events.jsonl").unlink()
        response = bob.get("/api/apps/past/run-data", params={"run_id": ids[0], "campaign": campaigns[0], "kind": "events"})
    else:
        (d / "out/trace/turns.jsonl").unlink()
        response = bob.get("/api/apps/past/turns", params={"run_id": ids[0], "campaign": campaigns[0]})
    assert response.status_code == 404
