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

OpenRouter speaks the OpenAI protocol, so this is the `openai` client pointed at a different base
URL. Nothing here is specific to that vendor beyond the default.
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


def remote_enabled() -> bool:
    """OPT-IN, and by an explicit switch rather than by a key happening to be in the environment.

    A key can be present for unrelated reasons; sending a study's measurements off the machine is
    not something to start doing because of that.
    """
    return os.environ.get("FLUX_LLM_REMOTE", "").strip().lower() in {"1", "true", "yes", "on"}


def remote_proposer(model: str | None = None, *, timeout_s: float = 120.0,
                    announce=print):
    """A `prompt -> text` callable against a hosted OpenAI-compatible endpoint.

    Raises `RemoteProposerUnavailable` when it cannot be built, so a caller can fall back to the
    local model instead of the run dying on a missing key.
    """
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("FLUX_REMOTE_API_KEY")
    if not key:
        raise RemoteProposerUnavailable(
            "no OPENROUTER_API_KEY in the environment; the remote proposer needs one")
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - the dev shell carries it
        raise RemoteProposerUnavailable(f"the openai client is not importable: {exc}") from exc

    chosen = model or remote_model()
    base = os.environ.get("FLUX_REMOTE_BASE_URL", _BASE_URL)
    client = OpenAI(base_url=base, api_key=key, timeout=timeout_s)

    def propose(prompt: str) -> str:
        # ANNOUNCED once per process, on the first prompt that actually goes out — not at
        # construction, because building a proposer and never using it sends nothing.
        global _ANNOUNCED
        if not _ANNOUNCED:
            _ANNOUNCED = True
            announce(f"\n!! SENDING PROMPTS OFF THIS MACHINE to {base} ({chosen}).\n"
                     "   The prompt carries this study's problem and its measured results.\n"
                     "   Unset FLUX_LLM_REMOTE to keep everything local.\n")
        try:
            reply = client.chat.completions.create(
                model=chosen, messages=[{"role": "user", "content": prompt}])
        except Exception as exc:  # noqa: BLE001 — a refused call is a fallback, not a crash
            raise RemoteProposerUnavailable(f"{type(exc).__name__}: {str(exc)[:160]}") from exc
        choices = getattr(reply, "choices", None)
        if not choices:
            raise RemoteProposerUnavailable("the endpoint returned no choices")
        return (choices[0].message.content or "").strip()

    return propose
