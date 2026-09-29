"""Stage estimators (D665): an estimate of a stage's metrics before its tool runs on a design,
a pre-gate the stage turns on with `estimate:` (off by default).

    stages:
      - {name: place, command: "...", metrics: [fmax_mhz, area_um2],
         estimate: {kind: surrogate, margin: 0.05}}

A design whose estimate fails the stage's own cutoff, or an objective's limit on that stage, by
more than `margin` (relative to the threshold) is skipped for the stage: refused with the
estimate as the reason, and the tool never runs on it. Otherwise the tool runs. No estimate
(too few rows, no model, a command that printed nothing) means the tool runs.

    surrogate  the record's rows on this stage: a least-squares plane over the knobs, else the
               nearest measured designs; under MIN_ROWS rows it estimates nothing
    command    a script with the stage's placeholders printing the same `name=value` metrics
    model      the loop's model, shown each design and the stage's measured rows, replies JSON
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

__all__ = ["KINDS", "MIN_ROWS", "Estimator", "by_model", "by_surrogate", "failing", "knobs_of", "measured_rows",
           "predict_fit", "predict_knn"]

KINDS = ("surrogate", "command", "model")
MIN_ROWS = 3          # the surrogate estimates nothing from fewer measured designs


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
    """A least-squares plane over the numeric knobs the record and the candidate share (it
    extrapolates along the trend), or None when the knobs are not all numeric, the rows are
    too few, or numpy is missing."""
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


@dataclass(frozen=True)
class Estimator:
    kind: str
    margin: float = 0.05
    command: tuple[str, ...] | None = None

    def to_doc(self) -> dict[str, Any]:
        return {"kind": self.kind, "margin": self.margin, **({"command": list(self.command)} if self.command else {})}

    def describe(self) -> str:
        how = {"surrogate": f"a fit over the record's rows on this stage, from {MIN_ROWS} rows",
               "command": "the estimate command", "model": "the model, from the design and the stage's rows"}[self.kind]
        return f"{self.kind} ({how}); skipped when it fails a cutoff or limit by more than {self.margin:.0%}"


def measured_rows(state: Any, stage: str) -> list[tuple[dict[str, Any], dict[str, float]]]:
    """(knobs, metrics) of every design measured on `stage`: the record's rows, else this pass's."""
    rows: list[tuple[dict[str, Any], dict[str, float]]] = []
    if state.records is not None:
        try:
            for row in state.records.known_rows(stage=stage):
                knobs = knobs_of(row.candidate or {})
                if knobs and row.metrics:
                    rows.append((knobs, dict(row.metrics)))
        except Exception:  # noqa: BLE001 -- a record that cannot be read estimates nothing
            rows = []
    if not rows:
        rows = [(dict(s.candidate.knobs), dict(s.metrics)) for s in state.scored
                if s.stage == stage and s.candidate.knobs and s.metrics]
    return rows


def by_surrogate(rows: list[tuple[dict[str, Any], dict[str, float]]], knobs: dict[str, Any],
                 metrics: list[str], k: int = 3) -> dict[str, float] | None:
    """The fit's estimate per metric, or None with too few rows or no knobs."""
    if len(rows) < MIN_ROWS or not knobs:
        return None
    out: dict[str, float] = {}
    for m in metrics:
        v = predict_fit(rows, knobs, m)
        if v is None:
            v = predict_knn(rows, knobs, m, k)
        if v is not None:
            out[m] = float(v)
    return out or None


def by_model(state: Any, stage: str, cands: list[Any], metrics: list[str],
             rows: list[tuple[dict[str, Any], dict[str, float]]], shown: int = 60) -> list[dict[str, float] | None]:
    """One model turn estimating every candidate; None per candidate the model left out, all
    None without a model or a usable reply."""
    none: list[dict[str, float] | None] = [None] * len(cands)
    if state.proposer is None or not cands:
        return none
    from .model import _ask, _json

    def design(c: Any) -> str:
        return json.dumps(c.knobs) if c.knobs else (c.artifact or "")[:3000]

    table = [f"  {json.dumps(kn)} -> " + ", ".join(f"{m} {v:g}" for m, v in ms.items() if m in metrics)
             for kn, ms in rows[-int(shown):]]
    asks = [f"  {i}: {design(c)}" for i, c in enumerate(cands)]
    prompt = (f"ESTIMATE what the {stage} stage will measure ({', '.join(metrics)}) for each design below, "
              "from what it measured before. Numbers only, no advice.\n\n"
              f"MEASURED ON {stage} ({len(rows)} design(s)):\n" + ("\n".join(table) or "  nothing yet")
              + "\n\nDESIGNS:\n" + "\n".join(asks)
              + "\n\nReply as JSON: {\"estimates\": [{\"index\": i, " + ", ".join(f"\"{m}\": number" for m in metrics) + "}, ...]}")
    schema = {"type": "object", "properties": {"estimates": {"type": "array", "items": {"type": "object"}}},
              "required": ["estimates"]}
    try:
        doc = _json(_ask(state, prompt, schema).text)
    except Exception as exc:  # noqa: BLE001 -- no estimate: the tool runs
        state.say(f"  estimate {stage}: the model did not answer ({exc!s:.100}); the tool runs")
        return none
    out = list(none)
    for e in (doc or {}).get("estimates", []) if isinstance(doc, dict) else []:
        try:
            i = int(e.get("index"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 0 <= i < len(cands):
            got = {m: float(e[m]) for m in metrics
                   if isinstance(e.get(m), (int, float)) and not isinstance(e.get(m), bool)}
            out[i] = got or None
    return out


def failing(estimate: dict[str, float], rules: list[tuple[str, str, float, str]], margin: float) -> str:
    """Why this estimate is skipped, or "": the first rule (metric, ">=" | "<=", threshold,
    what) it fails by more than `margin` of the threshold."""
    for metric, op, x, what in rules:
        v = estimate.get(metric)
        if v is None:
            continue
        slack = margin * abs(x)
        if (op == ">=" and v < x - slack) or (op == "<=" and v > x + slack):
            return f"estimated {metric} {v:g} fails {metric} {op} {x:g} ({what}) by more than {margin:.0%}"
    return ""
