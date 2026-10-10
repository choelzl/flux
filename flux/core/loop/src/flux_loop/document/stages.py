"""The costed stages (D454): `flow.measure`, their metrics, cutoffs and estimators."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..estimate import KINDS as ESTIMATE_KINDS, Estimator
from ..metrics import AGGREGATES
from .commands import _command, _flux_program_tools
from .keys import TaskError


@dataclass(frozen=True)
class Stage:
    """One costed measurement: a command whose output carries the metrics (`metrics_re`,
    one float group each), or an evaluator named in the ABI registry applied to the
    artifact read as an architecture document.

    `cutoff` is what is worth the next stage (D454): one of `{"metric": m, "at": x}` (a floor),
    `{"metric": m, "below": x}` (a budget) or `{"metric": m, "within": f}` (a band around this
    run's best, `f` a fraction), or a list of them, all of which a design must pass, in order
    (D657). Without one, only the last stage's results decide.

    `estimate` (D665, off by default) predicts the stage's metrics before its tool runs; a design
    whose estimate fails the cutoff or an objective's limit by more than its margin is skipped."""

    name: str
    command: tuple[str, ...] | None = None
    metrics_re: dict[str, str] = field(default_factory=dict)
    metrics: tuple[str, ...] = ()
    timeout_s: float = 600.0
    cutoff: dict[str, Any] | tuple[dict[str, Any], ...] = field(default_factory=dict)   # one gate, or several
    needs: tuple[str, ...] = ()          # tools on PATH the stage wants; absent, the stage is skipped (D519)
    estimate: Estimator | None = None    # the pre-gate before the tool (D665)
    metric_specs: dict[str, dict[str, str]] = field(default_factory=dict)

    def reports(self, metric: str) -> bool:
        if metric in self.metric_specs and self.metric_specs[metric].get("type") == "dict":
            return self.metric_specs[metric].get("aggregate") in AGGREGATES
        return metric in {*self.metrics, *self.metrics_re} or any(
            spec.get("type") == "dict" and metric.startswith(name + ".") and len(metric) > len(name) + 1
            for name, spec in self.metric_specs.items())

    def metric_doc(self) -> list[Any]:
        return [{"name": m, **self.metric_specs[m]} if m in self.metric_specs else m for m in self.metrics]

    @property
    def cutoffs(self) -> tuple[dict[str, Any], ...]:
        """The stage's gates in order: the single-dict form is one."""
        return (self.cutoff,) if isinstance(self.cutoff, dict) else tuple(self.cutoff)


def _stage(i: int, doc: Any) -> Stage:
    if not isinstance(doc, dict) or not isinstance(doc.get("name"), str) or not doc["name"]:
        raise TaskError(f"flow.measure: stage {i + 1} needs a name")
    at = f"flow.measure.{doc['name']}"                 # D775: a stage is said by its name
    if "evaluator" in doc:                             # D954: no evaluator stages
        raise TaskError(f"{at}.evaluator: evaluator stages are gone (D954); measure with a command that prints "
                        "name=value, e.g. an application's own script")
    cmd = doc.get("command")
    if not cmd:
        raise TaskError(f"{at} needs a `command`")
    needs = doc.get("needs")
    if needs is not None and (not isinstance(needs, list) or not all(isinstance(t, str) for t in needs)):
        raise TaskError(f"{at}.needs is a list of tool names")
    needs = list(needs or [])
    cmd = _command(cmd, f"{at}.command")
    if "needs" not in doc and cmd:
        needs.extend(_flux_program_tools(cmd))
    raw_metrics = doc.get("metrics") or ()
    if not isinstance(raw_metrics, (list, tuple)):
        raise TaskError(f"{at}.metrics is a list of names or {{name, type: number|dict, direction, unit, aggregate}}")
    metrics, metric_specs = [], {}
    for item in raw_metrics:
        if isinstance(item, dict):
            name = item.get("name")
            spec = {k: v for k, v in item.items() if k != "name"}
            if set(spec) - {"type", "direction", "unit", "aggregate"} or spec.get("type", "number") not in ("number", "dict"):
                raise TaskError(f"{at}.metrics: use {{name, type: number|dict, direction, unit, aggregate}}")
            if "direction" in spec and spec["direction"] not in ("minimize", "maximize"):
                raise TaskError(f"{at}.metrics: direction must be minimize or maximize")
            if "unit" in spec and not isinstance(spec["unit"], str):
                raise TaskError(f"{at}.metrics: unit must be text")
            if spec.get("type") == "dict":
                spec.setdefault("aggregate", "mean")
            if "aggregate" in spec and (spec.get("type") != "dict" or spec["aggregate"] not in (*AGGREGATES, "none")):
                raise TaskError(f"{at}.metrics: a dictionary aggregate is one of {', '.join(AGGREGATES)}, none")
        else:
            name, spec = item, None
        if not isinstance(name, str) or not name.strip() or name == "_metric_groups" or name in metrics:
            raise TaskError(f"{at}.metrics: names must be non-empty and unique")
        metrics.append(name)
        if spec is not None:
            metric_specs[name] = spec
    for parent, spec in metric_specs.items():
        if spec.get("type") == "dict" and any(m.startswith(parent + ".") for m in metrics):
            raise TaskError(f"{at}.metrics: dictionary {parent!r} already declares its submetrics; do not also declare {parent}.test")
    metrics = tuple(metrics)
    metrics_re = dict(doc.get("metrics_re") or {})
    if cmd and not metrics_re and metrics:
        # `name=value` tokens need only the `metrics:` names
        # (D580); a token starts a line or follows whitespace, so `area_um2` never reads `xarea_um2`
        metrics_re = {m: rf"(?:^|(?<=\s)){re.escape(m)}=" +
                     (r"(\{[^\n]*\})" if metric_specs.get(m, {}).get("type") == "dict" else r"([-+0-9.eE]+)")
                     for m in metrics}
    for m, pat in metrics_re.items():
        try:
            if re.compile(pat).groups < 1:
                raise TaskError(f"{at}.metrics_re[{m!r}] needs one capturing group")
        except re.error as exc:
            raise TaskError(f"{at}.metrics_re[{m!r}] is not a regex: {exc}") from exc
    if cmd and not metrics_re:
        raise TaskError(f"{at}: a command stage needs `metrics` (names the command "
                        "prints as `name=value` lines) or `metrics_re` (a regex per metric)")
    raw = doc.get("cutoff") or {}
    if isinstance(raw, dict):
        cutoff: dict[str, Any] | tuple[dict[str, Any], ...] = dict(raw)
        named = [(f"{at}.cutoff", cutoff)] if cutoff else []
    elif isinstance(raw, list) and all(isinstance(c, dict) for c in raw):
        cutoff = tuple(dict(c) for c in raw)        # several gates, all must pass (D657)
        named = [(f"{at}.cutoff[{j}]", c) for j, c in enumerate(cutoff)]
    else:
        raise TaskError(f"{at}.cutoff is one condition {{metric, at|below|within}} or a list of them")
    for where, rule in named:
        if not isinstance(rule.get("metric"), str):
            raise TaskError(f"{where} needs a `metric` naming one this stage measures")
        rules = [k for k in ("at", "below", "within") if k in rule]
        if len(rules) != 1:
            raise TaskError(
                f"{where} needs exactly one of `at` (a floor), `below` (a budget) or "
                f"`within` (a fraction of this run's best), got {sorted(rule)}")
        if not isinstance(rule[rules[0]], (int, float)) or isinstance(rule[rules[0]], bool):
            raise TaskError(f"{where}.{rules[0]} must be a number")
        if rules[0] == "within" and not 0 < float(rule["within"]) <= 1:
            raise TaskError(f"{where}.within must be a fraction in (0, 1]")
    return Stage(name=doc["name"], command=cmd, metrics_re=metrics_re,
                metrics=metrics or tuple(metrics_re),
                timeout_s=float(doc.get("timeout_s") or 600.0), cutoff=cutoff, needs=tuple(needs),
                estimate=_estimator(at, doc.get("estimate")), metric_specs=metric_specs)


def _estimator(at: str, raw: Any) -> Estimator | None:
    """`flow.measure.<stage>.estimate` (D665): `{kind: surrogate|command|model, margin: 0.05, command: ...}`,
    `command` for kind command only."""
    if raw is None:
        return None
    where = f"{at}.estimate"
    if not isinstance(raw, dict):
        raise TaskError(f"{where} is {{kind: {'|'.join(ESTIMATE_KINDS)}, margin: 0.05}}")
    bad = sorted(set(raw) - {"kind", "margin", "command"})
    if bad:
        raise TaskError(f"{where} keys {bad} are not known; known: kind, margin, command")
    kind = raw.get("kind")
    if kind not in ESTIMATE_KINDS:
        raise TaskError(f"{where}.kind is one of {', '.join(ESTIMATE_KINDS)}, not {kind!r}")
    margin = raw.get("margin", 0.05)
    if isinstance(margin, bool) or not isinstance(margin, (int, float)) or margin < 0:
        raise TaskError(f"{where}.margin is a number >= 0 (a fraction of the threshold), not {margin!r}")
    if (kind == "command") != ("command" in raw):
        raise TaskError(f"{where}.command is said for kind command, and only then")
    cmd = _command(raw["command"], f"{where}.command") if kind == "command" else None
    return Estimator(kind, float(margin), cmd)
