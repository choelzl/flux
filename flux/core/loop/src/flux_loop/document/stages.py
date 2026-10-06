"""The costed stages (D454): `flow.measure`, their metrics, cutoffs and estimators."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..estimate import KINDS as ESTIMATE_KINDS, Estimator
from .commands import RTL_METRICS, RTL_STAT_METRICS, _command, _flux_rtl_tools, _stage_of, rtl_tools_kind
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
    evaluator: str | None = None
    metrics: tuple[str, ...] = ()
    timeout_s: float = 600.0
    cutoff: dict[str, Any] | tuple[dict[str, Any], ...] = field(default_factory=dict)   # one gate, or several
    needs: tuple[str, ...] = ()          # tools on PATH the stage wants; absent, the stage is skipped (D519)
    estimate: Estimator | None = None    # the pre-gate before the tool (D665)

    @property
    def cutoffs(self) -> tuple[dict[str, Any], ...]:
        """The stage's gates in order: the single-dict form is one."""
        return (self.cutoff,) if isinstance(self.cutoff, dict) else tuple(self.cutoff)


def _stage(i: int, doc: Any) -> Stage:
    if not isinstance(doc, dict) or not isinstance(doc.get("name"), str) or not doc["name"]:
        raise TaskError(f"flow.measure: stage {i + 1} needs a name")
    at = f"flow.measure.{doc['name']}"                 # D775: a stage is said by its name
    cmd, ev = doc.get("command"), doc.get("evaluator")
    if cmd and ev:
        raise TaskError(f"{at} needs exactly one of `command` or `evaluator`, not both")
    if not cmd and not ev:
        raise TaskError(f"{at} needs exactly one of `command` or `evaluator`")
    needs = doc.get("needs")
    if needs is not None and (not isinstance(needs, list) or not all(isinstance(t, str) for t in needs)):
        raise TaskError(f"{at}.needs is a list of tool names")
    needs = list(needs or [])
    cmd = _command(cmd, f"{at}.command")
    rtl_tools = _flux_rtl_tools(cmd) if cmd else []
    for tool in rtl_tools if "needs" not in doc else ():    # D628: `flux rtl measure` says what it runs
        needs.append(tool)
    metrics = tuple(doc.get("metrics") or (() if "measure" not in rtl_tools_kind(cmd)
                                           else RTL_STAT_METRICS if _stage_of(list(cmd)) == "stat" else RTL_METRICS))
    if not all(isinstance(m, str) for m in metrics):
        raise TaskError(f"{at}.metrics is a list of metric names")
    metrics_re = dict(doc.get("metrics_re") or {})
    if cmd and not metrics_re and metrics:
        # `name=value` tokens (as `flux rtl measure` prints) need only the `metrics:` names
        # (D580); a token starts a line or follows whitespace, so `area_um2` never reads `xarea_um2`
        metrics_re = {m: rf"(?:^|(?<=\s)){re.escape(m)}=([-+0-9.eE]+)" for m in metrics}
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
                evaluator=ev, metrics=metrics or tuple(metrics_re),
                timeout_s=float(doc.get("timeout_s") or 600.0), cutoff=cutoff, needs=tuple(needs),
                estimate=_estimator(at, doc.get("estimate")))


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
