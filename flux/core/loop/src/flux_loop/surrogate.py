"""The evaluator's model half (D560, review 2 R9): a SURROGATE that predicts the costly
stage's numbers for candidates it has not measured, and uses the prediction to ORDER what is
sent up -- never to decide (D522: a decision stands on measured numbers).

    roles: {evaluator: surrogate}                       # or, with its keys:
    roles: {evaluator: {surrogate: {stage: confirm, kind: llm, k: 3}}}

`prepare` reads the record: every design the target stage measured, its knobs and numbers.
`order` predicts each candidate's numbers on that stage two ways -- the calibration first
(D464: a bias the loop measured between the candidate's own stage and the target, applied to
its shallow number), the record's nearest neighbours in knob space otherwise (`kind: fitted`),
or the model reading the measured table (`kind: llm`, the fitted half when the model fails) --
and hands the frontier back best-predicted first, so `request.finalists` of them are the ones
worth a placement. Every prediction is a row of the record on the stage `<stage>~predicted`,
tagged analytic with the evaluator `surrogate@<stage>` (D446): a prediction is never a
measurement, and a later pass can say how right it was.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .roles import register
from .types import Candidate, Scored

__all__ = ["Surrogate", "knobs_of", "predict_fit", "predict_knn"]


_NOT_KNOBS = {"name", "artifact", "meta", "subgoal", "knobs", "idea"}


def knobs_of(doc: dict[str, Any]) -> dict[str, Any]:
    """A recorded candidate's knobs: the loop writes them FLAT beside name/artifact/meta
    (`{"width": 1, "name": "w1", ...}`), `Candidate.to_record` nests them under `knobs`;
    both are read."""
    nested = doc.get("knobs")
    if isinstance(nested, dict) and nested:
        return dict(nested)
    return {k: v for k, v in doc.items() if k not in _NOT_KNOBS and not isinstance(v, (dict, list))}


def _numeric(v: Any) -> float | None:
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _distance(a: dict[str, Any], b: dict[str, Any], spans: dict[str, float]) -> float:
    """Knob-space distance: a numeric knob's difference over its span on the record, a
    categorical knob 0 or 1; a knob one side lacks counts 1."""
    d = 0.0
    for k in set(a) | set(b):
        if k not in a or k not in b:
            d += 1.0
            continue
        x, y = _numeric(a[k]), _numeric(b[k])
        if x is not None and y is not None:
            d += abs(x - y) / (spans.get(k) or 1.0)
        else:
            d += 0.0 if a[k] == b[k] else 1.0
    return d


def predict_knn(rows: list[tuple[dict[str, Any], dict[str, float]]], knobs: dict[str, Any],
                metric: str, k: int = 3) -> float | None:
    """The distance-weighted mean of `metric` over the `k` nearest measured designs, or None
    when nothing measured carries the metric."""
    have = [(kn, m[metric]) for kn, m in rows if metric in m]
    if not have:
        return None
    spans: dict[str, float] = {}
    for kn, _v in have:
        for name, v in kn.items():
            x = _numeric(v)
            if x is not None:
                lo, hi = spans.get(name + ":lo", x), spans.get(name + ":hi", x)
                spans[name + ":lo"], spans[name + ":hi"] = min(lo, x), max(hi, x)
    span = {name[:-3]: max(1e-9, spans[name] - spans[name[:-3] + ":lo"]) for name in spans if name.endswith(":hi")}
    ranked = sorted(((_distance(kn, knobs, span), v) for kn, v in have), key=lambda t: t[0])[:max(1, k)]
    if ranked[0][0] == 0.0:
        return ranked[0][1]
    weights = [1.0 / (d + 1e-6) for d, _v in ranked]
    return sum(w * v for w, (_d, v) in zip(weights, ranked)) / sum(weights)


def predict_fit(rows: list[tuple[dict[str, Any], dict[str, float]]], knobs: dict[str, Any],
                metric: str) -> float | None:
    """A least-squares plane over the NUMERIC knobs the record and the candidate share --
    the fit that extrapolates (a width the record never placed still places on the trend) --
    or None when the knobs are not all numeric, the rows are too few, or numpy is not there."""
    names = [k for k in knobs if _numeric(knobs[k]) is not None]
    if not names or len(names) != len(knobs):
        return None
    have = [(kn, m[metric]) for kn, m in rows if metric in m and all(_numeric(kn.get(k)) is not None for k in names)]
    if len(have) < len(names) + 2:
        return None
    try:
        import numpy as np

        x = np.array([[_numeric(kn[k]) for k in names] + [1.0] for kn, _v in have], dtype=float)
        y = np.array([v for _kn, v in have], dtype=float)
        coef, *_ = np.linalg.lstsq(x, y, rcond=None)
        return float(np.dot(np.array([_numeric(knobs[k]) for k in names] + [1.0]), coef))
    except Exception:  # noqa: BLE001 -- no numpy, a singular plane: the neighbours answer
        return None


@dataclass
class Surrogate:
    """The component. `stage` "" = the chain's last stage; `kind` fitted | llm."""

    name: str = "surrogate"
    stage: str = ""
    kind: str = "fitted"
    k: int = 3
    shown: int = 60
    _rows: list[tuple[dict[str, Any], dict[str, float]]] = field(default_factory=list, repr=False)
    _bias: dict[tuple[str, str], Any] = field(default_factory=dict, repr=False)   # (stage, metric) -> Bias, from the record

    # ---- the role's hooks the loop asks for (D461)
    def analytic(self) -> frozenset[str]:
        return frozenset()                    # it adds no stage to the chain; it orders

    def target(self, problem: Any) -> str:
        stages = list(problem.stages() or [])
        return self.stage or (stages[-1] if stages else "")

    def prepare(self, problem: Any, state: Any) -> None:
        """The record's measured designs on the target stage: the fit."""
        target = self.target(problem)
        self._rows = []
        if state.records is None or not target:
            return
        for row in state.records.known_rows(stage=target):
            knobs = knobs_of(row.candidate or {})
            if knobs and row.metrics:
                self._rows.append((knobs, dict(row.metrics)))
        self._bias = {}
        try:
            from .calibrate import Bias

            for doc in state.records.recall("calibration"):
                if doc.get("against") == target and doc.get("stage") and doc.get("metric"):
                    self._bias[(str(doc["stage"]), str(doc["metric"]))] = Bias(
                        metric=str(doc["metric"]), stage=str(doc["stage"]), against=target,
                        ratio=float(doc.get("ratio") or 1.0), spread=float(doc.get("spread") or 0.0), n=int(doc.get("n") or 0))
        except Exception:  # noqa: BLE001 -- a record without calibrations
            pass
        if self._rows or self._bias:
            state.say(f"  surrogate: {len(self._rows)} design(s) measured on {target}"
                      + (f" and {len(self._bias)} calibration(s)" if self._bias else "") + " fit the prediction")

    def order(self, problem: Any, front: list[Scored], state: Any, stage: str) -> list[Scored] | None:
        """`front` best-predicted first for `stage`, or None when there is nothing to
        predict with (no rows, no bias) -- the problem then chooses as before."""
        target = self.target(problem)
        if stage != target or not front:
            return None
        objs = list(problem.objectives() or [])
        if not objs:
            return None
        metrics = [o.metric for o in objs]
        predicted = self._predict(problem, front, state, stage, metrics)
        if predicted is None:
            return None
        lead = objs[0]
        sign = -1.0 if lead.direction == "maximize" else 1.0
        order = sorted(range(len(front)), key=lambda i: sign * predicted[i].get(lead.metric, float("inf")))
        for i in order:
            self._record(state, front[i].candidate, stage, predicted[i])
        state.say(f"  surrogate: {len(front)} candidate(s) ordered by their predicted {lead.metric} on {stage}"
                  + (" (the model's reading)" if self.kind == "llm" and self._used_model else ""))
        return [front[i] for i in order]

    # ---- the two halves
    _used_model: bool = False

    def _predict(self, problem, front, state, stage, metrics) -> list[dict[str, float]] | None:
        fitted = [self._fitted(c, state, stage, metrics) for c in front]
        if all(not p for p in fitted):
            return None
        self._used_model = False
        if self.kind == "llm" and state.proposer is not None:
            got = self._from_model(front, state, stage, metrics)
            if got is not None:
                self._used_model = True
                return [{**f, **g} for f, g in zip(fitted, got)]
        return fitted

    def _fitted(self, scored: Scored, state: Any, stage: str, metrics: list[str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for m in metrics:
            b = state.bias.get((scored.stage, m)) or self._bias.get((scored.stage, m))
            shallow = scored.metrics.get(m)
            if b is not None and getattr(b, "against", "") == stage and shallow is not None:
                out[m] = float(b.apply(shallow))                     # the calibration: the best predictor there is
                continue
            knobs = scored.candidate.knobs
            v = (predict_fit(self._rows, knobs, m) if knobs else None)
            if v is None and knobs:
                v = predict_knn(self._rows, knobs, m, self.k)
            if v is not None:
                out[m] = float(v)
        return out

    def _from_model(self, front, state, stage, metrics) -> list[dict[str, float]] | None:
        from .model import _ask, _json

        table = [f"  {json.dumps(kn)} -> " + ", ".join(f"{m} {v:g}" for m, v in ms.items() if m in metrics)
                 for kn, ms in self._rows[-int(self.shown):]]
        asks = [f"  {i}: {json.dumps(s.candidate.knobs)}" + (" (on " + s.stage + ": " + ", ".join(f"{m} {v:g}" for m, v in s.metrics.items() if m in metrics) + ")" if s.metrics else "")
                for i, s in enumerate(front)]
        prompt = (f"PREDICT what the {stage} stage will measure for each candidate below, from what it measured before. "
                  "Extrapolate the trend in the knobs; where a candidate has a number from a cheaper stage, correct it the "
                  "way the measured pairs suggest. Numbers only, no advice.\n\n"
                  f"MEASURED ON {stage} ({len(self._rows)} design(s)):\n" + ("\n".join(table) or "  nothing yet")
                  + "\n\nCANDIDATES:\n" + "\n".join(asks)
                  + "\n\nReply as JSON: {\"predictions\": [{\"index\": i, " + ", ".join(f"\"{m}\": number" for m in metrics) + "}, ...]}")
        schema = {"type": "object", "properties": {"predictions": {"type": "array", "items": {"type": "object"}}},
                  "required": ["predictions"]}
        try:
            doc = _json(_ask(state, prompt, schema).text)
        except Exception as exc:  # noqa: BLE001 -- the fitted half stands in
            state.say(f"  surrogate: the model's prediction did not run ({exc!s:.100}); the fitted half orders")
            return None
        out: list[dict[str, float]] = [{} for _ in front]
        for p in (doc or {}).get("predictions", []) if isinstance(doc, dict) else []:
            try:
                i = int(p.get("index"))
            except (TypeError, ValueError, AttributeError):
                continue
            if 0 <= i < len(front):
                for m in metrics:
                    v = _numeric(p.get(m))
                    if v is not None:
                        out[i][m] = v
        return out if any(out) else None

    def _record(self, state: Any, cand: Candidate, stage: str, predicted: dict[str, float]) -> None:
        if state.records is None or not predicted:
            return
        try:
            doc = {**cand.knobs, "name": cand.name, "artifact": cand.artifact, "meta": dict(cand.meta)}   # flat, as the loop writes
            state.records.trial(doc, f"{cand.key()}:{stage}:predicted", stage=f"{stage}~predicted",
                                strategy="surrogate", metrics=dict(predicted), analytic=True,
                                evaluator=f"surrogate@{stage}")
        except Exception:  # noqa: BLE001 -- a prediction that could not be written is still used
            pass


def _factory(config: dict[str, Any]) -> Surrogate:
    cfg = {k: v for k, v in (config or {}).items() if k in ("stage", "kind", "k", "shown")}
    if cfg.get("kind", "fitted") not in ("fitted", "llm"):
        raise ValueError(f"surrogate kind is fitted or llm, not {cfg['kind']!r}")
    return Surrogate(**cfg)


register("evaluator", "surrogate", _factory)
