"""D922: one resolver maps Flux's agent aliases (FLUX_<NAME>_BASE_URL / _API_KEY / _MODEL /
_OAUTH_TOKEN) to what each kind reads -- whatever set them: a web setting, flux.env, the shell, the
server's inherited environment -- checked at the process boundary (the agent's env and argv), with
synthetic values only. A8: an endpoint and its credential are resolved together."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest

from flux_loop.agent import agent_spec, run_turn
from flux_loop.agent_env import config_warnings, resolve

_CLEAN = ("ANTHROPIC_", "OPENAI_", "CLAUDE_CODE_", "OPENCODE_", "FLUX_CLAUDE_", "FLUX_CODEX_", "FLUX_OPENCODE_", "FLUX_CORP_",
          "FLUX_AGENTS", "FLUX_SHARED_VARS", "FLUX_REMOTE_", "FLUX_LLM_REMOTE", "FLUX_DEFAULT_AGENT")


@pytest.fixture()
def agents(tmp_path, monkeypatch):
    """A stand-in program per kind and `corp`, a Claude-kind agent of a server's own: each writes its
    env and argv where the test reads them."""
    for k in list(os.environ):
        if k.startswith(_CLEAN):
            monkeypatch.delenv(k, raising=False)
    progs = {}
    for name in ("claude", "codex", "opencode", "corp"):
        p = tmp_path / "bin" / name
        p.parent.mkdir(exist_ok=True)
        p.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                     f"json.dump({{'env': dict(os.environ), 'argv': sys.argv}}, open({str(tmp_path / name)!r} + ('.version' if '--version' in sys.argv else '') + '.json', 'w'))\n")
        p.chmod(0o755)
        progs[name] = p
        monkeypatch.setenv(f"FLUX_{name.upper()}_BIN", str(p))
    monkeypatch.setenv("FLUX_AGENTS", json.dumps({"corp": "claude"}))

    def capture(name: str) -> dict:
        spec = agent_spec(name)
        run_turn(spec, spec.argv, {"prompt": "p", "name": "n", "workdir": str(tmp_path)}, workdir=tmp_path)
        return json.loads((tmp_path / f"{name}.json").read_text())

    return capture


ALIASES = {"FLUX_CLAUDE_BASE_URL": "https://claude.invalid/v1", "FLUX_CLAUDE_API_KEY": "synthetic-claude-key",
           "FLUX_CLAUDE_MODEL": "claude-synth", "FLUX_CLAUDE_OAUTH_TOKEN": "synthetic-oauth",
           "FLUX_CODEX_BASE_URL": "https://codex.invalid/v1", "FLUX_CODEX_API_KEY": "synthetic-codex-key", "FLUX_CODEX_MODEL": "gpt-synth",
           "FLUX_OPENCODE_BASE_URL": "https://oc.invalid/v1", "FLUX_OPENCODE_API_KEY": "synthetic-oc-key", "FLUX_OPENCODE_MODEL": "oc-synth",
           "FLUX_CORP_BASE_URL": "https://corp.invalid/v1", "FLUX_CORP_API_KEY": "synthetic-corp-key", "FLUX_CORP_MODEL": "corp-synth"}


def _check_all(capture) -> None:
    cl = capture("claude")
    assert cl["env"]["ANTHROPIC_BASE_URL"] == "https://claude.invalid/v1" and cl["env"]["ANTHROPIC_API_KEY"] == "synthetic-claude-key"
    assert cl["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "synthetic-oauth" and cl["argv"][cl["argv"].index("--model") + 1] == "claude-synth"
    assert not [k for k in cl["env"] if k.startswith(("FLUX_CLAUDE_BASE", "FLUX_CLAUDE_API", "FLUX_CLAUDE_MODEL", "FLUX_CORP_API"))], \
        "the aliases are translated, not handed on -- and another agent's are not its business"
    cx = capture("codex")
    assert cx["env"]["OPENAI_BASE_URL"] == "https://codex.invalid/v1" and cx["env"]["OPENAI_API_KEY"] == "synthetic-codex-key"
    assert cx["argv"][cx["argv"].index("--model") + 1] == "gpt-synth" and cx["argv"][-1] == "-", "the model before the stdin prompt"
    oc = capture("opencode")
    cfg = json.loads(oc["env"]["OPENCODE_CONFIG_CONTENT"])
    assert cfg["model"] == "flux/oc-synth" and cfg["provider"]["flux"]["options"]["baseURL"] == "https://oc.invalid/v1"
    assert cfg["provider"]["flux"]["options"]["apiKey"] == "{env:FLUX_AGENT_API_KEY}" and oc["env"]["FLUX_AGENT_API_KEY"] == "synthetic-oc-key"
    assert "permission" in cfg, "its kind's denials kept"
    co = capture("corp")
    assert co["env"]["ANTHROPIC_BASE_URL"] == "https://corp.invalid/v1" and co["env"]["ANTHROPIC_API_KEY"] == "synthetic-corp-key"
    assert co["argv"][co["argv"].index("--model") + 1] == "corp-synth" and "CLAUDE_CODE_OAUTH_TOKEN" not in co["env"], \
        "a named agent: its own aliases, its kind's names -- not the claude agent's"


def test_the_aliases_from_the_shell_reach_each_kind_and_a_named_agent(agents, monkeypatch):
    for k, v in ALIASES.items():
        monkeypatch.setenv(k, v)
    _check_all(agents)


def test_the_aliases_from_flux_env_too(agents, tmp_path, monkeypatch, capsys):
    from flux_llm import openai_compat

    monkeypatch.setattr(openai_compat, "LOADED", [])

    cfg = tmp_path / "flux.env"
    cfg.write_text("".join(f"{k}={v}\n" for k, v in ALIASES.items()) + "ANTHROPIC_AUTH_TOKEN=synthetic-native\nPATH=/nope\n"
                   "FLUX_CODEX_OAUTH_TOKEN=x\nFLUX_AGENT_BASE_URL=https://x.invalid\n")
    # Snapshot before loading: recording variables afterwards would restore the loaded values.
    with patch.dict(os.environ):
        got = openai_compat.load_user_config(cfg)
        said = capsys.readouterr().err
        assert "ANTHROPIC_AUTH_TOKEN" in got and "PATH" not in got, "an agent's own name loads; anything else is said"
        assert "PATH: not a Flux setting" in said and "FLUX_CODEX_OAUTH_TOKEN: a codex agent takes no login token" in said
        assert "FLUX_AGENT_BASE_URL: agent is not an agent here" in said
        _check_all(agents)
        from flux_loop.agent import agent_config

        _env, eff = agent_config("claude", "claude", dict(os.environ))
        assert eff.fields["endpoint"]["source"] == "flux.env", "where it came from is said"


def test_the_aliases_inherited_by_the_server_and_its_settings_reach_a_web_run(agents, tmp_path, monkeypatch):
    """The same names inherited by `flux serve`, and saved as web settings: one translation."""
    from flux_web.runs import run_env
    from flux_web.store import Store

    from web_agents import install

    install(monkeypatch, tmp_path, ("opencode", "codex"))           # offered where installed: the stand-ins above for the rest
    monkeypatch.setenv("FLUX_CLAUDE_BIN", str(tmp_path / "bin" / "claude"))
    monkeypatch.setenv("FLUX_CODEX_BIN", str(tmp_path / "bin" / "codex"))
    monkeypatch.setenv("FLUX_OPENCODE_BIN", str(tmp_path / "bin" / "opencode"))
    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    store.server_set("agents", {"corp": {"kind": "claude", "bin": str(tmp_path / "bin" / "corp")}})
    bob = store.user(name="bob")
    for k, v in ALIASES.items():                                    # inherited: the server's own environment
        if "CLAUDE" in k or "CODEX" in k:
            monkeypatch.setenv(k, v)
    for k, v in ALIASES.items():                                    # saved: the server's settings
        if "OPENCODE" in k or "CORP" in k:
            store.set_server_setting(k, v)
    store.set_setting(bob, "FLUX_CLAUDE_OAUTH_TOKEN", "synthetic-oauth")   # D748: a login token is the user's
    monkeypatch.delenv("FLUX_CLAUDE_OAUTH_TOKEN")
    env = run_env(store, bob)
    assert not [k for k in env if k in ALIASES], "resolved into each agent's own set"
    for k in list(os.environ):
        if k.startswith(_CLEAN) and k != "FLUX_AGENTS":
            monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    _check_all(agents)


def test_a_personal_endpoint_never_pairs_with_an_inherited_machine_key(agents, tmp_path, monkeypatch):
    """A8: the user's own endpoint, the machine's native key: the key is not used with it -- nor the
    server's stored one; with the machine's endpoint, the machine's key is."""
    from flux_web.runs import run_env
    from flux_web.store import Store

    store = Store(tmp_path / "data")
    store.add_user("bob", "another long secret")
    bob = store.user(name="bob")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-machine-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://machine.invalid")
    store.set_server_setting("FLUX_CLAUDE_API_KEY", "synthetic-server-key")
    env = run_env(store, bob)
    pack = json.loads(env["FLUX_CLAUDE_ENV"])
    assert pack["ANTHROPIC_API_KEY"] == "synthetic-server-key" and pack["ANTHROPIC_BASE_URL"] == "https://machine.invalid"
    store.set_setting(bob, "FLUX_CLAUDE_BASE_URL", "https://personal.invalid/v1")
    env = run_env(store, bob)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    got = agents("claude")["env"]
    assert got["ANTHROPIC_BASE_URL"] == "https://personal.invalid/v1"
    assert "ANTHROPIC_API_KEY" not in got, "neither the machine's native key nor the server's stored one"


