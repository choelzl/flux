"""Which model a run talks to (D590): naming a server is the opt-in; a key alone never is;
the default hosted endpoint needs a key, a server you name may not; every run says which."""

from __future__ import annotations

import pytest

from flux_llm import OpenAIChatProposer, describe_model
from flux_llm.openai_compat import remote_enabled

VARS = ("FLUX_LLM_REMOTE", "FLUX_REMOTE_BASE_URL", "FLUX_REMOTE_API_KEY", "FLUX_REMOTE_API_KEY_FILE", "OPENROUTER_API_KEY",
        "FLUX_REMOTE_MODEL")


@pytest.fixture
def env(monkeypatch):
    for v in VARS:
        monkeypatch.delenv(v, raising=False)
    return monkeypatch


def test_naming_the_server_is_the_opt_in_and_a_key_alone_is_not(env):
    assert remote_enabled() is False
    env.setenv("FLUX_REMOTE_API_KEY", "k")
    assert remote_enabled() is False, "a key in the environment never sends anything off the machine"
    env.setenv("FLUX_REMOTE_BASE_URL", "http://llm.lan:8080")
    assert remote_enabled() is True
    env.setenv("FLUX_LLM_REMOTE", "0")
    assert remote_enabled() is False, "the switch forces local"


def test_a_named_server_may_have_no_key_and_the_default_one_needs_one(env):
    env.setenv("FLUX_REMOTE_BASE_URL", "http://llm.lan:8080")
    env.setenv("FLUX_REMOTE_MODEL", "some-model")
    p = OpenAIChatProposer(None)
    assert p.base_url == "http://llm.lan:8080/v1" and p.model == "some-model"
    assert describe_model(p) == "model: some-model at http://llm.lan:8080/v1 (remote)"
    env.delenv("FLUX_REMOTE_BASE_URL")
    env.setenv("FLUX_LLM_REMOTE", "1")
    with pytest.raises(RuntimeError, match="the default hosted server needs one"):
        OpenAIChatProposer(None)


def test_the_local_default_says_so(env):
    p = OpenAIChatProposer("qwen3:4b")
    assert describe_model(p).endswith("(local)") and "qwen3:4b" in describe_model(p)
