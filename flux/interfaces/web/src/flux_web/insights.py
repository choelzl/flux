"""What an admin reads at a glance (D766), from what the server keeps already: failed starts with
their own words and agents' failed Tests; turns, tokens and cost by day, user, agent and loop; the
model endpoints and agents as their turns found them -- how many, how many failed, how long; the
hosts the sandboxes refused; and where the disk goes, by user."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any
from urllib.parse import urlsplit

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\[[0-9;]*m")

__all__ = ["disk", "endpoints", "failures", "network", "turns", "usage_by_day"]

DAY = 86400.0
_TURNS: dict[str, tuple[tuple[int, float], list[tuple]]] = {}


def failures(store: Any, runs: Any, since: float) -> dict[str, Any]:
    """Starts that failed since `since`, newest first, each with why (its log's words, D757); the
    agents' Tests that failed (D751), per user."""
    starts = []
    for r in store.runs():
        if r.get("ended") and r["ended"] >= since and r.get("rc") not in (0, None, 130):
            starts.append({"user": r["user"], "app": r["app"], "when": r["ended"], "rc": r["rc"], "why": [w.strip() for w in runs.failure(r)]})
    tests = []
    for u in store.users():
        from .agents import registry

        for agent in registry(store):
            t = store.server_get(f"agent-test:{u.name}:{agent}") or {}
            if t.get("when") and not t.get("ok"):
                bad = next((s for s in t.get("steps") or [] if not s.get("ok")), {})
                tests.append({"user": u.name, "agent": agent, "when": t["when"], "step": bad.get("step", ""), "why": bad.get("said", "")})
    return {"starts": starts[:100], "tests": sorted(tests, key=lambda x: -x["when"])}


def _rows(path: str) -> list[tuple]:
    """A loop's turns, each (ts, kind, who, endpoint, ok, seconds, tokens in, out, cost, error),
    read once per change of the file."""
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (st.st_size, st.st_mtime)
    got = _TURNS.get(path)
    if got and got[0] == key:
        return got[1]
    out = []
    with open(path, "rb") as fh:
        for raw in fh:
            try:
                t = json.loads(raw)
            except ValueError:
                continue
            kind = str(t.get("kind") or "turn")
            if kind == "agent":
                who = str(t.get("agent") or "agent")
                model = str(t.get("about") or "").split(",")[0].strip()
                where = f"{who} · {model}" if model and not model.startswith(who) else (model or who)
                ok = bool(t.get("ok")) and not t.get("error")
                err = "" if ok else (str(t.get("error") or "") or f"exit {t.get('rc')}: " + " ".join(str(t.get("stderr") or "").split())[-200:])
            else:
                who = str(t.get("model") or kind)
                where = (urlsplit(str(t.get("server") or "")).hostname or "local") + " · " + who
                ok = not t.get("error")
                err = str(t.get("error") or "")[:300]
            err = " ".join(_ANSI.sub("", err).split())
            notes = t.get("notes") if isinstance(t.get("notes"), dict) else {}
            tin = t.get("tokens_in", notes.get("input_tokens")) or 0
            tout = t.get("tokens_out", notes.get("output_tokens")) or 0
            try:
                out.append((float(t.get("ts") or 0), kind, who, where, ok, float(t.get("seconds") or 0), float(tin), float(tout),
                            float(t.get("cost_usd") or 0), err))
            except (TypeError, ValueError):
                continue
    _TURNS[path] = (key, out)
    return out


def turns(store: Any, runs: Any) -> list[tuple]:
    """Every loop's turns, each (user, app, *turn)."""
    from .workspace import Workspace

    out = []
    for u in store.users():
        for a in Workspace(store.data, u.name).apps():
            path = runs.turns_path(runs.latest(u, a["name"]))
            if path:
                out.extend((u.name, a["name"], *t) for t in _rows(path))
    return out


def usage_by_day(rows: list[tuple], days: int = 14, now: float | None = None) -> dict[str, Any]:
    """Turns, tokens and cost per day (the last `days`), per user and per agent or model; the
    loops that cost most in that time."""
    now = now or time.time()
    first = int(now // DAY) - days + 1
    labels = [time.strftime("%m-%d", time.gmtime((first + i) * DAY)) for i in range(days)]
    blank = lambda: {"turns": [0] * days, "tokens": [0.0] * days, "cost": [0.0] * days}   # noqa: E731
    by_user: dict[str, dict] = {}
    by_who: dict[str, dict] = {}
    loops: dict[tuple[str, str], dict[str, float]] = {}
    for user, app, ts, _kind, who, _where, _ok, secs, tin, tout, cost, _err in rows:
        d = int(ts // DAY) - first
        if not 0 <= d < days:
            continue
        for into in (by_user.setdefault(user, blank()), by_who.setdefault(who, blank())):
            into["turns"][d] += 1
            into["tokens"][d] += tin + tout
            into["cost"][d] += cost
        lp = loops.setdefault((user, app), {"turns": 0, "tokens": 0.0, "cost": 0.0, "seconds": 0.0})
        lp["turns"] += 1
        lp["tokens"] += tin + tout
        lp["cost"] += cost
        lp["seconds"] += secs
    top = sorted(({"user": u, "app": a, **v} for (u, a), v in loops.items()), key=lambda x: (-x["cost"], -x["tokens"]))[:10]
    return {"days": labels, "users": by_user, "agents": by_who, "top": top}


def endpoints(rows: list[tuple], since: float) -> list[dict[str, Any]]:
    """Each model endpoint and agent as its turns since `since` found it: turns, failures and
    their rate, the median and the slow (95th) seconds, the last failure, when last used."""
    by: dict[tuple[str, str], list[tuple]] = {}
    for _user, _app, ts, kind, who, where, ok, secs, *_rest, err in rows:
        if ts >= since:
            by.setdefault((kind, where), []).append((ts, ok, secs, err))
    out = []
    for (kind, where), xs in by.items():
        secs = sorted(s for _t, _ok, s, _e in xs)
        bad = [x for x in xs if not x[1]]
        q = lambda f: secs[min(len(secs) - 1, int(f * len(secs)))] if secs else 0.0   # noqa: E731
        last_bad = max(bad, key=lambda x: x[0]) if bad else None
        out.append({"kind": kind, "where": where, "turns": len(xs), "failed": len(bad), "rate": len(bad) / len(xs),
                    "p50": q(0.5), "p95": q(0.95), "last": max(x[0] for x in xs),
                    "last_error": last_bad[3] if last_bad else "", "last_error_at": last_bad[0] if last_bad else None})
    return sorted(out, key=lambda e: (-e["rate"], -e["turns"]))


def network(path: str, since: float) -> list[dict[str, Any]]:
    """The hosts the sandboxes refused since `since`: how often, by which loops, when last."""
    by: dict[tuple[str, int], dict[str, Any]] = {}
    try:
        fh = open(path, "rb")
    except OSError:
        return []
    with fh:
        for raw in fh:
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if float(e.get("t") or 0) < since:
                continue
            k = (str(e.get("host") or "?"), int(e.get("port") or 0))
            x = by.setdefault(k, {"host": k[0], "port": k[1], "count": 0, "loops": {}, "last": 0.0})
            x["count"] += 1
            app = str(e.get("app") or "?")
            x["loops"][app] = x["loops"].get(app, 0) + 1
            x["last"] = max(x["last"], float(e.get("t") or 0))
    out = [{**x, "loops": sorted(x["loops"].items(), key=lambda kv: -kv[1])[:5]} for x in by.values()]
    return sorted(out, key=lambda x: -x["count"])[:50]


def disk(store: Any, loops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per user: their home (logins, agents' sessions, caches), their loops (from Resources'
    sizes), the largest loop; the largest users first."""
    from .admin import dir_size

    by: dict[str, dict[str, Any]] = {}
    for u in store.users():
        home = store.data / "users" / u.name / "home"
        by[u.name] = {"user": u.name, "home": dir_size(home) if home.is_dir() else 0, "loops": 0, "count": 0, "largest": None}
    for lp in loops:
        x = by.get(lp["user"])
        if x is None:
            continue
        size = int(lp.get("total") or 0)
        x["loops"] += size
        x["count"] += 1
        if x["largest"] is None or size > x["largest"]["size"]:
            x["largest"] = {"app": lp["app"], "size": size}
    out = [{**x, "total": x["home"] + x["loops"]} for x in by.values()]
    return sorted(out, key=lambda x: -x["total"])
