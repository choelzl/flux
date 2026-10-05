"""Past turns priced once, at today's prices (D841). A turn is priced when it is recorded (D835) by the
prices its run started with; turns recorded before a price was set carry none. An admin prices them
here, once: each turn priced gets `priced: retro` and is never priced again, nor is one priced when
it was recorded (`priced: set`). The prices are a run's (D835): the admin's, a user's own only with
an endpoint of their own. A running loop is left alone -- its transcript is being written."""

from __future__ import annotations

import json
import os
from typing import Any

__all__ = ["reprice"]


def _num(v: Any) -> float | None:
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def _table(store: Any, user: Any) -> dict[str, tuple[float | None, float | None]]:
    """Who ran a turn -> (USD per million in, out): "model" for Flux's own, else the agent's name."""
    from .agents import registry, run_prices
    from .store import GROUPS

    server = {} if user.external else store.server_settings(reveal=True)
    mine = store.settings(user, reveal=True)
    keys = GROUPS["model"]["prices"]
    src = mine if mine.get(GROUPS["model"]["endpoint"]) else server
    flux = {k: src[k] for k in keys if src.get(k)}
    out = {"model": (_num(flux.get(keys[0])), _num(flux.get(keys[1])))}
    for a in registry(store).values():
        got = run_prices(a, server, mine, flux)
        pin, pout = a.prices()
        out[a.name] = (_num(got.get(pin)), _num(got.get(pout)))
    return {k: v for k, v in out.items() if v != (None, None)}


def _priced(path: str, table: dict[str, tuple[float | None, float | None]]) -> tuple[int, float]:
    """Price the file's unpriced turns in place; (turns priced, USD they add up to)."""
    with open(path, "rb") as fh:
        lines = fh.read().splitlines()
    n, usd, out = 0, 0.0, []
    for raw in lines:
        try:
            t = json.loads(raw)
        except ValueError:
            out.append(raw)
            continue
        price = table.get(str(t.get("agent") or "") if t.get("kind") == "agent" else "model") if isinstance(t, dict) else None
        notes = t.get("notes") if isinstance(t, dict) and isinstance(t.get("notes"), dict) else {}
        tin = (t.get("tokens_in", notes.get("input_tokens")) if isinstance(t, dict) else None) or 0
        tout = (t.get("tokens_out", notes.get("output_tokens")) if isinstance(t, dict) else None) or 0
        if price is None or t.get("priced") in ("set", "retro") or not (tin or tout):
            out.append(raw)
            continue
        try:
            cost = round((float(tin) * (price[0] or 0) + float(tout) * (price[1] or 0)) / 1e6, 6)
        except (TypeError, ValueError):
            out.append(raw)
            continue
        t["cost_usd"], t["priced"] = cost, "retro"
        out.append(json.dumps(t, ensure_ascii=False, default=str).encode())
        n, usd = n + 1, usd + cost
    if n:
        tmp = f"{path}.pricing"
        with open(tmp, "wb") as fh:
            fh.write(b"\n".join(out) + b"\n")
        os.chmod(tmp, os.stat(path).st_mode & 0o777)
        os.replace(tmp, path)
    return n, usd


def reprice(store: Any, runs: Any) -> dict[str, Any]:
    """Every loop's unpriced turns, priced once at today's prices; running loops skipped, said."""
    from .workspace import Workspace

    turns, usd, loops, skipped = 0, 0.0, 0, []
    for u in store.users():
        table = _table(store, u)
        for a in Workspace(store.data, u.name).apps():
            run = runs.latest(u, a["name"])
            path = runs.turns_path(run)
            if not path or not os.path.exists(path):
                continue
            if runs.live(run):
                skipped.append(f"{u.name}/{a['name']}")
                continue
            if not table:
                continue
            n, cost = _priced(path, table)
            if n:
                turns, usd, loops = turns + n, usd + cost, loops + 1
    return {"turns": turns, "usd": round(usd, 4), "loops": loops, "skipped": skipped}
