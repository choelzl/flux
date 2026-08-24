"""The mentor's record layer: results and conclusions, as semantics over the store (D397).

`core/stores` is the storage engine; this is what a loop MEANS by it. Records hold two
distinct things and keep them distinct: MEASUREMENTS (trials with their stage, status
and metrics) and CONCLUSIONS (what a run decided the measurements meant, stored as
events labelled INFERENCE, the D297 posture). `flux_records.extract` (D565: one package reads the
record) then builds
rules from the measurements -- three layers, each honest about what it is: store =
bytes, records = results + conclusions, extract = laws.

The API generalises the prefetcher's Recorder (D367): domain-neutral candidate dicts,
a caller-named metric set, the same swallow-everything discipline -- a study must not
die because its logbook is unwritable, so every method degrades to a no-op and the
run continues without a record rather than not at all.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable

__all__ = ["ConclusionRow", "Records", "RefusalRow", "TrialRow"]


@dataclass(frozen=True)
class TrialRow:
    """One measured candidate as the record holds it (D445): the candidate document, its
    key, the stage it was measured on, its metrics and status."""

    candidate: dict[str, Any]
    key: str
    stage: str
    metrics: dict[str, float]
    status: str = "ok"


@dataclass(frozen=True)
class RefusalRow:
    """One refused candidate: the document, its key, the reason and the stage."""

    candidate: dict[str, Any]
    key: str
    reason: str
    stage: str


@dataclass(frozen=True)
class ConclusionRow:
    """One conclusion a run drew: its detail document, its label (INFERENCE) and when."""

    detail: dict[str, Any]
    label: str = "INFERENCE"
    created_at: float | None = None


class Records:
    """One campaign's results and conclusions in a CampaignStore, resumable."""

    def __init__(self, db: str, objective: dict[str, Any],
                 log: Callable[[str], None] | None = None, name: str | None = None) -> None:
        """`name` (D524) keys the campaign by the problem: a document's `campaign: {name: nlu}`;
        without one the campaign id is the objective's hash, as it was."""
        say = log or (lambda _m: None)
        self.store = None
        self.campaign_id = ""
        self._phase = "search"
        self.resumed = False
        try:
            from flux_store import CampaignStore

            digest = hashlib.sha256(
                json.dumps(objective, sort_keys=True).encode()).hexdigest()
            self.store = CampaignStore(db)
            self.campaign_id, created = self.store.start_campaign(objective, digest, campaign_id=name or None)
            self.resumed = not created
            say(f"campaign {self.campaign_id[:12]} "
                f"({'new' if created else 'resumed'}) in {db}")
        except Exception as exc:  # noqa: BLE001
            say(f"  (no campaign record: {type(exc).__name__}: {exc!s:.90})")
            self.store = None

    # ---- measurements -------------------------------------------------------
    def phase(self, name: str) -> None:
        self._phase = name
        if self.store is not None:
            try:
                self.store.set_phase(self.campaign_id, name)
            except Exception:  # noqa: BLE001
                pass

    def trial(self, candidate: dict[str, Any], key: str, *, stage: str,
              strategy: str, metrics: dict[str, float] | None,
              error: str | None = None, wall_s: float = 0.0,
              analytic: bool | Iterable[str] = True, evaluator: str = "flux@records",
              workload_hash: str = "") -> None:
        """One measured or refused candidate. `metrics=None` with `error` = a refusal.
        `analytic` is the method tag: True/False for every metric, or the names of the
        metrics that are modelled while the rest are measured (a storage model beside a
        simulated speedup, D446)."""
        if self.store is None:
            return
        try:
            from flux_evaluator_abi import (
                Bottleneck, Domain, Escalation, Estimate, Limiter, Method, Provenance,
                Result, Validity,
            )

            seq = self.store.begin_trial(
                self.campaign_id, phase=self._phase, candidate=candidate,
                candidate_key=key, workload_hash=workload_hash,
                arch_hash=hashlib.sha256(key.encode()).hexdigest()[:16],
                strategy_kind=strategy, stage=stage)
            result = None
            if metrics is not None:
                modelled = (set(metrics) if analytic is True else set()
                            if analytic is False else set(analytic))
                result = Result(
                    metrics={k: Estimate(value=float(v), ci_low=float(v),
                                         ci_high=float(v), unit="",
                                         method=(Method.ANALYTIC if k in modelled
                                                 else Method.SIMULATED))
                             for k, v in metrics.items()},
                    validity=Validity(ok=True, checker_version=evaluator,
                                      violations=()),
                    domain=Domain(in_domain=True),
                    bottleneck=Bottleneck(limiter=Limiter.NONE),    # no claim (D440)
                    provenance=Provenance(evaluator=evaluator,
                                          inputs={"stage": stage}),
                    escalation=Escalation(recommended=False))
            self.store.complete_trial(
                self.campaign_id, seq, status="ok" if error is None else "refused",
                result=result, error=error, wall_clock_s=wall_s)
        except Exception:  # noqa: BLE001
            pass

    def known(self, *, stage: str, metric: str,
              want: Callable[[dict[str, Any]], bool] | None = None,
              higher_is_better: bool = True,
              ) -> list[tuple[dict[str, Any], float]]:
        """What this campaign already measured on `stage`: (candidate, best value) per
        key, best first -- resume means the record, read back (D367). `higher_is_better`
        says which way "best" points (a latency or an area is best when smallest)."""
        if self.store is None or not self.campaign_id:
            return []
        sign = 1.0 if higher_is_better else -1.0
        best: dict[str, tuple[dict[str, Any], float]] = {}
        try:
            for t in self.store.trials(self.campaign_id, status="ok"):
                if t.stage != stage or t.result is None:
                    continue
                cand = t.candidate or {}
                if want is not None and not want(cand):
                    continue
                est = t.result.metrics.get(metric)
                if est is None:
                    continue
                v = float(est.value)
                key = t.candidate_key
                if key not in best or sign * v > sign * best[key][1]:
                    best[key] = (cand, v)
        except Exception:  # noqa: BLE001
            return []
        return sorted(best.values(), key=lambda kv: -sign * kv[1])

    def known_rows(self, *, stage: str | None = None) -> list[TrialRow]:
        """Every measured candidate as typed rows (D445), oldest first; `stage` filters."""
        if self.store is None or not self.campaign_id:
            return []
        try:
            out = []
            for t in self.store.trials(self.campaign_id, status="ok"):
                if t.result is None or (stage is not None and t.stage != stage):
                    continue
                out.append(TrialRow(dict(t.candidate or {}), t.candidate_key, t.stage or "",
                                    {k: float(e.value) for k, e in t.result.metrics.items()}, "ok"))
            return out
        except Exception:  # noqa: BLE001
            return []

    def refusal_rows(self, *, stage: str | None = None, limit: int = 50) -> list[RefusalRow]:
        """Every refusal as typed rows (D445), newest last, capped at `limit`."""
        return [RefusalRow(c, "", why, stage or "") for c, why in self.refusals(stage=stage, limit=limit)]

    def conclusion_rows(self, limit: int = 5) -> list[ConclusionRow]:
        """Earlier runs' conclusions as typed rows (D445), newest first."""
        if self.store is None or not self.campaign_id:
            return []
        try:
            evs = [e for e in self.store.events(self.campaign_id) if e.get("kind") == "conclusion"]
            return [ConclusionRow({k: v for k, v in e.get("detail", {}).items() if k != "label"},
                                  str(e.get("detail", {}).get("label", "INFERENCE")), e.get("created_at"))
                    for e in reversed(evs)][:limit]
        except Exception:  # noqa: BLE001
            return []

    def stages(self) -> list[str]:
        """The stages this campaign has measured on, in first-seen order (D445): what a
        renderer reads instead of a literal."""
        seen: list[str] = []
        for row in self.known_rows():
            if row.stage and row.stage not in seen:
                seen.append(row.stage)
        return seen

    def metrics(self, *, stage: str | None = None) -> list[str]:
        """The metric names measured on `stage` (or anywhere), in first-seen order (D445)."""
        seen: list[str] = []
        for row in self.known_rows(stage=stage):
            for m in row.metrics:
                if m not in seen:
                    seen.append(m)
        return seen

    def refusals(self, *, stage: str | None = None, limit: int = 50
                 ) -> list[tuple[dict[str, Any], str]]:
        """What this campaign refused, with the reasons -- the cheapest teaching
        signal a search produces, read back so a resumed run's proposer is told what
        already failed instead of reproposing it. Newest last, capped at `limit`."""
        if self.store is None or not self.campaign_id:
            return []
        out: list[tuple[dict[str, Any], str]] = []
        try:
            for t in self.store.trials(self.campaign_id, status="refused"):
                if stage is not None and t.stage != stage:
                    continue
                out.append((t.candidate or {}, t.error or "refused"))
        except Exception:  # noqa: BLE001
            return []
        return out[-limit:]

    # ---- conclusions --------------------------------------------------------
    def conclude(self, conclusion: dict[str, Any]) -> None:
        """What this run decided the measurements meant -- INFERENCE, stored beside
        the data it was drawn from (D297), never mixed into it."""
        if self.store is None:
            return
        try:
            self.store.append_event(self.campaign_id, "conclusion",
                                    {"label": "INFERENCE", **conclusion})
        except Exception:  # noqa: BLE001
            pass

    def note(self, text: str) -> None:
        """A human note (D388): persisted, so a typed line never vanishes silently."""
        if self.store is None:
            return
        try:
            import time

            self.store.append_event(self.campaign_id, "human_note",
                                    {"text": text, "ts": time.time()})
        except Exception:  # noqa: BLE001
            pass

    def notes(self) -> list[str]:
        """Operator guidance already in this campaign's record, oldest first -- what a
        resumed run's proposer should still be told (the record, read back, D367)."""
        if self.store is None or not self.campaign_id:
            return []
        try:
            return [e["detail"]["text"] for e in self.store.events(self.campaign_id)
                    if e.get("kind") == "human_note" and e.get("detail", {}).get("text")]
        except Exception:  # noqa: BLE001
            return []

    def close(self, status: str) -> None:
        """Mark the campaign's final status; failures cost only the status line."""
        if self.store is not None:
            try:
                self.store.set_status(self.campaign_id, status)
                self.store.close()
            except Exception:  # noqa: BLE001
                pass

    def remember(self, kind: str, payload: dict[str, Any]) -> None:
        """A decision the orchestrator made that a resume must reuse -- a decomposition,
        a plan -- as a typed event (D431). Swallowed when there is no record."""
        if self.store is None:
            return
        try:
            import time

            self.store.append_event(self.campaign_id, f"decided:{kind}",
                                    {**payload, "ts": time.time()})
        except Exception:  # noqa: BLE001
            pass

    def recall(self, kind: str) -> list[dict[str, Any]]:
        """Every remembered decision of `kind`, oldest first; [] without a record."""
        if self.store is None or not self.campaign_id:
            return []
        try:
            return [dict(e["detail"]) for e in self.store.events(self.campaign_id)
                    if e.get("kind") == f"decided:{kind}"]
        except Exception:  # noqa: BLE001
            return []

    def conclusions(self, limit: int = 5) -> list[dict[str, Any]]:
        """Earlier runs' conclusions, newest first -- the next run starts informed
        instead of rediscovering the shape of the space."""
        if self.store is None or not self.campaign_id:
            return []
        try:
            evs = [e for e in self.store.events(self.campaign_id)
                   if e.get("kind") == "conclusion"]
            return [e.get("detail", {}) for e in reversed(evs)][:limit]
        except Exception:  # noqa: BLE001
            return []
