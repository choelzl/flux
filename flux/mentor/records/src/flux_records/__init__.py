"""The mentor's record layer: results and conclusions, as semantics over the store (D397).

`core/stores` is the storage engine; this is what a loop MEANS by it. Records hold two
distinct things and keep them distinct: MEASUREMENTS (trials with their rung, status
and metrics) and CONCLUSIONS (what a run decided the measurements meant, stored as
events labelled INFERENCE, the D297 posture). Extract (`flux_extract`) then builds
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
from typing import Any, Callable

__all__ = ["Records"]


class Records:
    """One campaign's results and conclusions in a CampaignStore, resumable."""

    def __init__(self, db: str, objective: dict[str, Any],
                 log: Callable[[str], None] | None = None) -> None:
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
            self.campaign_id, created = self.store.start_campaign(objective, digest)
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

    def trial(self, candidate: dict[str, Any], key: str, *, rung: str,
              strategy: str, metrics: dict[str, float] | None,
              error: str | None = None, wall_s: float = 0.0,
              analytic: bool = True, evaluator: str = "flux@records",
              workload_hash: str = "") -> None:
        """One measured or refused candidate. `metrics=None` with `error` = a refusal."""
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
                mapping_hash=None, strategy_kind=strategy, seed=None,
                deterministic=True, rung=rung)
            result = None
            if metrics is not None:
                method = Method.ANALYTIC if analytic else Method.SIMULATED
                result = Result(
                    metrics={k: Estimate(value=float(v), ci_low=float(v),
                                         ci_high=float(v), unit="", method=method)
                             for k, v in metrics.items()},
                    validity=Validity(ok=True, checker_version=evaluator,
                                      violations=()),
                    domain=Domain(in_domain=True),
                    bottleneck=Bottleneck(limiter=Limiter.COMPUTE),
                    provenance=Provenance(evaluator=evaluator,
                                          inputs={"rung": rung}),
                    escalation=Escalation(recommended=False))
            self.store.complete_trial(
                self.campaign_id, seq, status="ok" if error is None else "refused",
                result=result, error=error, wall_clock_s=wall_s)
        except Exception:  # noqa: BLE001
            pass

    def known(self, *, rung: str, metric: str,
              want: Callable[[dict[str, Any]], bool] | None = None
              ) -> list[tuple[dict[str, Any], float]]:
        """What this campaign already measured on `rung`: (candidate, best value) per
        key, best first -- resume means the record, read back (D367)."""
        if self.store is None or not self.campaign_id:
            return []
        best: dict[str, tuple[dict[str, Any], float]] = {}
        try:
            for t in self.store.trials(self.campaign_id, status="ok"):
                if t.rung != rung or t.result is None:
                    continue
                cand = t.candidate or {}
                if want is not None and not want(cand):
                    continue
                est = t.result.metrics.get(metric)
                if est is None:
                    continue
                v = float(est.value)
                key = t.candidate_key
                if key not in best or v > best[key][1]:
                    best[key] = (cand, v)
        except Exception:  # noqa: BLE001
            return []
        return sorted(best.values(), key=lambda kv: -kv[1])

    def refusals(self, *, rung: str | None = None, limit: int = 50
                 ) -> list[tuple[dict[str, Any], str]]:
        """What this campaign refused, with the reasons -- the cheapest teaching
        signal a search produces, read back so a resumed run's proposer is told what
        already failed instead of reproposing it. Newest last, capped at `limit`."""
        if self.store is None or not self.campaign_id:
            return []
        out: list[tuple[dict[str, Any], str]] = []
        try:
            for t in self.store.trials(self.campaign_id, status="refused"):
                if rung is not None and t.rung != rung:
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
