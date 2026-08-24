"""A hosted proposer, for when the local model is the bottleneck (docs/decisions.md D337).

WHAT THIS COSTS, stated first because it is the whole trade. Every other proposer in this repo
runs on the machine you are sitting at, and `local_proposer`'s docstring says so in its first
line: nothing leaves. This one sends the prompt to a third party. In this application that prompt
carries the problem being solved, the fabrics already tried and their measured area, frequency and
throughput — a design-space exploration is exactly the kind of thing an organisation may not send
anywhere. So it is opt-in, never a fallback, and it announces itself on first use.

WHY IT EXISTS. On a measured run the local model was 75.7% of the wall clock — five calls, 857
seconds — against 4% for real Yosys, OpenROAD and Verilator combined. The tools stopped being the
bottleneck several fixes ago. A hosted model answers in seconds, and the loop's shape is the same
either way: the model proposes and directs, and every fabric it names is still built, screened and
placed by the same local machinery, which is what makes a wrong answer cost an evaluation rather
than produce a wrong result.

OpenRouter speaks the OpenAI protocol, so this is one request shape pointed at a base URL.
Nothing here is specific to that vendor beyond the default. Since D469 the request itself
is `openai_compat.OpenAIChatProposer`'s (one HTTP implementation, no `openai` client); this
module keeps the POLICY -- the switch, the key, the announcement -- and the `str -> str`
face with its fall-back-to-local error.
"""

from __future__ import annotations

import os

_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "deepseek/deepseek-chat-v3-0324:free"
_ANNOUNCED = False


class RemoteProposerUnavailable(RuntimeError):
    """No key, no client, or the endpoint refused. Callers fall back to local rather than fail."""


def remote_model() -> str:
    return os.environ.get("FLUX_REMOTE_MODEL", _DEFAULT_MODEL)


def remote_base_url() -> str:
    """`FLUX_REMOTE_BASE_URL`, always ending in `/v1`: a LocalAI or llama.cpp server is
    usually named by its root, OpenRouter by its `/api/v1`, and the chat path hangs off
    the same place on both."""
    base = os.environ.get("FLUX_REMOTE_BASE_URL", _BASE_URL).rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def remote_api_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("FLUX_REMOTE_API_KEY") or None


def remote_enabled() -> bool:
    """OPT-IN, and by an explicit switch rather than by a key happening to be in the environment.

    A key can be present for unrelated reasons; sending a study's measurements off the machine is
    not something to start doing because of that.
    """
    return os.environ.get("FLUX_LLM_REMOTE", "").strip().lower() in {"1", "true", "yes", "on"}


def announce_remote(base: str, model: str, announce=print) -> None:
    """ANNOUNCED once per process, on the first prompt that actually goes out -- not at
    construction, because building a proposer and never using it sends nothing."""
    global _ANNOUNCED
    if _ANNOUNCED:
        return
    _ANNOUNCED = True
    announce(f"\n!! SENDING PROMPTS OFF THIS MACHINE to {base} ({model}).\n"
             "   The prompt carries this study's problem and its measured results.\n"
             "   Unset FLUX_LLM_REMOTE to keep everything local.\n")


def remote_proposer(model: str | None = None, *, timeout_s: float = 120.0,
                    announce=print):
    """A `prompt -> text` callable against a hosted OpenAI-compatible endpoint.

    Raises `RemoteProposerUnavailable` when it cannot be built, so a caller can fall back to the
    local model instead of the run dying on a missing key. On the wire this is what it always
    was: no schema, no output cap, nothing said about reasoning (the server's setting stands).
    """
    key = remote_api_key()
    if not key:
        raise RemoteProposerUnavailable(
            "no OPENROUTER_API_KEY in the environment; the remote proposer needs one")
    from .openai_compat import OpenAIChatProposer

    proposer = OpenAIChatProposer(model, num_predict=None, timeout_s=timeout_s, think=True,
                                  api_key=key, announce=announce)

    def propose(prompt: str) -> str:
        try:
            return proposer.propose(prompt)
        except Exception as exc:  # noqa: BLE001 -- a refused call is a fallback, not a crash
            raise RemoteProposerUnavailable(f"{type(exc).__name__}: {str(exc)[:160]}") from exc

    return propose
