"""What every design-loop node shares (D435): the report skeleton, the optional proposer,
and the one way a CHIA node builds a local-model proposer.

Five `*_dse_loop` nodes each wrote the same result-to-dict skeleton and the same
"a model only when rounds > 0, and never a crash when there is none" construction; two
modules carried a verbatim `_OllamaProposer` class. This is the one copy.
"""

from __future__ import annotations

import os
from typing import Any, Callable

__all__ = ["TextProposer", "loop_report", "ollama_proposer", "optional_proposer", "text_proposer"]


def loop_report(result: Any, payload: Callable[[Any], Any] | None = None, *,
                refused_key: str = "who", frontier_from: str | None = "frontier",
                extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """The JSON-safe report every loop node returns, from the study result's own fields:
    `decision` (through `payload`), `decided_by`, the frontier (`frontier_from`, through
    `payload`), `refused` as `{refused_key, "why"}` rows, `lessons`, `not_established`,
    `met_requirement` when the result has one, `provenance` -- then `extra`, which wins."""
    out: dict[str, Any] = {}
    decision = getattr(result, "decision", None)
    out["decision"] = (payload(decision) if (decision is not None and payload is not None)
                       else decision)
    if hasattr(result, "decided_by"):
        out["decided_by"] = result.decided_by
    if frontier_from and hasattr(result, frontier_from):
        front = getattr(result, frontier_from) or []
        out[frontier_from] = [payload(x) if payload is not None else x for x in front]
    out["refused"] = [{refused_key: label, "why": why} for label, why in getattr(result, "refused", [])]
    out["lessons"] = list(getattr(result, "lessons", []))
    out["not_established"] = list(getattr(result, "not_established", []))
    if hasattr(result, "met_requirement"):
        out["met_requirement"] = result.met_requirement
    out["provenance"] = dict(getattr(result, "provenance", {}) or {})
    out.update(extra or {})
    return out


def optional_proposer(rounds: int, make: Callable[[], Any], *,
                      log: Callable[[str], None] | None = None) -> Any | None:
    """A proposer only when the study has model rounds to spend, and None -- never a
    crash -- when the model cannot be reached: the study then reports that the proposer
    did not run and every model-free path still runs."""
    if rounds <= 0:
        return None
    try:
        return make()
    except Exception as exc:  # noqa: BLE001
        if log is not None:
            log(f"no model proposer ({type(exc).__name__}: {exc!s:.80}); running without one")
        return None


def ollama_proposer(model: str | None = None, **kwargs: Any) -> Any:
    """The one proposer (`flux_llm.OpenAIChatProposer`, D508) for `model`, reasoning off
    unless `FLUX_LLM_THINK` asks for it (the restore-reasoning A/B arm, D376); where it
    points -- the local Ollama's `/v1`, or the hosted server when `FLUX_LLM_REMOTE` asks --
    is the proposer's own policy. The name is the one the live tests import (D435)."""
    from flux_llm import OpenAIChatProposer

    think = os.environ.get("FLUX_LLM_THINK", "").lower() in ("1", "true", "yes")
    return OpenAIChatProposer(model, think=think, **kwargs)


class TextProposer:
    """The `propose(prompt) -> str` face the architecture generator node (`generate_architecture`,
    `flux_generation`) still reads, over the one proposer: the adapter lives HERE, at the edge
    of that world, and nowhere in `flux_llm` (D508). The pre-one-loop strategies that read it
    too went with D521."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.model = getattr(inner, "model", "?")

    def propose(self, prompt: str) -> str:
        return self._inner.propose(prompt).text


def text_proposer(model: str | None = None, **kwargs: Any) -> TextProposer:
    return TextProposer(ollama_proposer(model, **kwargs))
