"""How much a study has TRIED, and what it has found so far, read from the campaign store.

Written inside one application's demo and generic to all of them: every long search wants to
report what it attempted, tell an orchestrator what the frontier looks like, and start a fresh
proposal series without resuming a spent one. None of that is about the thing being designed.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

_EMPTY = dict.fromkeys(
    ("attempted", "screened", "measured", "refused", "failed", "proposed"), 0)


def campaign_tally(db: str | Path) -> dict[str, int]:
    """Distinct CANDIDATES by outcome, not trials.

    Distinct matters: the same candidate screened in three overlapping scopes is one thing that
    was tried, and counting trials inflates the figure exactly where scopes overlap. Refused
    candidates count as attempted, because a candidate ruled out by its own dimensions was still
    considered, and omitting it makes a search look narrower than it was.

    An unwritten store answers zero rather than raising: this is called BEFORE the first campaign
    to establish what a run inherited, and on a clean machine that store has no schema yet.
    """
    if not Path(db).exists():
        return dict(_EMPTY)
    con = sqlite3.connect(str(db))
    try:
        def distinct(where: str) -> int:
            return con.execute(
                f"select count(distinct candidate_key) from trials where {where}").fetchone()[0]

        return {
            "attempted": distinct("1=1"),
            "screened": distinct("phase='screen'"),
            "measured": distinct("phase='escalate' and status='ok'"),
            "refused": distinct("status='constraint_violated'"),
            "failed": distinct("status='error'"),
            "proposed": distinct("strategy_kind like '%generative%'"),
        }
    except sqlite3.Error:
        return dict(_EMPTY)
    finally:
        con.close()


def next_proposal_series(db: str | Path) -> int:
    """The next unused proposal-series number for this store.

    A round's objective document carries its step number and a campaign's identity is that
    document's hash, so a second run's step 5 IS the first run's step 5, resumed. For enumeration
    that is exactly right and is what makes a re-run cheap. For PROPOSING it is silently wrong:
    the strategy's budget was already spent there, so it returns nothing, the run records no new
    candidates, and a convergence check reads exhaustion where there was only resumption. Tagging
    each run's proposal rounds with a fresh series makes asking again actually ask again.
    """
    if not Path(db).exists():
        return 1
    con = sqlite3.connect(str(db))
    try:
        row = con.execute("select count(distinct campaign_id) from trials "
                          "where strategy_kind like '%generative%'").fetchone()
        return int(row[0] or 0) + 1
    except sqlite3.Error:
        return 1
    finally:
        con.close()


def frontier_digest(db: str | Path, *, metrics: tuple[str, ...], limit: int = 6,
                    label_key: str = "label") -> str:
    """The measured frontier in a few lines, for a prompt rather than a report.

    Deliberately lossy: an orchestrator choosing where to look next needs the shape of what has
    been found, and a full table would spend prompt budget that is charged on every decision.
    """
    if not Path(db).exists():
        return "(nothing measured yet)"
    con = sqlite3.connect(str(db))
    rows: list[tuple[str, dict[str, Any]]] = []
    try:
        query = ("select t.candidate_json, r.result_json from trials t "
                 "join results r on t.result_id = r.id where t.status = 'ok'")
        for candidate_json, result_json in con.execute(query):
            values = {}
            metric_rows = json.loads(result_json).get("metrics", {})
            for metric in metrics:
                got = (metric_rows.get(metric) or {}).get("value")
                if got is not None:
                    values[metric] = got
            if len(values) == len(metrics):
                rows.append((json.loads(candidate_json).get(label_key, "?"), values))
    except sqlite3.Error:
        return "(store unreadable)"
    finally:
        con.close()
    if not rows:
        return "(nothing measured yet)"
    best = {}
    for label, values in rows:
        best[label] = values
    ordered = sorted(best.items(), key=lambda kv: kv[1][metrics[0]])[:limit]
    return "\n".join(
        "  " + label[:38].ljust(40) + "  ".join(f"{m}={v[m]:.4g}" for m in metrics)
        for label, v in ordered)


def measured_results(store, evaluator_id: str, metrics: dict[str, str], *,
                     constraint_metric: str | None = None) -> tuple[dict, dict, dict]:
    """Everything this store measured, split into (ok, refused, stale).

    STALE is the point. A store outlives the code that filled it, and mixing rows from a
    superseded evaluator method into a current table is how a candidate once appeared at a
    frequency computed a different way, with nothing on the row saying so. Rows whose provenance
    names a different evaluator are separated and returned so the caller can COUNT and NAME them,
    never silently dropped and never silently mixed in.

    `metrics` maps the caller's column key to the metric to read, so an application chooses its
    own column names without this function knowing any of them.
    """
    ok: dict[str, dict] = {}
    refused: dict[str, float] = {}
    stale: dict[str, str] = {}
    for row in store.list_campaigns():
        cid = row["campaign_id"]
        for trial in store.ok_trials(cid, phase="escalate"):
            label = trial.candidate.get("label", trial.candidate_key)
            if trial.result.provenance.evaluator != evaluator_id:
                stale[label] = trial.result.provenance.evaluator
                continue
            ok[label] = {key: trial.result.value_of(metric) for key, metric in metrics.items()}
            ok[label]["_candidate"] = trial.candidate
        if constraint_metric is None:
            continue
        for trial in store.trials(cid, phase="escalate", status="constraint_violated"):
            label = trial.candidate.get("label", trial.candidate_key)
            if trial.result is None:
                continue
            if trial.result.provenance.evaluator != evaluator_id:
                stale[label] = trial.result.provenance.evaluator
                continue
            refused[label] = trial.result.value_of(constraint_metric)
    return ok, refused, stale


def tally_lines(tally: dict[str, int], *, added: int, steps: int, noun: str = "candidates",
                proposed_noun: str = "proposed by a model rather than enumerated by rule",
                ) -> list[str]:
    """How much was tried, as lines to print.

    BOTH totals, because a resumed store makes them different and the difference is the whole
    point: a warm store can show a large total beside a run that added nothing, which reads as a
    large study and was not one.
    """
    lines = [f"{tally['attempted']} distinct {noun} in this store "
             f"({added} first tried by this run, over {steps} step(s))",
             f"    {tally['screened']} screened, {tally['measured']} measured with real tools, "
             f"{tally['refused']} refused by constraint, {tally['failed']} failed to build"]
    if tally.get("proposed"):
        lines.append(f"    {tally['proposed']} of them {proposed_noun}")
    return lines
