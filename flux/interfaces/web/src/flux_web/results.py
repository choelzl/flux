"""A loop's results (D690): the designs it measured successfully, across every start, each
accepted or failed by the loop's limits, with its measurements.

A design is a result once a stage measured it; a draft sent to repair or refused by the gate is
not. Its numbers are each stage's latest; `shown` is the deepest stage measured, in the
document's order. Each objective's limit is read as the loop reads it (D899,
`flux_loop.eligibility`): on the numbers of the stage it names (no stage: the deepest measured).
A design is **accepted** (`eligible`) when it meets every limit; **pending** while a limit waits for
a later stage it has not reached; else **failed** -- a limit missed, or a required number not
measured at the stage that judges it. `reasons` says each one, short; `why` adds the stages'
cutoffs it missed (`at` a floor, `below` a ceiling, `within` a share of the best measured there),
which prune what climbs but are not requirements (D900). The decided design is the loop's latest
answer's."""

from __future__ import annotations

import bisect
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from collections import OrderedDict
from typing import Any

__all__ = ["content_key", "decision_doc", "decision_of", "decision_said", "designs", "measurement_summary", "thin"]

_NOT_MEASURED = ("gate", "admit", "prototype")


def measurement_summary(result: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Compact decision values and the same scoped references as measurementdata.js.

    Display choices are saved separately; every metric is available without fetching full results
    for every loop in a list. Baselines win over P90 performance of accepted designs.
    """
    def finite(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    def measured_when(design: dict[str, Any]) -> int:
        # Browser Date.parse uses milliseconds; the last baseline wins equal timestamps.
        return int(_when(design.get("last") or design.get("first") or "") * 1000)

    stage, group = decision["shown"], decision.get("group") or ""
    peers = [d for d in result["designs"] if (d.get("group") or "") == group]
    out = {}
    for metric in result["metrics"]:
        value = decision.get("stages", {}).get(stage, {}).get(metric)
        measured = [(d, d.get("stages", {}).get(stage, {}).get(metric)) for d in peers]
        measured = [(d, v) for d, v in measured if finite(v)]
        baseline = [(d, v) for d, v in measured if d.get("baseline")]
        reference = None
        if baseline:
            d, v = max(reversed(baseline), key=lambda pair: measured_when(pair[0]))
            reference = {"kind": "baseline", "name": d["name"], "value": v, "when": measured_when(d)}
        else:
            values = sorted(v for d, v in measured if (d["eligible"] if d.get("eligible") is not None else d.get("verdict") == "accepted"))
            if values:
                objective = next((o for o in result.get("limits", []) if o["metric"] == metric), {})
                direction = objective.get("direction") or result.get("metric_info", {}).get(metric, {}).get("direction")
                if not direction:
                    direction = "minimize" if re.search(r"area|power|energy|delay|latency|time|cells?|count|luts?|ffs?|error|loss|slack_viol|cost|size|bytes|cycles", metric, re.I) else "maximize"
                q = .1 if direction == "minimize" else .9
                pos = (len(values) - 1) * q
                lo, hi = math.floor(pos), math.ceil(pos)
                reference = {"kind": "P10" if q == .1 else "P90", "count": len(values),
                             "value": values[lo] * (1 - (pos - lo)) + values[hi] * (pos - lo)}
        percent = (value - reference["value"]) / abs(reference["value"]) * 100 if finite(value) and reference and reference["value"] != 0 else None
        out[metric] = {"value": value if finite(value) else None, "meets": decision.get("meets", {}).get(metric),
                       "percent": percent if finite(percent) else None, "reference": reference}
    return out


def _at(until: float) -> str:
    return datetime.fromtimestamp(until, timezone.utc).isoformat()


def _objective_limits(db: str, campaign: str | None = None, until: float | None = None) -> Any:
    """The objectives, from the given or latest campaign's latest `decided:objectives` -- read alone
    (D774), not with the whole record -- or None. Their limits are what a design must meet (D899)."""
    import sqlite3

    try:
        from flux_loop.objective import Objectives

        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT e.detail_json FROM campaign_events e WHERE e.kind = 'decided:objectives' "
                              + ("AND e.campaign_id = ? " if campaign else
                                 "AND e.campaign_id = (SELECT campaign_id FROM campaigns ORDER BY created_at DESC LIMIT 1) ")
                              + ("AND e.created_at <= ? " if until is not None else "")
                              + "ORDER BY e.id DESC LIMIT 1",
                              (*((campaign,) if campaign else ()), *((_at(until),) if until is not None else ()))).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return Objectives.from_doc((json.loads(row[0]) or {}).get("objectives") or [])
    except Exception:  # noqa: BLE001 -- a record without an objective: numbers without verdicts
        return None


def content_key(c: dict[str, Any]) -> str:
    """A recorded design's identity by what it is (D840): its text, else its knobs -- as
    `Candidate.key` -- since a name may be given again by a later start."""
    import hashlib

    body = c.get("artifact") or json.dumps(c.get("knobs") or {}, sort_keys=True, default=str)
    return hashlib.sha256(str(body).encode()).hexdigest()[:16]


def decision_of(db: str, answer_path: Any = None, campaign: str | None = None) -> str | None:
    """The decided design's name (`decision_doc`)."""
    got = decision_doc(db, answer_path, campaign)
    return got["name"] if got else None


def decision_doc(db: str, answer_path: Any = None, campaign: str | None = None, until: float | None = None) -> dict[str, Any] | None:
    """The loop's decision (D809): the record's latest pass's -- each pass writes its conclusion,
    so a loop that runs for days has one from its first pass on -- unless the run's answer
    (`runs/answer.json`, written when a run ends) is newer. `campaign`: the run's own (a record may
    hold a parent's and its sub-loops').

    D900: a pass that found no design meeting every requirement concludes with no decision; its
    conclusion stands over an older answer, and names the closest design apart (`closest`), with what
    it does not meet -- `{"name": None, "closest": {"name", "key", "unmet"}}`."""
    import sqlite3
    from datetime import datetime

    name, when, said, row = None, 0.0, {}, None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT detail_json, created_at FROM campaign_events WHERE kind = 'conclusion' "
                              + ("AND campaign_id = ? " if campaign else "")
                              + ("AND created_at <= ? " if until is not None else "") + "ORDER BY id DESC LIMIT 1",
                              (*((campaign,) if campaign else ()), *((_at(until),) if until is not None else ()))).fetchone()
        finally:
            con.close()
        if row:
            said = json.loads(row[0]) or {}
            name = said.get("decision")
            try:
                when = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00")).timestamp()
            except ValueError:
                when = 0.0
    except Exception:  # noqa: BLE001 -- no record: the answer alone
        name = None
    if answer_path is not None:
        try:
            st = os.stat(answer_path)
            if row is None or st.st_mtime > when:
                from .confine import open_read

                with open_read(answer_path, os.path.dirname(os.path.dirname(os.path.abspath(answer_path))), text=True) as fh:
                    ans = json.loads(fh.read())          # D852: runs/ is the run's to write
                dec = ans.get("decision") if isinstance(ans.get("decision"), dict) else {}
                near = ans.get("closest") if isinstance(ans.get("closest"), dict) else {}
                if dec.get("name"):
                    name, said = dec["name"], {**dec, "decision_key": dec.get("key")}
                elif near.get("name"):
                    name, said = None, {"closest": near["name"], "closest_key": near.get("key"), "unmet": near.get("unmet") or []}
        except (OSError, ValueError):
            pass
    if not name:
        if said.get("closest"):
            return {"name": None, "key": None, "metrics": {},
                    "closest": {"name": str(said["closest"]), "key": said.get("closest_key"), "unmet": list(said.get("unmet") or [])}}
        return None
    metrics = {k: v for k, v in (said.get("metrics") or said).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {"name": str(name), "key": said.get("decision_key"), "metrics": metrics}


def decision_said(db: str, campaign: str | None = None, until: float | None = None) -> str:
    """Why the record's latest pass decided as it did (D815): its `decided_by` -- e.g. "the least
    area_um2 at fmax_mhz >= 800" -- or ""."""
    import sqlite3

    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT detail_json FROM campaign_events WHERE kind = 'conclusion' "
                              + ("AND campaign_id = ? " if campaign else "")
                              + ("AND created_at <= ? " if until is not None else "") + "ORDER BY id DESC LIMIT 1",
                              (*((campaign,) if campaign else ()), *((_at(until),) if until is not None else ()))).fetchone()
        finally:
            con.close()
        return str((json.loads(row[0]) or {}).get("decided_by") or "") if row else ""
    except Exception:  # noqa: BLE001
        return ""


