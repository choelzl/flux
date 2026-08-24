"""Evaluation's AI half: a screening stage whose numbers come from a model FITTED ON THIS
CAMPAIGN'S OWN MEASURED TRIALS (D461).

The evaluation role always had two non-AI kinds -- analytical models and real tools -- and
Cedric's fourth requirement is the third kind: "AI Model (not necessarily LLM) and real tools".
This is that, in the only form that costs nothing to trust: a surrogate over what this campaign
has already MEASURED, used to order candidates before the expensive stages run.

Three rules make it safe to have at all, and they are not negotiable inside this module:

1. **It is never the last stage.** A learned number orders candidates; it never answers. The
   component refuses to be a problem's only stage, and it inserts itself FIRST, below whatever
   real stages the problem declares -- so the loop's own chain rule (the decision is made on the
   highest stage with results, and a run that got no further says so) does the rest.
2. **It is tagged analytic in the record**, like every other modelled number (D446), so a reader
   asking the record what was measured never gets a prediction back.
3. **It says how much to trust it.** Every run reports what it was fitted on -- how many measured
   points, from which stage -- and its leave-one-out mean absolute error on those points.

Cold start is handled by NOT EXISTING: with fewer than `min_points` measured trials on record the
stage does not join the chain at all, and the run says so. The first runs are plain (real tools
only), they fill the record, and a later run of the same campaign gets the screen for free.

The model is k-nearest-neighbour with inverse-distance weighting over the z-scored numeric knobs
(`flux_extract.numeric_knobs`, the repository's one definition of a candidate's numeric view).
Deliberately chosen over a fitted linear model: kNN cannot predict a value outside the range it
has actually seen measured, so a screen built from four points cannot invent a fifth that beats
everything ever built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["Surrogate"]


@dataclass
class Surrogate:
    """A learned screening stage. `metric` is what it predicts -- the same name the real stage
    reports, because a modelled `fmax_mhz` is still an `fmax_mhz` (the method tag is what says
    it was modelled, not the name). `trained_on` is the stage whose measured numbers fit it
    (default: the problem's last, the one that answers)."""

    metric: str
    trained_on: str = ""
    stage: str = "learned"
    min_points: int = 4
    neighbours: int = 3
    within: float | None = None          # a cutoff on its own stage: a band around its own best
    higher_is_better: bool = True
    name: str = "learned"

    _keys: tuple[str, ...] = field(default_factory=tuple, repr=False)
    _rows: list[tuple[list[float], float]] = field(default_factory=list, repr=False)
    _mean: dict[str, float] = field(default_factory=dict, repr=False)
    _scale: dict[str, float] = field(default_factory=dict, repr=False)
    _error: float | None = field(default=None, repr=False)
    _from: str = field(default="", repr=False)
    _asked: bool = field(default=False, repr=False)

    # ------------------------------------------------------------------ fitting
    def prepare(self, problem: Any, state: Any) -> None:
        """Fit from the record, once per run, before the chain is asked for its shape."""
        self._asked = True
        self._rows = []
        self._error = None
        target = self.trained_on or _last_stage(problem)
        self._from = target
        rows = _measured(state, target, self.metric)
        say = state.say
        if len(rows) < max(2, self.min_points):
            say(f"  the learned screen is not in this chain: {len(rows)} measured "
                f"{self.metric} point(s) on the {target} stage, {self.min_points} needed")
            return
        keys = sorted({k for knobs, _v in rows for k in knobs})
        if not keys:
            say(f"  the learned screen is not in this chain: the {target} stage's candidates "
                f"carry no numeric knobs to predict from")
            return
        self._keys = tuple(keys)
        self._mean = {k: _mean([knobs.get(k) for knobs, _v in rows]) for k in keys}
        self._scale = {k: _spread([knobs.get(k) for knobs, _v in rows], self._mean[k])
                       for k in keys}
        self._rows = [(self._features(knobs), value) for knobs, value in rows]
        self._error = self._leave_one_out()
        state.lessons.append(
            f"[{self.stage}] the learned screen was fitted on {len(self._rows)} measured "
            f"{self.metric} point(s) from the {target} stage; leave-one-out mean error "
            f"{self._error:.3g} ({self._error / max(1e-9, abs(_mean([v for _f, v in self._rows]))):.0%} "
            f"of their mean). It ORDERS candidates; it never answers.")
        say(f"  learned screen: {len(self._rows)} point(s) from {target}, "
            f"leave-one-out error {self._error:.3g}")

    @property
    def fitted(self) -> bool:
        return bool(self._rows)

    # ------------------------------------------------------------------ the chain
    def stages(self, problem: Any, own: list[str]) -> list[str]:
        """The problem's chain with the learned screen FIRST -- or unchanged when the model
        has nothing to stand on."""
        if not own:
            raise ValueError(
                "a learned stage ORDERS candidates and never answers, so it cannot be a "
                "problem's only stage: declare the real stage that measures the answer")
        if self.stage in own:
            raise ValueError(f"the learned stage's name {self.stage!r} is already a stage of this "
                             f"problem; give the component another `stage` name")
        if not self.fitted:
            return list(own)
        return [self.stage, *own]

    def claims(self, stage: str) -> bool:
        return stage == self.stage

    def uncached(self) -> frozenset[str]:
        """Its stage is never cached (D461): the model refits every run as the record grows,
        so a cached prediction would be an older, smaller model's answer."""
        return frozenset({self.stage})

    def analytic(self) -> frozenset[str]:
        """Its stage's numbers are modelled, and the record says so (D446)."""
        return frozenset({self.stage})

    def evaluator_name(self, stage: str) -> str | None:
        if not self.claims(stage):
            return None
        return f"learned:{self.metric}@{self._from or 'record'}"

    def measure(self, problem: Any, cand: Any, stage: str, state: Any) -> dict[str, Any] | None:
        """Predict, in seconds, from what this campaign has measured. None = not my stage."""
        if not self.claims(stage):
            return None
        if not self.fitted:
            return {"error": "the learned screen was not fitted; it should not be a stage"}
        from flux_extract import numeric_knobs

        knobs = numeric_knobs(dict(cand.knobs))
        value, used = self._predict(self._features(knobs))
        # One metric and one sentence: a count in the metrics dict would land in the record
        # as a metric of its own, and `neighbours` is not something this stage measured.
        return {self.metric: value, "predicted": True,
                "predicted_from": (f"{used} nearest of {len(self._rows)} measured point(s), "
                                   f"{len(knobs)} feature(s) of {len(self._keys)}")}

    def cutoff(self, stage: str, scored: list[Any], state: Any) -> Any | None:
        """A band around its own best, when the caller asked for one: what a screen is FOR."""
        if not self.claims(stage) or self.within is None:
            return None
        from .cutoff import within_best

        return within_best(scored, self.metric, float(self.within),
                           higher_is_better=self.higher_is_better)

    # ------------------------------------------------------------------ the model
    def _features(self, knobs: dict[str, float]) -> list[float]:
        """z-scored, with a knob this candidate does not carry taking the training mean --
        a neutral value, so a missing feature moves the prediction nowhere."""
        return [((knobs.get(k, self._mean[k]) - self._mean[k]) / self._scale[k])
                for k in self._keys]

    def _predict(self, point: list[float], skip: int = -1) -> tuple[float, int]:
        k = max(1, min(self.neighbours, len(self._rows) - (1 if skip >= 0 else 0)))
        near = sorted(((_distance(point, f), v) for i, (f, v) in enumerate(self._rows)
                       if i != skip), key=lambda pair: pair[0])[:k]
        if not near:
            return 0.0, 0
        if near[0][0] <= 1e-12:                 # the same design, measured: its own number
            return near[0][1], 1
        weights = [1.0 / d for d, _v in near]
        return sum(w * v for w, (_d, v) in zip(weights, near)) / sum(weights), len(near)

    def _leave_one_out(self) -> float:
        """Mean absolute error predicting each measured point from the others -- the only
        honest thing to say about a model fitted on this little data."""
        errors = [abs(self._predict(f, skip=i)[0] - v) for i, (f, v) in enumerate(self._rows)]
        return sum(errors) / len(errors) if errors else 0.0


# --------------------------------------------------------------------------- helpers
def _measured(state: Any, stage: str, metric: str) -> list[tuple[dict[str, float], float]]:
    """(numeric knobs, metric) for every candidate this campaign MEASURED on `stage`."""
    from flux_extract import numeric_knobs

    records = getattr(state, "records", None)
    if records is None:
        return []
    try:
        rows = records.known_rows(stage=stage)
    except Exception:  # noqa: BLE001 -- a record that cannot be read leaves the screen out
        return []
    out = []
    for row in rows:
        value = row.metrics.get(metric)
        if value is None:
            continue
        knobs = numeric_knobs(dict(row.candidate or {}))
        if knobs:
            out.append((knobs, float(value)))
    return out


def _last_stage(problem: Any) -> str:
    """The stage a problem's answer comes from: the last of its own, the screen by default."""
    try:
        own = [r for r in problem.stages() if r != getattr(problem.roles().evaluator, "stage", "")]
    except Exception:  # noqa: BLE001
        own = []
    return own[-1] if own else "screen"


def _mean(values: list[float | None]) -> float:
    real = [v for v in values if v is not None]
    return sum(real) / len(real) if real else 0.0


def _spread(values: list[float | None], mean: float) -> float:
    """Standard deviation, floored so a knob that never varies cannot divide by zero."""
    real = [v for v in values if v is not None]
    if len(real) < 2:
        return 1.0
    var = sum((v - mean) ** 2 for v in real) / (len(real) - 1)
    return max(var ** 0.5, 1e-9)


def _distance(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
