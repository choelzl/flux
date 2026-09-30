"""A loop's results (D690): the designs it measured successfully, across every start, each
accepted or failed by the loop's limits, with its measurements.

A design is a result once a stage measured it; a draft sent to repair or refused by the gate is
not. Its numbers are each stage's latest; `shown` is the deepest stage measured, in the
document's order. It is **accepted** when its numbers meet every limit that applies to them:

- each stage's cutoffs (the gates of a measurement: `at` a floor, `below` a ceiling, `within` a
  share of the best design measured at that stage), on that stage's numbers;
- each objective with a limit, on its stage's numbers when the design reached that stage (an
  objective without a stage: the deepest measured).

Otherwise it is **failed**, and each limit it misses is said. The decided design is the loop's
latest answer's."""

from __future__ import annotations

from typing import Any

__all__ = ["designs", "thin"]

_NOT_MEASURED = ("gate", "admit", "prototype")


def _objective_limits(db: str) -> list[dict[str, Any]]:
    try:
        from flux_loop.report import load

        rep = load(db)
    except Exception:  # noqa: BLE001 -- a record without an objective: numbers without verdicts
        return []
    return [{"metric": o.metric, "direction": o.direction, "goal": o.goal, "stage": o.stage}
            for o in rep.objectives if o.goal is not None]


def _cutoffs(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for st in stages:
        cut = st.get("cutoff")
        for c in (cut if isinstance(cut, list) else [cut] if cut else []):
            if isinstance(c, dict) and c.get("metric"):
                out.append({"stage": st.get("name"), **c})
    return out


def designs(db: str, stages: list[dict[str, Any]], decision: str | None = None, limit: int = 1000) -> dict[str, Any]:
    """`stages`: the document's stages as the loader writes them (name, cutoff)."""
    from flux_store import CampaignStore

    store = CampaignStore(db)
    by: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        for camp in store.list_campaigns():
            for t in store.trials(camp["campaign_id"], status="ok"):
                if t.result is None or not t.stage or t.stage in _NOT_MEASURED:
                    continue
                raw = {m: t.result.value_of(m) for m in t.result.metrics}
                numbers = {k: float(v) for k, v in raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
                if not numbers:
                    continue
                c = t.candidate or {}
                name = str(c.get("name") or t.candidate_key or "?")
                part = str(c.get("subgoal") or (c.get("knobs") or {}).get("part") or "")
                d = by.setdefault((part, name), {"name": name, "part": part, "stages": {}, "first": t.created_at,
                                                 "last": t.created_at})
                d["stages"][t.stage] = numbers
                d["last"] = t.created_at or d["last"]
    finally:
        store.close()
    order = {st.get("name"): i for i, st in enumerate(stages)}
    objectives = _objective_limits(db)
    cutoffs = _cutoffs(stages)
    directions = {o["metric"]: o["direction"] for o in objectives}
    best: dict[tuple[str, str], float] = {}                       # (stage, metric) -> the best measured, for `within`
    for c in cutoffs:
        if "within" in c:
            vals = [d["stages"][c["stage"]][c["metric"]] for d in by.values()
                    if c["metric"] in d["stages"].get(c["stage"], {})]
            if vals:
                best[(c["stage"], c["metric"])] = min(vals) if directions.get(c["metric"]) == "minimize" else max(vals)
    out = []
    for d in by.values():
        shown = max(d["stages"], key=lambda s: order.get(s, -1))
        d["shown"], d["numbers"] = shown, d["stages"][shown]
        misses, meets = [], {}
        for c in cutoffs:
            v = d["stages"].get(c["stage"], {}).get(c["metric"])
            if v is None:
                continue
            if "at" in c:
                ok, said = v >= float(c["at"]), f"{c['metric']} {v:g} is below {c['at']:g}"
            elif "below" in c:
                ok, said = v <= float(c["below"]), f"{c['metric']} {v:g} is above {c['below']:g}"
            else:
                b = best.get((c["stage"], c["metric"]))
                if b is None:
                    continue
                f = float(c["within"])
                if directions.get(c["metric"]) == "minimize":
                    ok, said = v <= b / f if b >= 0 else v <= b * f, f"{c['metric']} {v:g} is not within {f:.0%} of the best {b:g}"
                else:
                    ok, said = v >= b * f if b >= 0 else v >= b / f, f"{c['metric']} {v:g} is not within {f:.0%} of the best {b:g}"
            if not ok:
                misses.append(f"{said} (the {c['stage']} cutoff)")
            if c["stage"] == shown:
                meets[c["metric"]] = meets.get(c["metric"], True) and ok
        for o in objectives:
            # an objective of a stage judges the designs measured there; else the deepest measured
            st = o["stage"] if o["stage"] and o["stage"] not in ("deepest", "last") else shown
            v = d["stages"].get(st, {}).get(o["metric"])
            if v is None:
                continue
            ok = v >= o["goal"] if o["direction"] == "maximize" else v <= o["goal"]
            if not ok:
                misses.append(f"{o['metric']} {v:g} {'is below' if o['direction'] == 'maximize' else 'is above'} the limit "
                              f"{o['goal']:g} ({st})")
            if st == shown:
                meets[o["metric"]] = meets.get(o["metric"], True) and ok
        d["verdict"] = "failed" if misses else "accepted"
        d["why"] = misses
        d["meets"] = meets
        d["decision"] = bool(decision and d["name"] == decision)
        out.append(d)
    out.sort(key=lambda d: d["last"] or "", reverse=True)       # newest first,
    out.sort(key=lambda d: not d["decision"])                   # the decided design on top
    metrics: list[str] = []
    for m in [o["metric"] for o in objectives] + [c["metric"] for c in cutoffs] + [m for d in out for m in d["numbers"]]:
        if m not in metrics:
            metrics.append(m)
    limits = [{"metric": o["metric"], "direction": o["direction"], "goal": o["goal"]} for o in objectives]
    return {"designs": out[:limit], "total": len(out), "counts": {k: sum(1 for d in out if d["verdict"] == k) for k in ("accepted", "failed")},
            "metrics": metrics[:8], "limits": limits, "stages": [st.get("name") for st in stages]}


def thin(all_rows: list[Any], objectives: list[tuple[str, str]], cap: int = 3000) -> list[dict[str, Any]]:
    """At most `cap` measurements for the charts (D694): every one that set a new best on an
    objective at its stage, and an even share of the rest, in order."""
    keep = set(range(len(all_rows))) if len(all_rows) <= cap else set()
    if not keep:
        best: dict[tuple[str, str], float] = {}
        for i, x in enumerate(all_rows):
            for m, d in objectives:
                v = (x.metrics or {}).get(m)
                if not isinstance(v, (int, float)):
                    continue
                b = best.get((x.stage, m))
                if b is None or (v < b if d == "minimize" else v > b):
                    best[(x.stage, m)] = v
                    keep.add(i)
        room = max(0, cap - len(keep))
        rest = [i for i in range(len(all_rows)) if i not in keep]
        if room and rest:
            step = len(rest) / room
            keep.update(rest[int(k * step)] for k in range(min(room, len(rest))))
    return [{"when": x.when, "stage": x.stage, "name": x.name, "part": x.part, "whole": x.whole, "metrics": x.metrics}
            for i, x in enumerate(all_rows) if i in keep]