def _objectives(db: str) -> Any:
    """The record's latest objectives, whole (the ranking's), or None."""
    import sqlite3

    try:
        from flux_loop.objective import Objectives

        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT detail_json FROM campaign_events WHERE kind = 'decided:objectives' "
                              "ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            con.close()
        return Objectives.from_doc((json.loads(row[0]) or {}).get("objectives") or []) if row else None
    except Exception:  # noqa: BLE001
        return None


def _rank(out: list[dict[str, Any]], order: dict[str, int], db: str, n: int = 10, objectives: Any = None) -> None:
    """The best `n` by the loop's own rule (D809): `Objectives.decide` over the designs measured on
    the deepest stage any reached -- picked, set aside, picked again -- each its `rank` (1 the best)."""
    out = [d for d in out if not d.get("reference_only")]
    if objectives is None:
        objectives = _objectives(db)
    if not objectives or not out:
        return
    from types import SimpleNamespace

    deepest = max((s for d in out for s in d["stages"]), key=lambda s: order.get(s, -1))
    pool = [SimpleNamespace(stage=deepest, metrics=d["stages"][deepest], d=d) for d in out if deepest in d["stages"]]
    names = list(order) or None
    for i in range(1, n + 1):
        if not pool:
            break
        try:
            pick, _why = objectives.decide(pool, names)
        except Exception:  # noqa: BLE001 -- numbers the rule cannot read: no ranking
            return
        if pick is None:
            break
        pick.d["rank"] = i
        pool.remove(pick)


