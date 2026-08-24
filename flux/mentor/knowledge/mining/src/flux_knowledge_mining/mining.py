"""Mine typed, provenance-carrying facts from campaign and calibration stores
(docs/decisions.md D243) — the Knowledge role learning from the Evaluator's measured history.

The design center is the failure mode this module must NOT have: a fact that reads as more
than the data supports. Four rules, enforced by construction rather than by care:

1. **Computed, never asserted.** Every number in a fact comes from stored rows; every
   statement is generated from those numbers by this module's own fixed wording. There is no
   free-text field an author (human or model) fills in.
2. **Measured language only.** Statements say "measured", "observed on", "refused with" —
   past-tense reports of what happened. No fact says "is", "scales as", or "will".
3. **The boundary is part of the fact.** Every fact carries `scope` (exactly where the
   evidence lives) AND `not_established` (the inference the numbers do NOT license) — the
   anti-overgeneralization line is not a docstring here, it is a field the consumer receives.
4. **Pointers or it didn't happen.** Every fact names the store rows it was computed from —
   campaign ids and trial seqs, calibration record ids, content hashes — so any consumer can
   re-derive it from the same rows, and a fact that outlives its store is visibly dangling.

What is deliberately NOT mined: fitted scaling laws (only measured point-pairs and their
ratios), anything from screen-phase analytic estimates presented as measurement (screening
values appear only inside bias facts, AS the predictions they were), and anything from
campaigns that did not complete (an interrupted frontier is not an outcome; skipped campaigns
are counted, not silently dropped).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Below this many distinct measured points, a residual family reports its observed range but
# makes no direction/correction claim — the same threshold the calibrator itself corrects at
# (docs/decisions.md D106); the two must not disagree about what little data means.
_MIN_TRUSTED_N = 3


@dataclass(frozen=True)
class Fact:
    kind: str  # estimator_bias | measured_point | observed_ratio | refusal_pattern | frontier_outcome
    statement: str  # fixed-wording, measured-language sentence generated from `evidence`
    evidence: dict[str, Any]  # the stored numbers the statement is computed from, verbatim
    scope: str  # exactly where the evidence lives — the boundary of the claim
    not_established: str  # the inference these numbers do NOT license
    pointers: dict[str, Any]  # store rows: campaign ids, trial seqs, record ids, hashes
    caveats: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "statement": self.statement,
            "evidence": self.evidence,
            "scope": self.scope,
            "not_established": self.not_established,
            "pointers": self.pointers,
            "caveats": list(self.caveats),
        }


@dataclass
class MinedKnowledge:
    facts: list[Fact]
    skipped: list[str] = field(default_factory=list)  # what was NOT mined, and why — counted

    def to_dict(self) -> dict[str, Any]:
        return {"facts": [f.to_dict() for f in self.facts], "skipped": list(self.skipped)}


# -- estimator bias (calibration stores) ----------------------------------------------------


def mine_estimator_bias(calibration_db_path: str) -> list[Fact]:
    """One fact per (evaluator, metric) residual family: the observed prediction/reference
    ratio range over the exact measured points. Caveated records are counted but NEVER pooled —
    the calibrator excludes them for correction (D112) and a mined fact must not launder them
    back in."""
    from flux_calibration import CalibrationStore

    facts: list[Fact] = []
    with CalibrationStore(calibration_db_path) as cal:
        for evaluator, metric in cal.evaluator_metric_pairs():
            records = cal.records_for(evaluator, metric, exclude_caveated=True)
            caveated = [
                r for r in cal.records_for(evaluator, metric, exclude_caveated=False)
                if r["caveat"] is not None
            ]
            if not records:
                continue
            ratios = [r["predicted_value"] / r["reference_value"] for r in records]
            points = {(r["workload_hash"], r["arch_hash"]) for r in records}
            lo, hi = min(ratios), max(ratios)
            sources = sorted({r["reference_source"] for r in records})

            if lo > 1.0:
                direction = "over-predicted"
            elif hi < 1.0:
                direction = "under-predicted"
            else:
                direction = "predicted within"  # range spans 1.0: no direction claim
            statement = (
                f"{evaluator} {direction} {metric} at {lo:.3f}x-{hi:.3f}x of the reference "
                f"({'/'.join(sources)}) across {len(points)} measured (workload, arch) "
                f"point(s), {len(records)} record(s)."
            )
            caveats: list[str] = []
            if len(points) < _MIN_TRUSTED_N:
                caveats.append(
                    f"only {len(points)} distinct point(s) — below the correction threshold "
                    "(D106): range reported, no systematic-bias claim"
                )
            if caveated:
                caveats.append(
                    f"{len(caveated)} caveated record(s) excluded from the range (their own "
                    "store entries say the pool does not describe them)"
                )
            facts.append(Fact(
                kind="estimator_bias",
                statement=statement,
                evidence={
                    "ratios": ratios,
                    "mean_relative_residual": sum(r["relative_residual"] for r in records)
                    / len(records),
                    "records": [
                        {k: r[k] for k in ("id", "workload_hash", "arch_hash",
                                           "predicted_value", "reference_value",
                                           "reference_source")}
                        for r in records
                    ],
                },
                scope=(
                    f"the {len(points)} (workload_hash, arch_hash) point(s) listed in "
                    "evidence.records — no other workloads, architectures, or metrics"
                ),
                not_established=(
                    "behavior at any unmeasured point (other widths, shapes, workloads, or "
                    "metrics); that the ratio is constant between or beyond the measured points"
                ),
                pointers={
                    "calibration_db": calibration_db_path,
                    "record_ids": [r["id"] for r in records],
                    "evaluator": evaluator,
                    "metric": metric,
                    "excluded_caveated_record_ids": [r["id"] for r in caveated],
                },
                caveats=tuple(caveats),
            ))
    return facts


# -- campaign-store miners ------------------------------------------------------------------


def _slim(candidate: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in candidate.items() if k != "arch"}


def _numeric_knobs(candidate: dict[str, Any]) -> dict[str, float]:
    """Flat numeric view of a candidate's parameters (assignment sub-dicts flattened) — the
    keys ratio mining may pair on. Non-numeric entries are ignored, never coerced."""
    out: dict[str, float] = {}
    for k, v in _slim(candidate).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = float(v)
        elif isinstance(v, dict):
            for kk, vv in v.items():
                if isinstance(vv, (int, float)) and not isinstance(vv, bool):
                    out[f"{k}.{kk}"] = float(vv)
    return out


def _metric_names(trial: Any) -> list[str]:
    return sorted(trial.result.metrics.keys()) if trial.result is not None else []


def mine_measured_points(campaign_db_path: str) -> list[Fact]:
    """One fact per (campaign, rung, metric): the escalation-phase measurements — values a
    real tool produced for specific candidates. Screen-phase estimates are deliberately
    absent here (they are predictions; they appear only inside bias facts as such)."""
    from flux_store import CampaignStore

    facts: list[Fact] = []
    with CampaignStore(campaign_db_path) as store:
        for row in store.list_campaigns():
            cid = row["campaign_id"]
            by_rung_metric: dict[tuple[str, str], list[Any]] = {}
            for t in store.ok_trials(cid, phase="escalate"):
                for m in _metric_names(t):
                    by_rung_metric.setdefault((t.rung or f"rung{t.rung_index}", m), []).append(t)
            for (rung, metric), trials in sorted(by_rung_metric.items()):
                points = []
                for t in sorted(trials, key=lambda t: t.seq):
                    est = t.result.estimate_of(metric)
                    points.append({
                        "candidate": _slim(t.candidate),
                        "value": est.value,
                        "unit": est.unit,
                        "method": est.method.value,
                        "evaluator": t.result.provenance.evaluator,
                        "seq": t.seq,
                    })
                values = [p["value"] for p in points]
                unit = points[0]["unit"]
                statement = (
                    f"Rung {rung!r} measured {metric} for {len(points)} candidate(s) of "
                    f"campaign {cid[:12]}...: "
                    + "; ".join(
                        f"{p['candidate']} -> {p['value']:g} {p['unit']}" for p in points[:6])
                    + ("; ..." if len(points) > 6 else "")
                    + f" (range {min(values):g}-{max(values):g} {unit})."
                )
                facts.append(Fact(
                    kind="measured_point",
                    statement=statement,
                    evidence={"points": points},
                    scope=(
                        f"campaign {cid}, rung {rung!r}, exactly the candidates listed — "
                        "one workload, one base architecture family"
                    ),
                    not_established=(
                        "values for any unlisted candidate; that these candidates are optimal "
                        "in any larger space; transfer to other workloads or technologies"
                    ),
                    pointers={
                        "campaign_db": campaign_db_path,
                        "campaign_id": cid,
                        "trial_seqs": [p["seq"] for p in points],
                        "rung": rung,
                        "metric": metric,
                    },
                ))
    return facts


def mine_observed_ratios(campaign_db_path: str) -> list[Fact]:
    """Measured point-PAIRS whose candidates differ in exactly one numeric knob by exactly 2x:
    the observed effect of one doubling, reported as the two stored values and their ratio.
    Deliberately not a fit: two points license a ratio between them and nothing else."""
    from flux_store import CampaignStore

    facts: list[Fact] = []
    with CampaignStore(campaign_db_path) as store:
        for row in store.list_campaigns():
            cid = row["campaign_id"]
            # deepest measurement first: escalate trials by rung, else nothing (screen values
            # are predictions — a ratio of two predictions is a prediction, not an observation)
            trials = store.ok_trials(cid, phase="escalate")
            by_key: dict[str, Any] = {}
            for t in sorted(trials, key=lambda t: (t.rung_index or 0, t.seq)):
                by_key[t.candidate_key] = t  # deepest rung wins per candidate
            items = list(by_key.values())
            for i, a in enumerate(items):
                ka = _numeric_knobs(a.candidate)
                for b in items[i + 1:]:
                    kb = _numeric_knobs(b.candidate)
                    if set(ka) != set(kb):
                        continue
                    diff = [k for k in ka if ka[k] != kb[k]]
                    if len(diff) != 1:
                        continue
                    (knob,) = diff
                    lo_t, hi_t = (a, b) if ka[knob] < kb[knob] else (b, a)
                    lo_v = _numeric_knobs(lo_t.candidate)[knob]
                    hi_v = _numeric_knobs(hi_t.candidate)[knob]
                    if hi_v != 2 * lo_v:
                        continue
                    for metric in set(_metric_names(lo_t)) & set(_metric_names(hi_t)):
                        v_lo = lo_t.result.value_of(metric)
                        v_hi = hi_t.result.value_of(metric)
                        if v_lo == 0 or not math.isfinite(v_lo) or not math.isfinite(v_hi):
                            continue
                        ratio = v_hi / v_lo
                        facts.append(Fact(
                            kind="observed_ratio",
                            statement=(
                                f"Doubling {knob} {lo_v:g}->{hi_v:g} changed {metric} by "
                                f"{ratio:.3f}x ({v_lo:g} -> {v_hi:g}), measured at rung "
                                f"{lo_t.rung!r}/{hi_t.rung!r} of campaign {cid[:12]}...."
                            ),
                            evidence={
                                "knob": knob, "knob_values": [lo_v, hi_v],
                                "metric": metric, "values": [v_lo, v_hi], "ratio": ratio,
                                "candidates": [_slim(lo_t.candidate), _slim(hi_t.candidate)],
                            },
                            scope=(
                                f"exactly these two measured candidates of campaign {cid}; "
                                "all other knobs equal between them"
                            ),
                            not_established=(
                                "a scaling law; the ratio at any other knob value or between "
                                "other points; transfer to other workloads or metrics"
                            ),
                            pointers={
                                "campaign_db": campaign_db_path,
                                "campaign_id": cid,
                                "trial_seqs": [lo_t.seq, hi_t.seq],
                            },
                        ))
    return facts


def mine_refusal_patterns(campaign_db_path: str) -> list[Fact]:
    """Refusals, errors and constraint violations grouped by their EXACT stored message —
    no normalization, no clustering: a paraphrased error is an interpretation, and grouping
    two different messages under one pattern is exactly the misleading summary this module
    exists to avoid. One fact per distinct (status, message)."""
    from flux_store import CampaignStore

    facts: list[Fact] = []
    with CampaignStore(campaign_db_path) as store:
        for row in store.list_campaigns():
            cid = row["campaign_id"]
            groups: dict[tuple[str, str], list[Any]] = {}
            for t in store.trials(cid):
                if t.status in ("refused", "error", "constraint_violated") and t.error:
                    groups.setdefault((t.status, t.error), []).append(t)
            for (status, message), trials in sorted(groups.items()):
                facts.append(Fact(
                    kind="refusal_pattern",
                    statement=(
                        f"{len(trials)} trial(s) of campaign {cid[:12]}... ended "
                        f"{status} with: {message!r}"
                    ),
                    evidence={
                        "status": status,
                        "message": message,
                        "candidates": [_slim(t.candidate) for t in trials],
                    },
                    scope=f"campaign {cid}, exactly the trials listed",
                    not_established=(
                        "that other candidates fail the same way; the full precondition of "
                        "the failure (the message states what the tool reported, no more)"
                    ),
                    pointers={
                        "campaign_db": campaign_db_path,
                        "campaign_id": cid,
                        "trial_seqs": [t.seq for t in trials],
                    },
                ))
    return facts


def mine_frontier_outcomes(campaign_db_path: str) -> tuple[list[Fact], list[str]]:
    """One fact per DONE campaign: its final frontier at the deepest covering fidelity, with
    per-metric fidelity labels. Campaigns in any other state are counted in `skipped` — an
    interrupted frontier is not an outcome, and silently dropping it would misreport the store."""
    from flux_search_campaign import composite_frontier, frontier_payload, parse_objective
    from flux_store import CampaignStore

    facts: list[Fact] = []
    skipped: list[str] = []
    with CampaignStore(campaign_db_path) as store:
        for row in store.list_campaigns():
            cid = row["campaign_id"]
            if row["status"] != "done":
                skipped.append(
                    f"campaign {cid}: status {row['status']!r} — no outcome fact mined")
                continue
            objective = parse_objective(store.campaign_row(cid)["objective"])
            composite = composite_frontier(store, cid, objective)
            if composite:
                entries, fidelity_note = composite, "deepest covering rung per metric"
            else:
                entries = frontier_payload(
                    store.ok_trials(cid, phase="screen"), objective.screened_view())
                fidelity_note = "screening estimates only — no rung covered every contender"
            summary = "; ".join(
                f"{e['candidate']} -> "
                + ", ".join(
                    f"{m}={v['value']:g}"
                    + (f" ({v['fidelity']})" if "fidelity" in v else " (screen estimate)")
                    for m, v in sorted(e["metrics"].items()))
                for e in entries[:4]
            ) + ("; ..." if len(entries) > 4 else "")
            objectives_text = ", ".join(
                f"{m.direction} {m.metric}" for m in objective.metrics)
            facts.append(Fact(
                kind="frontier_outcome",
                statement=(
                    f"Campaign {objective.id!r} ({objectives_text}) completed with a "
                    f"{len(entries)}-point frontier [{fidelity_note}]: {summary}"
                ),
                evidence={"frontier": entries, "objective_id": objective.id,
                          "mode": objective.mode},
                scope=(
                    f"campaign {cid}: its own workload, base architecture and search space "
                    "as recorded in the objective document at the pointer"
                ),
                not_established=(
                    "optimality outside the campaign's own search space and budget; screen-"
                    "fidelity numbers are calibrated-or-raw model estimates, not measurements"
                    if "screen" in fidelity_note else
                    "optimality outside the campaign's own search space and budget"
                ),
                pointers={
                    "campaign_db": campaign_db_path,
                    "campaign_id": cid,
                    "objective_hash": store.campaign_row(cid)["objective_hash"],
                },
            ))
    return facts, skipped


# -- prompt rendering -----------------------------------------------------------------------


def render_facts_for_prompt(
    facts: list[Fact] | list[dict[str, Any]], *, max_facts: int = 12
) -> str:
    """Render facts for an LLM prompt (docs/decisions.md D245): statement plus the
    NOT-ESTABLISHED boundary, always together — a fact whose limits get trimmed away is
    exactly the incorrect-assumption vector mining was built to avoid, so the boundary is not
    optional here. Accepts Fact objects or their to_dict() form. Capped (stated when it
    bites): a prompt drowning in facts stops being advice."""
    rows: list[dict[str, Any]] = [
        f.to_dict() if isinstance(f, Fact) else f for f in facts
    ]
    lines: list[str] = []
    for row in rows[:max_facts]:
        lines.append(f"- {row['statement']}")
        boundary = row.get("not_established")
        if boundary:
            lines.append(f"  NOT established: {boundary}")
    if len(rows) > max_facts:
        lines.append(f"({len(rows) - max_facts} more fact(s) not shown)")
    return "\n".join(lines)


# -- the aggregator -------------------------------------------------------------------------


def mine_knowledge(
    campaign_db_paths: list[str] | None = None,
    calibration_db_paths: list[str] | None = None,
) -> MinedKnowledge:
    """Mine every fact the given stores support. Missing/empty stores contribute nothing and
    are noted in `skipped` rather than raised — mining reports on what exists."""
    facts: list[Fact] = []
    skipped: list[str] = []
    for path in calibration_db_paths or ():
        try:
            facts.extend(mine_estimator_bias(path))
        except Exception as exc:  # noqa: BLE001 — one bad store must not hide the others
            skipped.append(f"calibration store {path}: {type(exc).__name__}: {exc}")
    for path in campaign_db_paths or ():
        try:
            facts.extend(mine_measured_points(path))
            facts.extend(mine_observed_ratios(path))
            facts.extend(mine_refusal_patterns(path))
            outcome_facts, outcome_skipped = mine_frontier_outcomes(path)
            facts.extend(outcome_facts)
            skipped.extend(outcome_skipped)
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"campaign store {path}: {type(exc).__name__}: {exc}")
    return MinedKnowledge(facts=facts, skipped=skipped)
