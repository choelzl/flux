"""D1170: agents and Flux's model are apart. Flux's own model (FLUX_REMOTE_*, FLUX_LLM_*, its Flux tab) is
Flux's alone: a coding agent's turn, its configuration, its prices, its model list and its readiness take
nothing from it -- its own settings, else its own configuration file and login. A server whose OpenCode ran
on Flux's model has those settings copied, once, into OpenCode's own. Synthetic values; nothing contacted."""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from flux_web import create_app
from flux_web.runs import run_env
from flux_web.store import Store

H = {"X-Flux": "1"}
FLUX_MODEL = {"FLUX_REMOTE_BASE_URL": "https://flux-model.invalid/v1", "FLUX_REMOTE_MODEL": "flux-only-model",
              "FLUX_REMOTE_API_KEY": "synthetic-flux-key", "FLUX_REMOTE_API_KEY_FILE": "/nowhere/flux.key",
              "FLUX_LLM_REMOTE": "1", "FLUX_LLM_MODEL": "flux-local", "FLUX_REMOTE_PRICE_IN": "9", "OPENROUTER_API_KEY": "synthetic-or-key"}
KINDS = ("opencode", "claude", "codex")


@pytest.fixture()
def clean(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("FLUX_REMOTE_", "FLUX_LLM_", "OPENROUTER_", "OPENAI_", "ANTHROPIC_", "OPENCODE_", "CODEX_", "CLAUDE_CODE_",
                         "FLUX_OPENCODE_", "FLUX_CLAUDE_", "FLUX_CODEX_", "FLUX_AGENT")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    from web_agents import install

    install(monkeypatch, tmp_path)
    return tmp_path


def _client(app, name, password):
    c = TestClient(app)
    assert c.post("/api/login", json={"name": name, "password": password}, headers=H).status_code == 200
    return c


def _nothing_of_flux(env: dict[str, str]) -> None:
    leaked = {k for k in env if k.startswith(("FLUX_REMOTE_", "FLUX_LLM_")) or k == "OPENROUTER_API_KEY"}
    assert not leaked, leaked
    assert not any(v in json.dumps(env) for v in ("synthetic-flux-key", "flux-only-model", "flux-model.invalid", "synthetic-or-key"))


@pytest.mark.parametrize("kind", KINDS)
def test_an_agents_turn_gets_nothing_of_fluxs_model(kind):
    """Each kind's turn environment and resolved configuration: none of Flux's model's variables, no
    provider, model or key made of them."""
    from flux_loop.agent import agent_config

    env, eff = agent_config(kind, kind, {"PATH": "/usr/bin", **FLUX_MODEL})
    _nothing_of_flux(env)
    assert eff.fields == {} and not eff.args and "OPENCODE_CONFIG_CONTENT" not in env and "FLUX_AGENT_API_KEY" not in env
    assert env["FLUX_AGENT_TURN"] == kind, "what it runs (flux task check) asks no model of Flux's"
    # its own settings still reach it
    own = {"FLUX_OPENCODE_BASE_URL": "https://oc.invalid/v1", "FLUX_OPENCODE_MODEL": "oc-model", "FLUX_OPENCODE_API_KEY": "synthetic-oc",
           "FLUX_CLAUDE_API_KEY": "synthetic-claude", "FLUX_CODEX_API_KEY": "synthetic-codex"}
    env, eff = agent_config(kind, kind, {"PATH": "/usr/bin", **FLUX_MODEL, f"FLUX_{kind.upper()}_ENV": json.dumps(own)})
    _nothing_of_flux(env)
    assert eff.fields["key"]["value"] == {"opencode": "synthetic-oc", "claude": "synthetic-claude", "codex": "synthetic-codex"}[kind]


def test_opencodes_own_key_reaches_its_own_configuration_file():
    """Without an endpoint and model of its own, OpenCode's key (its tab, FLUX_OPENCODE_API_KEY) is its own
    configuration's to read, `{env:FLUX_AGENT_API_KEY}`; no provider is made."""
    from flux_loop.agent import agent_config

    env, eff = agent_config("opencode", "opencode", {**FLUX_MODEL, "FLUX_OPENCODE_API_KEY": "synthetic-oc"})
    assert env["FLUX_AGENT_API_KEY"] == "synthetic-oc" and "OPENCODE_CONFIG_CONTENT" not in env and not eff.unused
    _nothing_of_flux(env)


def test_a_web_run_hands_no_agent_fluxs_model(clean):
    """The server's and a user's Flux model set, no agent's: every agent's own set (FLUX_<NAME>_ENV), its
    prices and its turn's environment hold none of it; Flux's own model still reaches the run itself."""
    store = Store(clean / "data")
    store.add_user("ada", "correct horse battery", "admin")
    bob = store.add_user("bob", "another long secret")
    for k in ("FLUX_REMOTE_BASE_URL", "FLUX_REMOTE_MODEL", "FLUX_REMOTE_API_KEY", "FLUX_REMOTE_PRICE_IN"):
        store.set_server_setting(k, FLUX_MODEL[k])
    store.set_setting(bob, "FLUX_REMOTE_API_KEY", "synthetic-bob-flux-key")
    env = run_env(store, bob)
    assert env["FLUX_REMOTE_BASE_URL"] == FLUX_MODEL["FLUX_REMOTE_BASE_URL"], "Flux's model is the run's"
    from flux_loop.agent import agent_config

    for kind in KINDS:
        own = json.loads(env.get(f"FLUX_{kind.upper()}_ENV") or "{}")
        assert not any(k.startswith("FLUX_REMOTE_") for k in own) and "OPENCODE_CONFIG_CONTENT" not in own and "FLUX_AGENT_API_KEY" not in own
        assert f"FLUX_{kind.upper()}_PRICE_IN" not in env
        turn, _eff = agent_config(kind, kind, env)
        _nothing_of_flux(turn)
        assert "synthetic-bob-flux-key" not in json.dumps(turn)


def test_the_model_list_of_opencode_without_its_own_endpoint_is_none(clean):
    """D1161's From endpoint for OpenCode lists OpenCode's endpoint alone: Flux's model set is not it."""
    app = create_app(clean / "data", sandbox=False)
    store = Store(clean / "data")
    store.add_user("ada", "correct horse battery", "admin")
    store.add_user("bob", "another long secret")
    ada, bob = _client(app, "ada", "correct horse battery"), _client(app, "bob", "another long secret")
    assert ada.put("/api/admin/settings", json={"values": {k: FLUX_MODEL[k] for k in ("FLUX_REMOTE_BASE_URL", "FLUX_REMOTE_MODEL",
                                                                                        "FLUX_REMOTE_API_KEY")}}, headers=H).status_code == 200
    assert ada.get("/api/admin/settings/models?group=opencode", headers=H).json() == {"models": [], "error": "No endpoint set"}
    assert bob.get("/api/settings/models?group=opencode", headers=H).json() == {"models": [], "error": "No endpoint set"}
    bob.put("/api/settings", json={"values": {"FLUX_REMOTE_BASE_URL": "https://bobs-flux.invalid/v1"}}, headers=H)
    assert bob.get("/api/settings/models?group=opencode", headers=H).json() == {"models": [], "error": "No endpoint set"}


def test_the_readiness_card_says_not_configured_without_its_own(clean):
    """Account › Agents: OpenCode with Flux's model set and nothing of its own is not configured -- not
    connected through Flux's model; its own configuration file still counts (D1167), with its key."""
    app = create_app(clean / "data", sandbox=False)
    store = Store(clean / "data")
    store.add_user("ian", "another long secret", "admin")
    for k in ("FLUX_REMOTE_BASE_URL", "FLUX_REMOTE_MODEL", "FLUX_REMOTE_API_KEY"):
        store.set_server_setting(k, FLUX_MODEL[k])
    c = _client(app, "ian", "another long secret")
    card = lambda: next(a for a in c.get("/api/logins").json()["agents"] if a["id"] == "opencode")  # noqa: E731
    got = card()
    assert got["connection"]["mechanism"] == "none" and got["fields"] == {}, got
    assert got["connection"]["said"] == "no key, provider configuration or login of its own"
    from pathlib import Path

    import flux_web

    js = (Path(flux_web.__file__).parent / "static" / "admin" / "Agents.js").read_text()
    assert 'none: "not configured"' in js
    conf = store.home_of(store.user(name="ian")) / ".config/opencode"
    conf.mkdir(parents=True)
    (conf / "opencode.json").write_text(json.dumps({"provider": {"localai": {"options": {"apiKey": "{env:FLUX_AGENT_API_KEY}"}}},
                                                    "model": "localai/qwen"}))
    store.set_server_setting("FLUX_OPENCODE_API_KEY", "synthetic-oc")
    got = card()["connection"]
    assert got["mechanism"] == "provider" and got["said"] == "a provider in ~/.config/opencode/opencode.json with a key (FLUX_AGENT_API_KEY)", got
    assert card()["fields"]["key"]["source"] == "from server", "said in lower case, as every page says it (review of Oct 17)"


def _raw(store: Store) -> dict:
    import sqlite3

    con = sqlite3.connect(store.path)
    return {"server": dict(con.execute("SELECT key, value FROM server WHERE key LIKE 'setting:%' OR key LIKE 'migrated:%'")),
            "settings": sorted(con.execute("SELECT user_id, key, value FROM settings"))}


def test_opencode_on_fluxs_model_keeps_its_model_once_moved_to_its_own_settings(clean, monkeypatch):
    """The migration: where OpenCode ran on Flux's model (Flux's endpoint and model set, OpenCode's endpoint
    nowhere), Flux's model's settings are copied into OpenCode's own, the server's and a user's own, the
    key still encrypted; once -- a later change to Flux's model is not OpenCode's -- and never over
    OpenCode's own."""
    import sqlite3

    store = Store(clean / "data")
    store.add_user("ada", "correct horse battery", "admin")
    bob = store.add_user("bob", "another long secret")
    cy = store.add_user("cy", "a third long secret")
    eve = store.add_user("eve", "eve has a long secret", "external")
    for k in ("FLUX_REMOTE_BASE_URL", "FLUX_REMOTE_MODEL", "FLUX_REMOTE_API_KEY", "FLUX_REMOTE_PRICE_IN"):
        store.set_server_setting(k, FLUX_MODEL[k])
    store.set_setting(bob, "FLUX_REMOTE_BASE_URL", "https://bobs.invalid/v1")         # bob's OpenCode ran on his own
    store.set_setting(bob, "FLUX_REMOTE_MODEL", "bobs-model")
    store.set_setting(bob, "FLUX_REMOTE_API_KEY", "synthetic-bob-key")
    store.set_setting(cy, "FLUX_REMOTE_API_KEY", "synthetic-cy-key")                  # cy's: the server's, on his key
    store.set_setting(cy, "FLUX_OPENCODE_MODEL", "cys-own-oc")                        # ... and his own OpenCode model
    store.set_setting(eve, "FLUX_REMOTE_BASE_URL", "https://eves.invalid/v1")         # eve's own endpoint without a model:
    with sqlite3.connect(store.path) as con:                                          # never a provider; nothing to keep
        con.execute("DELETE FROM server WHERE key = 'migrated:opencode-model'")
    before = _raw(store)
    store = Store(clean / "data")                                                     # the server starts
    srv = store.server_settings(reveal=True)
    assert (srv["FLUX_OPENCODE_BASE_URL"], srv["FLUX_OPENCODE_MODEL"], srv["FLUX_OPENCODE_API_KEY"], srv["FLUX_OPENCODE_PRICE_IN"]) == \
        (FLUX_MODEL["FLUX_REMOTE_BASE_URL"], "flux-only-model", "synthetic-flux-key", "9")
    assert store.server_settings()["FLUX_OPENCODE_API_KEY"] == "set"
    assert "synthetic-flux-key" not in json.dumps(_raw(store)), "the key copied as stored, encrypted"
    b = store.settings(store.user(name="bob"), reveal=True)
    assert (b["FLUX_OPENCODE_BASE_URL"], b["FLUX_OPENCODE_MODEL"], b["FLUX_OPENCODE_API_KEY"]) == ("https://bobs.invalid/v1", "bobs-model", "synthetic-bob-key")
    c = store.settings(store.user(name="cy"), reveal=True)
    assert c["FLUX_OPENCODE_API_KEY"] == "synthetic-cy-key" and c["FLUX_OPENCODE_MODEL"] == "cys-own-oc", "his own kept"
    assert not any(k.startswith("FLUX_OPENCODE_") for k in store.settings(store.user(name="eve")))
    assert not any(k.startswith("FLUX_OPENCODE_") for k in store.settings(store.user(name="ada"))), "nothing of her own to copy"
    # a run of bob's: OpenCode on what it ran on before, now as its own
    oc = json.loads(json.loads(run_env(store, store.user(name="bob"))["FLUX_OPENCODE_ENV"])["OPENCODE_CONFIG_CONTENT"])
    assert oc["model"] == "flux/bobs-model" and oc["provider"]["flux"]["options"]["baseURL"] == "https://bobs.invalid/v1"
    # once: Flux's model changed after is Flux's alone; a restart copies nothing again
    after = _raw(store)
    store.set_server_setting("FLUX_REMOTE_MODEL", "flux-next")
    store.set_server_setting("FLUX_OPENCODE_MODEL", None)
    Store(clean / "data")
    assert "FLUX_OPENCODE_MODEL" not in store.server_settings() and after != before


def test_the_migration_leaves_an_opencode_with_its_own_endpoint(clean, monkeypatch):
    """OpenCode's endpoint named by the machine (flux.env) or the server: the fallback never applied, nothing copied."""
    import sqlite3

    store = Store(clean / "data")
    bob = store.add_user("bob", "another long secret")
    for k in ("FLUX_REMOTE_BASE_URL", "FLUX_REMOTE_MODEL", "FLUX_REMOTE_API_KEY"):
        store.set_server_setting(k, FLUX_MODEL[k])
    store.set_setting(bob, "FLUX_REMOTE_BASE_URL", "https://bobs.invalid/v1")
    store.set_setting(bob, "FLUX_REMOTE_MODEL", "bobs-model")
    with sqlite3.connect(store.path) as con:
        con.execute("DELETE FROM server WHERE key = 'migrated:opencode-model'")
    monkeypatch.setenv("FLUX_OPENCODE_BASE_URL", "https://machine-oc.invalid/v1")
    store = Store(clean / "data")
    assert not any(k.startswith("FLUX_OPENCODE_") for k in store.server_settings())
    assert not any(k.startswith("FLUX_OPENCODE_") for k in store.settings(store.user(name="bob")))