def _decided(out: list[dict[str, Any]], decision: Any) -> dict[str, Any] | None:
    """Mark the decided design (D840): by name and what it is when the record says it; else, of the
    designs with that name, the one whose numbers are the conclusion's; else the latest.

    D900: only an eligible design is the decision. A design a conclusion from before named that does
    not meet every requirement, or a conclusion with no decision, leaves no design marked; the closest
    is returned instead -- the one the conclusion names, else that old decision, else the best ranked
    -- and marked `closest`."""
    doc = decision if isinstance(decision, dict) else {"name": decision}
    old_pick = _find(out, doc) if doc.get("name") else None
    reference_pick = old_pick is not None and old_pick.get("reference_only")
    out = [d for d in out if not d.get("reference_only")]
    if reference_pick:
        ranked = [d for d in out if d["eligible"] and d["rank"] is not None]
        if ranked:
            min(ranked, key=lambda d: d["rank"])["decision"] = True
        return None
    if not decision:
        return None                                             # no pass ended yet: nothing decided, nothing closest
    picked = _find(out, doc) if doc.get("name") else None
    if picked is not None and picked["eligible"]:
        picked["decision"] = True
        return None
    near = _find(out, doc["closest"]) if isinstance(doc.get("closest"), dict) else None
    near = near or picked or min((d for d in out if d["rank"] is not None), key=lambda d: d["rank"], default=None)
    if near is None:
        return None
    near["closest"] = True
    return {"name": near["name"], "key": near["key"], "part": near["part"], "numbers": dict(near["numbers"]),
            "shown": near["shown"], "reasons": list(near["reasons"]) or list(near["why"])}


def _find(out: list[dict[str, Any]], doc: dict[str, Any]) -> dict[str, Any] | None:
    """The design a conclusion names, by name and what it is (D840)."""
    same = [d for d in out if d["base"] == doc.get("name")]
    if doc.get("key"):
        same = [d for d in same if d["key"] == doc["key"]] or same
    if len(same) > 1 and doc.get("metrics"):
        def fits(d: dict[str, Any]) -> bool:
            return any(all(abs(float(nums.get(k, float("nan"))) - float(v)) <= 1e-9 * max(1.0, abs(float(v)))
                           for k, v in doc["metrics"].items() if k in nums) and any(k in nums for k in doc["metrics"])
                       for nums in d["stages"].values())

        same = [d for d in same if fits(d)] or same
    return max(same, key=lambda d: d["last"] or "") if same else None


