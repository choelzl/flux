"""Mine typed, provenance-carrying facts from campaign stores (D243).

A fact must never read as more than the data supports. Four rules, enforced by construction:

1. **Computed, never asserted.** Every number in a fact comes from stored rows; every
   statement is generated from those numbers by this module's own fixed wording. There is no
   free-text field an author (human or model) fills in.
2. **Measured language only.** Statements say "measured", "observed on", "refused with" —
   past-tense reports of what happened. No fact says "is", "scales as", or "will".
3. **The boundary is part of the fact.** Every fact carries `scope` (where the evidence lives)
   and `not_established` (the inference the numbers do not license).
4. **Pointers.** Every fact names the store rows it was computed from, so it can be re-derived.

Not mined: fitted scaling laws (only measured point-pairs and ratios), analytic estimates as
measurements (they appear only inside bias facts, as predictions), and incomplete campaigns
(counted in `skipped`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Below this many distinct measured points, a residual family makes no direction claim; must match
# the calibrator's own correction threshold (D106).
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


# -- campaign-store miners ------------------------------------------------------------------


def _slim(candidate: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in candidate.items() if k != "arch"}


def _numeric_knobs(candidate: dict[str, Any]) -> dict[str, float]:
    """The extractor's numeric view of a candidate (D444): one definition."""
    from flux_records.extract import numeric_knobs

    return numeric_knobs(candidate)


def _metric_names(trial: Any) -> list[str]:
    return sorted(trial.result.metrics.keys()) if trial.result is not None else []


def _is_measured(trial: Any) -> bool:
    """A trial whose result a real tool produced (any metric simulated or measured), as
    opposed to an analytic prediction. Decided from the ABI `Method` on the estimates, not
    from phase names, which differ between writers."""
    from flux_store.result import Method

    if trial.result is None:
        return False
    return any(e.method != Method.ANALYTIC for e in trial.result.metrics.values())


def _measured_trials(store: Any, cid: str) -> list[Any]:
    return [t for t in store.trials(cid, status="ok") if _is_measured(t)]


def mine_measured_points(campaign_db_path: str) -> list[Fact]:
    """One fact per (campaign, stage, metric): the measurements — values a real tool
    produced for specific candidates, whatever phase the writer called it. Analytic
    estimates are deliberately absent here (they are predictions; they appear only
    inside bias facts as such)."""
    from flux_store import CampaignStore

    facts: list[Fact] = []
    with CampaignStore(campaign_db_path) as store:
        for row in store.list_campaigns():
            cid = row["campaign_id"]
            by_stage_metric: dict[tuple[str, str], list[Any]] = {}
            for t in _measured_trials(store, cid):
                for m in _metric_names(t):
                    by_stage_metric.setdefault((t.stage or "stage", m), []).append(t)
            for (stage, metric), trials in sorted(by_stage_metric.items()):
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
                    f"Stage {stage!r} measured {metric} for {len(points)} candidate(s) of "
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
                        f"campaign {cid}, stage {stage!r}, exactly the candidates listed — "
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
                        "stage": stage,
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
            # deepest measurement first: measured trials by stage, else nothing (analytic
            # values are predictions — a ratio of two predictions is a prediction, not an
            # observation)
            trials = _measured_trials(store, cid)
            by_key: dict[str, Any] = {}
            for t in sorted(trials, key=lambda t: t.seq):
                by_key[t.candidate_key] = t  # deepest stage wins per candidate
            from flux_records.extract import controlled_pairs

            items = list(by_key.values())
            known = [(_numeric_knobs(t.candidate), t) for t in items]
            for knob, (ka, a), (kb, b) in controlled_pairs(known, numeric=True):
                if set(ka) != set(kb):
                    continue                    # a knob one side lacks is not a doubling
                if True:
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
                                f"{ratio:.3f}x ({v_lo:g} -> {v_hi:g}), measured at stage "
                                f"{lo_t.stage!r}/{hi_t.stage!r} of campaign {cid[:12]}...."
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


#: The head of a refusal message a fact quotes (D671).
_HEAD = 160


def _ends(message: str, keep: int = 2000) -> str:
    """A long message kept by its start and its end, where tool and agent outputs differ."""
    return message if len(message) <= 2 * keep else f"{message[:keep]} ... {message[-keep:]}"


def _head(message: str) -> str:
    """The first non-empty line of `message`, cut at `_HEAD` characters, marked when cut."""
    first = next((ln.strip() for ln in str(message).splitlines() if ln.strip()), "")
    cut = len(first) > _HEAD or len(str(message).strip()) > len(first)
    return (first[:_HEAD] + " ...") if cut else first


def mine_refusal_patterns(campaign_db_path: str) -> list[Fact]:
    """Refusals, errors and constraint violations grouped by the head of their stored message:
    its first line, cut at `_HEAD` characters (a raw tool or agent output can run to pages; the
    head is what it reported first, not a paraphrase). The full messages stay in the evidence
    (D671). One fact per distinct (status, head)."""
    from flux_store import CampaignStore

    facts: list[Fact] = []
    with CampaignStore(campaign_db_path) as store:
        for row in store.list_campaigns():
            cid = row["campaign_id"]
            groups: dict[tuple[str, str], list[Any]] = {}
            for t in store.trials(cid):
                if t.status in ("refused", "error", "constraint_violated") and t.error:
                    groups.setdefault((t.status, _head(t.error)), []).append(t)
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
                        "full_messages": sorted({_ends(str(t.error)) for t in trials})[:3],
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


# -- prompt rendering -----------------------------------------------------------------------


def render_facts_for_prompt(
    facts: list[Fact] | list[dict[str, Any]], *, max_facts: int = 12
) -> str:
    """Render facts for an LLM prompt (D245): statement plus the not-established boundary,
    always together. Accepts Fact objects or their to_dict() form. Capped, and says so."""
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
) -> MinedKnowledge:
    """Mine every fact the given stores support. Missing/empty stores contribute nothing and
    are noted in `skipped` rather than raised — mining reports on what exists."""
    facts: list[Fact] = []
    skipped: list[str] = []
    for path in campaign_db_paths or ():
        try:
            facts.extend(mine_measured_points(path))
            facts.extend(mine_observed_ratios(path))
            facts.extend(mine_refusal_patterns(path))
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"campaign store {path}: {type(exc).__name__}: {exc}")
    return MinedKnowledge(facts=facts, skipped=skipped)
