"""The hosted proposer, and the conditions under which anything leaves the machine (D337).

Every other proposer here runs locally and `local_proposer`'s first line says so. This one sends
the prompt — which in this application carries the problem being solved and the measured area,
frequency and throughput of everything tried — to a third party. The tests that matter are
therefore about WHEN it is used, not about what a model says.
"""

from __future__ import annotations


import pytest
from flux_llm import RemoteProposerUnavailable, remote_enabled, remote_model, remote_proposer



def test_nothing_is_remote_without_an_explicit_switch(monkeypatch):
    monkeypatch.delenv("FLUX_LLM_REMOTE", raising=False)
    assert remote_enabled() is False


def test_a_key_alone_does_not_turn_it_on(monkeypatch):
    """A key can be in the environment for unrelated reasons. Sending a study's measurements off
    the machine is not something to start doing because of that."""
    monkeypatch.delenv("FLUX_LLM_REMOTE", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-whatever")
    assert remote_enabled() is False


@pytest.mark.parametrize("value,expected",
                         [("1", True), ("true", True), ("YES", True), ("on", True),
                          ("0", False), ("false", False), ("", False), ("maybe", False)],
                         ids=lambda v: str(v))
def test_the_switch_is_read_strictly(monkeypatch, value, expected):
    monkeypatch.setenv("FLUX_LLM_REMOTE", value)
    assert remote_enabled() is expected


def test_no_key_is_refused_rather_than_guessed(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("FLUX_REMOTE_API_KEY", raising=False)
    with pytest.raises(RemoteProposerUnavailable, match="OPENROUTER_API_KEY"):
        remote_proposer()


def test_the_default_model_is_a_free_one():
    """The point of the exercise was speed at no cost; a default that bills silently is a
    different feature."""
    monkeypatch_free = remote_model()
    assert monkeypatch_free.endswith(":free"), monkeypatch_free


def test_the_model_is_overridable(monkeypatch):
    monkeypatch.setenv("FLUX_REMOTE_MODEL", "some/other-model")
    assert remote_model() == "some/other-model"


# -- how the demo chooses ---------------------------------------------------------------------


def test_the_demo_stays_local_by_default(monkeypatch):
    import flux_interconnect.flow as demo

    monkeypatch.delenv("FLUX_LLM_REMOTE", raising=False)
    calls = []
    import flux_llm.auto as auto
    monkeypatch.setattr(auto, "local_proposer", lambda *a, **k: calls.append(a) or (lambda p: ""))
    demo.a_proposer()
    assert calls, "the local proposer must be the default path"


def test_a_refusing_endpoint_falls_back_to_local_rather_than_ending_the_run(monkeypatch, capsys):
    """LOCAL IS THE FALLBACK, never the reverse. A rate limit or an absent network must not end a
    study the machine can still run."""
    import flux_interconnect.flow as demo

    monkeypatch.setenv("FLUX_LLM_REMOTE", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("FLUX_REMOTE_API_KEY", raising=False)
    used_local = []
    import flux_llm.auto as auto
    monkeypatch.setattr(auto, "local_proposer",
                        lambda *a, **k: used_local.append(True) or (lambda p: ""))
    assert demo.a_proposer() is not None
    assert used_local, "it must fall back, not raise"
    assert "remote model unavailable" in capsys.readouterr().out


def test_going_remote_is_announced_before_the_first_prompt_leaves(monkeypatch):
    """Announced on the first prompt that actually goes out, not at construction: building a
    proposer and never using it sends nothing, and a warning printed then is noise."""
    import flux_llm.openrouter as remote

    monkeypatch.setattr(remote, "_ANNOUNCED", False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    said: list[str] = []

    class _Reply:
        class _Choice:
            class message:  # noqa: N801 - mimicking the client's shape
                content = "ok"
        choices = [_Choice()]

    class _Client:
        def __init__(self, **_kw):
            self.chat = self

        @property
        def completions(self):
            return self

        def create(self, **_kw):
            return _Reply()

    monkeypatch.setattr("openai.OpenAI", _Client)
    propose = remote.remote_proposer(announce=said.append)
    assert not said, "nothing has been sent yet"
    propose("a prompt carrying measurements")
    assert said and "SENDING PROMPTS OFF THIS MACHINE" in said[0]
