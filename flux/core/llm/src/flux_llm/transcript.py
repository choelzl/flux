"""Transcripts (D599): every model and coding-agent turn of a run, one JSON line each, in the
run's directory (`turns.jsonl`), read back with `flux log <record>`.

The loop names the file when a run registers (`flux_loop.ops.register`); with no file named,
recording is off. Recording never raises."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

__all__ = ["path", "priced", "record", "set_path"]

_STATE: dict[str, Any] = {"path": None}
_LOCK = threading.Lock()


def set_path(p: str | None) -> None:
    _STATE["path"] = p


def path() -> str | None:
    return _STATE["path"]


def priced(prefix: str, tokens_in: Any, tokens_out: Any) -> dict[str, Any]:
    """A turn's cost from the prices set for who ran it (D835): `<prefix>_PRICE_IN` and `_OUT`, USD
    per million tokens (`FLUX_REMOTE` for Flux's own model, `FLUX_<NAME>` for an agent) --
    {cost_usd, priced: "set"}; {} with neither set, so an agent's own figure stands."""
    def num(name: str) -> float | None:
        try:
            v = float(os.environ.get(f"{prefix}_PRICE_{name}", "").strip())
        except ValueError:
            return None
        return v if v >= 0 else None

    pin, pout = num("IN"), num("OUT")
    if pin is None and pout is None:
        return {}
    try:
        cost = (float(tokens_in or 0) * (pin or 0) + float(tokens_out or 0) * (pout or 0)) / 1e6
    except (TypeError, ValueError):
        return {}
    return {"cost_usd": round(cost, 6), "priced": "set"}


def record(kind: str, **fields: Any) -> None:
    """One turn: `kind` ("model", "agent"), then whatever the turn has -- the prompt, the reply,
    the tool calls, the seconds, the error."""
    p = _STATE["path"]
    if not p:
        return
    line = json.dumps({"ts": time.time(), "kind": kind, **fields}, ensure_ascii=False, default=str)
    try:
        with _LOCK:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            with open(p, "a") as f:
                f.write(line + "\n")
    except OSError:
        pass