def _cutoffs(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for st in stages:
        cut = st.get("cutoff")
        for c in (cut if isinstance(cut, list) else [cut] if cut else []):
            if isinstance(c, dict) and c.get("metric"):
                out.append({"stage": st.get("name"), **c})
    return out


#: D901: one current view per record and view (its stages, its page size), the decision and the record's
#: disk signature what it is checked against, not part of its key -- a winner that changes replaces the
#: view, never adds one -- least recently used first out past `_KEEP_VIEWS` views or `_KEEP_DESIGNS` designs
_KEPT: "OrderedDict[tuple, tuple[tuple, str, float, dict[str, Any], list[float]]]" = OrderedDict()
_KEEPING = threading.Lock()
_KEEP_VIEWS = 32
_KEEP_DESIGNS = 100_000


def _signature(db: str) -> tuple:
    """The record as it stands on disk: the file and its write-ahead log."""
    out = []
    for p in (db, db + "-wal"):
        try:
            st = os.stat(p)
            out.append((st.st_size, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


def designs(db: str, stages: list[dict[str, Any]], decision: str | None = None, limit: int | None = 1000,
            stale_s: float = 0.0, since: Any = None, campaign: str | None = None, until: float | None = None) -> dict[str, Any]:
    """`stages`: the document's stages as the loader writes them (name, cutoff). D774: kept
    while the record is unchanged; with `stale_s`, also while it changed less than that ago --
    a running loop's line in a list need not be read again on every look. `since` (a start's
    time, D901): the result also says `this_start`, the designs first measured since, counted over
    every design, not the page. `campaign` and `until`: one campaign as it stood at a past start's end."""
    key = (db, json.dumps(stages, sort_keys=True, default=str), limit, campaign, until)
    said = json.dumps(decision, sort_keys=True, default=str)
    sig = _signature(db)
    with _KEEPING:
        got = _KEPT.get(key)
        if got is not None:
            _KEPT.move_to_end(key)
    if got is not None and got[1] == said and (got[0] == sig or time.monotonic() - got[2] < stale_s):
        out, firsts = got[3], got[4]
    else:
        out = _designs(db, stages, decision, limit, campaign, until)
        firsts = out.pop("_firsts", [])
        with _KEEPING:                                 # as it stands after the read (opening it touches its log)
            _KEPT[key] = (_signature(db), said, time.monotonic(), out, firsts)
            _KEPT.move_to_end(key)
            while len(_KEPT) > 1 and (len(_KEPT) > _KEEP_VIEWS or sum(len(v[4]) for v in _KEPT.values()) > _KEEP_DESIGNS):
                _KEPT.popitem(last=False)
    if since is None:
        return out
    try:
        t0 = float(since)
    except (TypeError, ValueError):
        return {**out, "this_start": 0}
    return {**out, "this_start": len(firsts) - bisect.bisect_left(firsts, t0)}


def _when(s: Any) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _designs(db: str, stages: list[dict[str, Any]], decision: str | None, limit: int | None,
             campaign: str | None = None, until: float | None = None) -> dict[str, Any]:
    from flux_store import CampaignStore

    store = CampaignStore(db)
    by: dict[tuple[str, str], dict[str, Any]] = {}
    definitions = {item["name"]: {k: v for k, v in item.items() if k != "name"}
                   for stage in stages for item in stage.get("metrics", []) if isinstance(item, dict) and "name" in item}
    groups = {name: {**spec, "metrics": []} for name, spec in definitions.items() if spec.get("type") == "dict"}
    try:
        for camp in store.list_campaigns():
            if campaign is not None and camp["campaign_id"] != campaign:
                continue
            for t in store.trials(camp["campaign_id"], status="ok"):
                if until is not None and _when(t.created_at) > until:
                    continue
                if t.result is None or not t.stage or t.stage in _NOT_MEASURED:
                    continue
                raw = {m: t.result.value_of(m) for m in t.result.metrics}
                numbers = {k: float(v) for k, v in raw.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
                if not numbers:
                    continue
                c = t.candidate or {}
                provenance = (c.get("meta") or {}).get("provenance") or {}
                for parent, spec in provenance.get("metric_specs", {}).items():
                    definitions.setdefault(parent, spec)
                    if spec.get("type") == "dict":
                        groups.setdefault(parent, {**spec, "metrics": []})
                for parent, tests in provenance.get("metric_groups", {}).items():
                    group = groups.setdefault(parent, {"type": "dict", "metrics": []})
                    for test in tests:
                        metric = f"{parent}.{test}"
                        if metric not in group["metrics"]:
                            group["metrics"].append(metric)
                name = str(c.get("name") or t.candidate_key or "?")
                part = str(c.get("subgoal") or (c.get("knobs") or {}).get("part") or "")
                ck = content_key(c)                 # D840: a name a later start gave again is another design
                # D896: which piece of a design of parts it is -- the whole (a composition of named parts,
                # D511), else its part: named, or (measured alone, recorded without it, D507) its name's head
                composed = [x for x in ((c.get("meta") or {}).get("composed") or ()) if x != "*"]
                group = "whole" if composed else (part or (name.split("#", 1)[0] if "#" in name else ""))
                d = by.setdefault((part, name, ck), {"name": name, "base": name, "key": ck, "part": part, "group": group,
                                                     "baseline": bool((c.get("meta") or {}).get("baseline")),
                                                     "reference_only": bool((c.get("meta") or {}).get("baseline") or (c.get("meta") or {}).get("baseline_metrics")),
                                                     "stages": {}, "first": t.created_at, "last": t.created_at})
                d["stages"][t.stage] = numbers
                d["last"] = t.created_at or d["last"]
    finally:
        store.close()
    order = {st.get("name"): i for i, st in enumerate(stages)}
    from flux_loop.eligibility import eligibility, judging_stage
    from flux_loop.objective import Objectives

    vector = _objective_limits(db, campaign, until) or Objectives()
    objectives = [o for o in vector if o.goal is not None]
    cutoffs = _cutoffs(stages)
    chain = [st.get("name") for st in stages] or sorted({s for d in by.values() for s in d["stages"]})
    directions = {o.metric: o.direction for o in vector}
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
        # D899: the objectives' limits by the loop's own reading -- a limit's stage judges it; a
        # required number missing there is "not measured", one a later stage will judge is pending.
        # A cutoff prunes what climbs (a design it cut is measured no deeper); it is not a requirement.
        judged = eligibility(vector, d["stages"], chain, stopped=bool(misses))
        measured = sorted(d["stages"], key=lambda s: order.get(s, -1))
        for o in objectives:
            st = judging_stage(o, measured, chain)
            v = d["stages"].get(st or "", {}).get(o.metric)
            if st == shown and v is not None:
                ok = v >= o.goal if o.direction == "maximize" else v <= o.goal
                meets[o.metric] = meets.get(o.metric, True) and ok
        d["eligible"], d["pending"], d["reasons"] = judged.eligible, judged.pending, list(judged.reasons)
        d["verdict"] = "accepted" if judged.eligible else "pending" if judged.pending else "failed"
        d["why"] = misses + list(judged.reasons)
        d["meets"] = meets
        d["decision"] = False
        d["closest"] = False
        d["rank"] = None
        out.append(d)
    names: dict[tuple[str, str], int] = {}
    for d in out:
        names[(d["part"], d["base"])] = names.get((d["part"], d["base"]), 0) + 1
    for d in out:                                   # a name given to more than one design: told apart by what each is
        if names[(d["part"], d["base"])] > 1:
            d["name"] = f"{d['base']}·{d['key'][:6]}"
    _rank(out, order, db, objectives=vector if campaign is not None or until is not None else None)
    closest = _decided(out, decision)                           # D900: after the ranking, its best is the closest
    out.sort(key=lambda d: d["last"] or "", reverse=True)       # newest first,
    out.sort(key=lambda d: not (d["decision"] or d["closest"]))  # the decided design (or the closest) on top
    metrics: list[str] = []
    for parent, group in groups.items():
        if group.get("aggregate") and group["aggregate"] != "none":
            group["metrics"].append(parent)
        for d in out:
            for numbers in d["stages"].values():
                for m in numbers:
                    if m.startswith(parent + ".") and m not in group["metrics"]:
                        group["metrics"].append(m)
        group["metrics"] = sorted(set(group["metrics"]))
    group_metrics = [m for group in groups.values() for m in group["metrics"]]
    for m in [o.metric for o in vector] + [c["metric"] for c in cutoffs] + [m for d in out for numbers in d["stages"].values() for m in numbers] + group_metrics:
        if m not in metrics:
            metrics.append(m)
    limits = [{"metric": o.metric, "direction": o.direction, "goal": o.goal, "stage": o.stage} for o in objectives]
    # Goal-free objectives drive ranking; constraints should not displace that metric in summaries.
    main = next((o.metric for o in vector if o.goal is None), metrics[0] if metrics else None)
    result = {"_firsts": sorted(_when(d["first"]) for d in out),          # D901: for `this_start`, over every design
            "designs": out[:limit], "total": len(out), "feasible": any(d["decision"] for d in out), "closest": closest,
            "counts": {k: sum(1 for d in out if d["verdict"] == k) for k in ("accepted", "pending", "failed")},
            "metrics": metrics, "main_metrics": [main] if main else [],
            "limits": limits, "stages": [st.get("name") for st in stages],
            "metric_groups": groups,
            "metric_info": {m: definitions.get(m) or next(({k: v for k, v in g.items() if k not in ("metrics", "type")}
                            for parent, g in groups.items() if m in g["metrics"]), {}) for m in metrics}}
    chosen = next((d for d in out if d["decision"]), None)
    result["decision_measurements"] = measurement_summary({**result, "designs": out}, chosen) if chosen else {}
    return result


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
