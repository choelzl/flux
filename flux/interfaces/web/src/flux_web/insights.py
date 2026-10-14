"""What an admin reads at a glance (D766), from what the server keeps already: failed starts with
their own words and agents' failed Tests; turns, tokens and cost by day, user, agent and loop; the
model endpoints and agents as their turns found them -- how many, how many failed, how long; the
hosts the sandboxes refused; and where the disk goes, by user."""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any
from urllib.parse import urlsplit

from .runs import OK_RC

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\[[0-9;]*m")

__all__ = ["disk", "failures", "journals", "network", "transcripts", "turn_of", "window"]

DAY = 86400.0


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


def turn_of(t: Any) -> tuple | None:
    """A transcript line as a turn: (ts, kind, who, endpoint, ok, seconds, tokens in, out, cost, error).
    `ok=None` marks an agent's local time limit, neutral for endpoint health."""
    if not isinstance(t, dict):
        return None
    kind = str(t.get("kind") or "turn")
    if kind == "agent":
        who = str(t.get("agent") or "agent")
        model = str(t.get("about") or "").split(",")[0].strip()
        where = f"{who} · {model}" if model and not model.startswith(who) else (model or who)
        ok = bool(t.get("ok")) and not t.get("error")
        err = "" if ok else (str(t.get("error") or "") or f"exit {t.get('rc')}: " + " ".join(str(t.get("stderr") or "").split())[-200:])
        if t.get("rc") == 124:                  # Flux stopped the agent at its local time budget
            ok = None
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
        got = (float(t.get("ts") or 0), kind, who, where, ok, float(t.get("seconds") or 0), float(tin), float(tout),
               float(t.get("cost_usd") or 0), err)
    except (TypeError, ValueError):
        return None
    # D1069: a number that is not one (NaN, infinity) would poison every sum it joins -- the turn is skipped
    return got if all(math.isfinite(v) for v in (got[0], got[5], got[6], got[7], got[8])) and got[6] >= 0 and got[7] >= 0 else None


def journals(store: Any, runs: Any) -> list[str]:
    """D1085: each loop's task journal of its latest start -- where its agents report their tokens as they work."""
    from .workspace import Workspace

    out = set()
    for u in store.users():
        for a in Workspace(store.data, u.name).apps():
            p = runs.events_path(runs.latest(u, a["name"]))
            if p:
                out.add(p)
    return sorted(out)


def transcripts(store: Any, runs: Any) -> list[tuple[str, str, str]]:
    """D1069: every loop's transcripts, (user, app, path) -- the latest start's and every retained
    campaign's, so restarting a loop cannot erase its history -- for the usage log to read."""
    from .workspace import Workspace

    out = set()
    for run in store.runs():
        for p in runs.turns_paths(run):
            out.add((run["user"], run["app"], p))
    for u in store.users():
        for a in Workspace(store.data, u.name).apps():
            p = runs.turns_path(runs.latest(u, a["name"]))
            if p:
                out.add((u.name, a["name"], p))
    return sorted(out)


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
