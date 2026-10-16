"""D1161: a settings group's model "From endpoint" -- the list its endpoint gives, asked by the server
for the scope (the server's; a user's own endpoint and key, else the server's they inherit; nothing
of the server's for an external user), the key never on the page; the box kept per scope and group."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.store import Store

H = {"X-Flux": "1"}
_MACHINE = ("FLUX_REMOTE_BASE_URL", "FLUX_REMOTE_API_KEY", "FLUX_REMOTE_API_KEY_FILE", "OPENROUTER_API_KEY", "FLUX_REMOTE_MODEL",
            "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_BASE_URL", "OPENAI_API_KEY")


@pytest.fixture()
def fake():
    """An OpenAI-compatible server: /srv/v1/models the server's list, /mine/v1/models a user's (a root
    base: /v1 added), /claude/v1/models Anthropic's, /bad a failure; each request's headers kept."""
    seen: list[tuple[str, dict[str, str]]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, {k.lower(): v for k, v in self.headers.items()}))
            path = self.path.split("?")[0]
            lists = {"/srv/v1/models": {"data": [{"id": "srv-b"}, {"id": "srv-a"}]},
                     "/mine/v1/models": {"data": [{"id": "my-model"}]},
                     "/claude/v1/models": {"data": [{"id": "claude-x", "type": "model"}], "has_more": False},
                     "/empty/v1/models": {"data": []}}
            if path in lists:
                body, code = json.dumps(lists[path]).encode(), 200
            elif path.startswith("/bad"):
                body, code = b'{"error": {"message": "upstream down"}}', 500
            else:
                body, code = b"{}", 404
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture()
def server(tmp_path, monkeypatch):
    from web_agents import install

    for k in _MACHINE:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    install(monkeypatch, tmp_path)
    store = Store(tmp_path / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret", "internal")
    store.add_user("eve", "eve has a long secret", "external")
    return create_app(tmp_path / "data", sandbox=True), store


def _client(app, name, password):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": name, "password": password}, headers=H).status_code == 200
    return c


def test_the_list_comes_from_the_scopes_endpoint_with_its_key_never_said(server, fake):
    app, store = server
    url, seen = fake
    ada, bob, eve = (_client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret"),
                     _client(app, "eve", "eve has a long secret"))
    ada.put("/api/admin/settings", json={"values": {"FLUX_REMOTE_BASE_URL": f"{url}/srv/v1", "FLUX_REMOTE_API_KEY": "server-secret"}}, headers=H)

    r = ada.get("/api/admin/settings/models?group=model", headers=H)
    assert r.status_code == 200 and r.json() == {"models": ["srv-a", "srv-b"], "error": ""}
    assert seen[-1] == ("/srv/v1/models", seen[-1][1]) and seen[-1][1]["authorization"] == "Bearer server-secret"
    assert "server-secret" not in r.text

    # an internal user without an endpoint of their own: the server's, on the server's key
    r = bob.get("/api/settings/models?group=model", headers=H)
    assert r.json()["models"] == ["srv-a", "srv-b"] and "server-secret" not in r.text
    assert seen[-1][1]["authorization"] == "Bearer server-secret"
    assert bob.get("/api/admin/settings/models?group=model", headers=H).status_code == 403

    # their own endpoint (a root: /v1 added) with their own key alone
    bob.put("/api/settings", json={"values": {"FLUX_REMOTE_BASE_URL": f"{url}/mine", "FLUX_REMOTE_API_KEY": "bob-secret"}}, headers=H)
    r = bob.get("/api/settings/models?group=model", headers=H)
    assert r.json() == {"models": ["my-model"], "error": ""} and "bob-secret" not in r.text
    assert seen[-1][0] == "/mine/v1/models" and seen[-1][1]["authorization"] == "Bearer bob-secret"
    # the server's own list is still the server's
    assert ada.get("/api/admin/settings/models?group=model", headers=H).json()["models"] == ["srv-a", "srv-b"]

    # an external user gets nothing of the server's
    assert eve.get("/api/settings/models?group=model", headers=H).json() == {"models": [], "error": "No endpoint set"}

    # a failure said, short; an empty list said
    bob.put("/api/settings", json={"values": {"FLUX_REMOTE_BASE_URL": f"{url}/bad/v1"}}, headers=H)
    r = bob.get("/api/settings/models?group=model", headers=H).json()
    assert r["models"] == [] and r["error"].startswith("Model list failed (HTTP 500") and "upstream down" in r["error"]
    bob.put("/api/settings", json={"values": {"FLUX_REMOTE_BASE_URL": f"{url}/empty/v1"}}, headers=H)
    assert bob.get("/api/settings/models?group=model", headers=H).json() == {"models": [], "error": "Endpoint lists no models"}
    bob.put("/api/settings", json={"values": {"FLUX_REMOTE_BASE_URL": "http://127.0.0.1:9/v1"}}, headers=H)
    assert "No answer from endpoint" in bob.get("/api/settings/models?group=model", headers=H).json()["error"]

    # a group without a model to pick, or none at all
    assert ada.get("/api/admin/settings/models?group=agent", headers=H).status_code == 404
    assert ada.get("/api/admin/settings/models?group=nope", headers=H).status_code == 404


def test_an_anthropic_endpoint_is_asked_as_anthropic(server, fake):
    app, _store = server
    url, seen = fake
    ada = _client(app, "ada", "correct horse battery")
    ada.put("/api/admin/settings", json={"values": {"FLUX_CLAUDE_BASE_URL": f"{url}/claude", "FLUX_CLAUDE_API_KEY": "sk-claude"}}, headers=H)
    r = ada.get("/api/admin/settings/models?group=claude", headers=H)
    assert r.json() == {"models": ["claude-x"], "error": ""} and "sk-claude" not in r.text
    path, headers = seen[-1]
    assert path.startswith("/claude/v1/models") and headers["x-api-key"] == "sk-claude" and "anthropic-version" in headers
    assert "authorization" not in headers


def test_the_sandboxs_allowlist_holds_when_endpoints_are_off_it(server, fake):
    app, store = server
    url, _seen = fake
    ada = _client(app, "ada", "correct horse battery")
    ada.put("/api/admin/settings", json={"values": {"FLUX_REMOTE_BASE_URL": f"{url}/srv/v1"}}, headers=H)
    store.server_set("sandbox", {"network": "allowlist", "allow": ["example.com"], "endpoints": False})
    assert "allowlist" in ada.get("/api/admin/settings/models?group=model", headers=H).json()["error"]
    store.server_set("sandbox", {"network": "allowlist", "allow": ["127.0.0.1"], "endpoints": False})
    assert ada.get("/api/admin/settings/models?group=model", headers=H).json()["models"] == ["srv-a", "srv-b"]


def test_from_endpoint_is_kept_per_scope_and_group(server):
    app, _store = server
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    assert bob.get("/api/settings", headers=H).json()["from_endpoint"] == []
    assert bob.put("/api/settings/from-endpoint", json={"group": "model", "on": True}, headers=H).json() == {"from_endpoint": ["model"]}
    bob.put("/api/settings/from-endpoint", json={"group": "codex", "on": True}, headers=H)
    assert bob.get("/api/settings", headers=H).json()["from_endpoint"] == ["model", "codex"]
    assert ada.get("/api/settings", headers=H).json()["from_endpoint"] == [], "another user's own"
    assert ada.get("/api/admin/settings", headers=H).json()["from_endpoint"] == [], "the server's own"
    ada.put("/api/admin/settings/from-endpoint", json={"group": "claude", "on": True}, headers=H)
    assert ada.get("/api/admin/settings", headers=H).json()["from_endpoint"] == ["claude"]
    bob.put("/api/settings/from-endpoint", json={"group": "model", "on": False}, headers=H)
    assert bob.get("/api/settings", headers=H).json()["from_endpoint"] == ["codex"]
    assert bob.put("/api/settings/from-endpoint", json={"group": "agent", "on": True}, headers=H).status_code == 404
    assert bob.put("/api/admin/settings/from-endpoint", json={"group": "model", "on": True}, headers=H).status_code == 403
    groups = {g["id"]: g.get("model_key") for g in bob.get("/api/settings", headers=H).json()["groups"]}
    assert groups["model"] == "FLUX_REMOTE_MODEL" and groups["claude"] == "FLUX_CLAUDE_MODEL" and groups["agent"] is None
