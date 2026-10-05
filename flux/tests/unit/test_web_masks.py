"""D850: the admin's stderr masks -- lines left out of what the pages show, the record untouched --
and Insights' endpoints and refused hosts removed until used again."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.insights import endpoints, network
from flux_web.masks import Masks, check
from flux_web.store import Store

H = {"X-Flux": "1"}


def test_masks_hide_matching_lines_and_say_how_many():
    m = Masks(["stale arg0", "/^ERROR rmcp/"])
    got = m.text("WARNING: failed to clean up stale arg0 temp dirs\nreal problem\nERROR rmcp::worker quit")
    assert got == "real problem\n[2 line(s) hidden by the admin's masks]"
    assert m.text("nothing to hide") == "nothing to hide"
    doc = m.fields({"turns": [{"stderr": "stale arg0 x\nkept", "reply": "stale arg0 in a reply stays"}], "fields": {"stderr (live tail)": "stale arg0"}})
    assert doc["turns"][0]["stderr"].startswith("kept") and doc["turns"][0]["reply"] == "stale arg0 in a reply stays"
    assert doc["fields"]["stderr (live tail)"].startswith("\n[1 line")
    assert not Masks([]) and Masks([]).body('{"a": 1}') == '{"a": 1}'
    with pytest.raises(ValueError, match="not a regular expression"):
        check(["/(unclosed/"])
    assert check([" x ", "", "/ok/"]) == ["x", "/ok/"]


def test_forgotten_endpoints_and_hosts_leave_until_used_again(tmp_path):
    now = time.time()
    rows = [("bob", "x", now - 100, "agent", "codex", "codex · old", True, 1.0, 0, 0, 0.0, ""),
            ("bob", "x", now - 50, "model", "qwen", "10.0.0.9 · qwen", True, 1.0, 0, 0, 0.0, "")]
    assert {e["key"] for e in endpoints(rows, now - 1000)} == {"agent|codex · old", "model|10.0.0.9 · qwen"}
    assert [e["key"] for e in endpoints(rows, now - 1000, {"agent|codex · old": now - 10})] == ["model|10.0.0.9 · qwen"]
    assert [e["key"] for e in endpoints(rows, now - 1000, {"agent|codex · old": now - 200})] != ["model|10.0.0.9 · qwen"], "used after: back"
    refusals = tmp_path / "refusals.jsonl"
    refusals.write_text("\n".join(json.dumps(e) for e in [{"t": now - 100, "host": "a.example", "port": 443, "app": "x"},
                                                          {"t": now - 5, "host": "b.example", "port": 443, "app": "x"}]))
    assert [n["key"] for n in network(str(refusals), now - 1000, {"a.example:443": now - 10})] == ["b.example:443"]


def test_the_routes_mask_turns_and_forget_rows(tmp_path):
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    c = TestClient(create_app(tmp_path / "data", sandbox=False))
    assert c.post("/api/login", json={"name": "ada", "password": "correct horse battery"}, headers=H).status_code == 200
    assert c.put("/api/admin/masks", json={"masks": ["/(bad/"]}, headers=H).status_code == 400
    assert c.put("/api/admin/masks", json={"masks": ["stale arg0", ""]}, headers=H).json() == {"masks": ["stale arg0"]}
    assert c.get("/api/admin/masks").json() == {"masks": ["stale arg0"]}
    assert c.post("/api/admin/insights/forget", json={"kind": "endpoint", "key": "agent|codex · old"}, headers=H).json() == {"ok": True}
    assert c.post("/api/admin/insights/forget", json={"kind": "disk", "key": "x"}, headers=H).status_code == 400
    assert "agent|codex · old" in store.server_get("insights_forgot")["endpoint"]
    bob = TestClient(create_app(tmp_path / "data", sandbox=False))
    bob.post("/api/login", json={"name": "bob", "password": "another long secret"}, headers=H)
    assert bob.put("/api/admin/masks", json={"masks": []}, headers=H).status_code == 403