def test_one_scope_with_two_spellings_is_a_conflict_and_the_loop_wins():
    """Precedence by scope; in one scope an alias and its native name that differ are said, the alias used."""
    eff = resolve("claude", "claude", [("machine", {"ANTHROPIC_API_KEY": "a"}),
                                       ("yours", {"FLUX_CLAUDE_API_KEY": "b", "ANTHROPIC_API_KEY": "c"}),
                                       ("this loop's", {"ANTHROPIC_API_KEY": "synthetic-loop-key"})])
    assert eff.env["ANTHROPIC_API_KEY"] == "synthetic-loop-key" and eff.fields["key"]["source"] == "this loop's"
    assert eff.conflicts == ["yours: FLUX_CLAUDE_API_KEY and ANTHROPIC_API_KEY differ; FLUX_CLAUDE_API_KEY is used"]
    assert "synthetic-loop-key" not in json.dumps(eff.public()), "the effective view: sources, never a secret"
    half = resolve("opencode", "opencode", [("yours", {"FLUX_OPENCODE_BASE_URL": "https://x.invalid", "FLUX_OPENCODE_API_KEY": "k"})])
    assert "OPENCODE_CONFIG_CONTENT" not in half.env and "needs an endpoint and a model" in half.unused[0]
    assert config_warnings({"FLUX_NGA_MODEL": "m", "FLUX_OPENCODE_MODEL": "m"}, {"opencode": "opencode", "claude": "claude"}) == [
        "FLUX_NGA_MODEL: nga is not an agent here (agents: opencode, claude) -- not used",
        "FLUX_OPENCODE_MODEL: OpenCode's provider needs both FLUX_OPENCODE_BASE_URL and FLUX_OPENCODE_MODEL -- not used"]


def test_a_documents_args_without_a_model_keep_the_settings_model(agents, monkeypatch):
    """`args: []` no longer drops the model setting; an argument naming a model wins."""
    monkeypatch.setenv("FLUX_CODEX_MODEL", "gpt-synth")
    argv = agent_spec({"preset": "codex", "args": []}).argv
    assert argv[argv.index("--model") + 1] == "gpt-synth"
    argv = agent_spec({"preset": "codex", "args": ["--model", "mine"]}).argv
    assert argv.count("--model") == 1 and argv[argv.index("--model") + 1] == "mine"
