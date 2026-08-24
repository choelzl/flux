"""What every design-loop node shares (D435): the report skeleton, the optional proposer,
and the one way a CHIA node builds a local-model proposer.

Five `*_dse_loop` nodes each wrote the same result-to-dict skeleton and the same
"a model only when rounds > 0, and never a crash when there is none" construction; two
modules carried a verbatim `_OllamaProposer` class. This is the one copy.
"""

from __future__ import annotations

import os
from typing import Any, Callable

__all__ = ["loop_report", "ollama_proposer", "optional_proposer"]


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
    """`flux_llm.NativeOllamaProposer` for `model`, reasoning off unless `FLUX_LLM_THINK`
    asks for it (the restore-reasoning A/B arm, D376). The native endpoint, not chia's
    OllamaLLM: the /v1 path cannot turn reasoning off and `/no_think` is ignored by newer
    qwen3 builds, so every proposal bought a hidden trace at CPU speed. Through
    `structured_proposer`, so `FLUX_LLM_REMOTE` moves these nodes to a hosted server with
    the rest of the repo (D469)."""
    from flux_llm import structured_proposer

    think = os.environ.get("FLUX_LLM_THINK", "").lower() in ("1", "true", "yes")
    return structured_proposer(model=model, think=think, **kwargs)
