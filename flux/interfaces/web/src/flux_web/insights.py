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

from .runs import OK_RC

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\[[0-9;]*m")

__all__ = ["disk", "endpoints", "failures", "network", "token_rate", "turns", "usage_by_day", "window"]

DAY = 86400.0
_TURNS: dict[str, tuple[int, int, list[tuple]]] = {}   # path -> (inode, bytes read, its turns): D921


def window(days: float, now: float | None = None) -> tuple[float, float]:
    """D920: "over the last" as one interval, captured once -- the last `days` × 24 h up to now,
    rolling as every historical panel's; each is cut by it before it is counted."""
    end = now if now is not None else time.time()
    return end - days * DAY, end


def failures(store: Any, runs: Any, since: float, until: float | None = None) -> dict[str, Any]:
    """Starts that failed since `since` (D920: and until `until`), newest first, each with why (its
    log's words, D757); the agents' Tests whose latest status, failed, was set in that time (D751),
    per user -- the store keeps each one's latest, not every failure."""
    until = until if until is not None else float("inf")
    starts = []
    for r in store.runs():
        if r.get("ended") and since <= r["ended"] <= until and r.get("rc") not in OK_RC:
            starts.append({"user": r["user"], "app": r["app"], "when": r["ended"], "rc": r["rc"], "why": [w.strip() for w in runs.failure(r)]})
    tests = []
    for u in store.users():
        from .agents import registry

        for agent in registry(store):
            t = store.server_get(f"agent-test:{u.name}:{agent}") or {}
            if t.get("when") and not t.get("ok") and since <= float(t["when"]) <= until:
                bad = next((s for s in t.get("steps") or [] if not s.get("ok")), {})
                tests.append({"user": u.name, "agent": agent, "when": t["when"], "step": bad.get("step", ""), "why": bad.get("said", "")})
    return {"starts": starts[:100], "tests": sorted(tests, key=lambda x: -x["when"])}


def _rows(path: str) -> list[tuple]:
    """A loop's turns, each (ts, kind, who, endpoint, ok, seconds, tokens in, out, cost, error).
    D921: read as the file grows -- a turn appended is that turn read, not the transcript again
    (another file, or one cut, from its start)."""
    from .usage import grown

    try:
        st = os.stat(path)
    except OSError:
        return []
    got = _TURNS.get(path)
    if got is None or got[0] != st.st_ino or got[1] > st.st_size:
        got = (st.st_ino, 0, [])
    ino, read, out = got
    more = grown(path, read)
    if more is None:
        return []
    if more[0] != ino:
        ino, out = more[0], []
        more = grown(path, 0) or (ino, [], 0)
    for raw in more[1]:
        try:
            t = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(t, dict):
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
    _TURNS[path] = (ino, more[2], out)
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


def token_rate(rows: list[tuple], hours: float = 24, points: int = 180, now: float | None = None) -> list[dict[str, Any]]:
    """Every loop's tokens per second over the last `hours` (D838), in and out, the coding agents'
    and Flux's own model's apart: each turn's tokens spread evenly over the time it took, summed per
    bucket of `hours / points`. A turn counts once it ends (the transcript is written then), so the
    newest bucket may grow."""
    now = now or time.time()
    size = hours * 3600 / points
    t0 = now - hours * 3600
    out = [{"t": t0 + (i + 1) * size, "in_agent": 0.0, "in_model": 0.0, "out_agent": 0.0, "out_model": 0.0} for i in range(points)]
    for _user, _app, ts, kind, _who, _where, _ok, secs, tin, tout, _cost, _err in rows:
        end = float(ts or 0)
        start = end - max(1.0, float(secs or 0))
        if end <= t0 or start >= now or not (tin or tout):
            continue
        k = "agent" if kind == "agent" else "model"
        for i in range(max(0, int((start - t0) // size)), min(points, int((end - t0) // size) + 1)):
            lo, hi = t0 + i * size, t0 + (i + 1) * size
            share = (min(end, hi) - max(start, lo)) / (end - start)
            if share > 0:
                out[i][f"in_{k}"] += float(tin or 0) * share / size
                out[i][f"out_{k}"] += float(tout or 0) * share / size
    return out


def usage_by_day(rows: list[tuple], days: int = 14, now: float | None = None) -> dict[str, Any]:
    """Turns, tokens and cost over the last `days` × 24 h up to `now` (D920: the interval, not UTC
    calendar days -- a turn outside it, or after `now`, is not counted), per user and per agent or
    model; the loops that cost most in that time. Bucketed for the sparklines only, back from `now`:
    an hour each over one day, else a day each; the totals are the rows' whatever the buckets."""
    start, now = window(days, now)
    hourly = days <= 1
    size = 3600.0 if hourly else DAY
    n = int(round((now - start) / size))
    labels = [time.strftime("%H:%M" if hourly else "%m-%d", time.gmtime(start + (i + 1) * size)) for i in range(n)]
    blank = lambda: {"turns": [0] * n, "tokens": [0.0] * n, "cost": [0.0] * n}   # noqa: E731
    by_user: dict[str, dict] = {}
    by_who: dict[str, dict] = {}
    loops: dict[tuple[str, str], dict[str, float]] = {}
    for user, app, ts, _kind, who, _where, _ok, secs, tin, tout, cost, _err in rows:
        if not start <= ts <= now:
            continue
        d = min(n - 1, int((ts - start) // size))
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
    return {"days": labels, "bucket": "hour" if hourly else "day", "users": by_user, "agents": by_who, "top": top}


def endpoints(rows: list[tuple], since: float, forgot: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Each model endpoint and agent as its turns since `since` found it: turns, failures and
    their rate, the median and the slow (95th) seconds, the last failure, when last used. `forgot`
    (D850): `kind|where` -> when the admin removed it; only turns after that count."""
    forgot = forgot or {}
    by: dict[tuple[str, str], list[tuple]] = {}
    for _user, _app, ts, kind, who, where, ok, secs, *_rest, err in rows:
        if ts >= since and ts > forgot.get(f"{kind}|{where}", 0.0):
            by.setdefault((kind, where), []).append((ts, ok, secs, err))
    out = []
    for (kind, where), xs in by.items():
        secs = sorted(s for _t, _ok, s, _e in xs)
        bad = [x for x in xs if not x[1]]
        q = lambda f: secs[min(len(secs) - 1, int(f * len(secs)))] if secs else 0.0   # noqa: E731
        last_bad = max(bad, key=lambda x: x[0]) if bad else None
        out.append({"key": f"{kind}|{where}", "kind": kind, "where": where, "turns": len(xs), "failed": len(bad), "rate": len(bad) / len(xs),
                    "p50": q(0.5), "p95": q(0.95), "last": max(x[0] for x in xs),
                    "last_error": last_bad[3] if last_bad else "", "last_error_at": last_bad[0] if last_bad else None})
    return sorted(out, key=lambda e: (-e["rate"], -e["turns"]))


def network(path: str, since: float, forgot: dict[str, float] | None = None, until: float | None = None) -> list[dict[str, Any]]:
    """The hosts the sandboxes refused since `since` (D920: and until `until`): how often, by which
    loops, when last; `forgot` (D850): `host:port` -> when the admin removed it."""
    forgot = forgot or {}
    until = until if until is not None else float("inf")
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
            if not since <= float(e.get("t") or 0) <= until:
                continue
            k = (str(e.get("host") or "?"), int(e.get("port") or 0))
            if float(e.get("t") or 0) <= forgot.get(f"{k[0]}:{k[1]}", 0.0):
                continue
            x = by.setdefault(k, {"key": f"{k[0]}:{k[1]}", "host": k[0], "port": k[1], "count": 0, "loops": {}, "last": 0.0})
            x["count"] += 1
            app = str(e.get("app") or "?")
            x["loops"][app] = x["loops"].get(app, 0) + 1
            x["last"] = max(x["last"], float(e.get("t") or 0))
    out = [{**x, "loops": sorted(x["loops"].items(), key=lambda kv: -kv[1])[:5]} for x in by.values()]
    return sorted(out, key=lambda x: -x["count"])[:200]


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
