"""Evaluator ABI v0.1 types (docs/evaluator-abi.md). Any cost model that implements the `Evaluator`
protocol (see protocol.py) becomes swappable behind these types; any search strategy that speaks
them becomes portable across evaluators.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Union

# A workload/arch/mapping reference is either a content hash (str, see flux_ir.content_hash)
# pointing at something already in the Result/artifact store, or an inline IR document (dict) to
# be hashed on first use. mapping=None means "the evaluator may choose one, and must declare that
# it did" (docs/evaluator-abi.md).
WorkloadRef = Union[str, dict[str, Any]]
# `arch=None` means "use the evaluator's own default architecture" (docs/decisions.md D172, D173).
# This was annotated non-Optional, which was simply wrong about the code around it: the
# `workload_dynamism` sweeps construct `Candidate(..., arch=None)`, and every registered backend
# already handles it deliberately — rtl, systemc, timeloop and zigzag fall back to their own
# default accelerator, and the other eight refuse with `NotExpressibleError` naming the
# requirement. Neither behaviour is a bug; both are the ABI's "honour it or refuse loudly"
# posture. The annotation was the only part that disagreed.
ArchRef = Union[str, dict[str, Any], None]
MappingRef = Union[str, dict[str, Any], None]


class Method(str, Enum):
    ANALYTIC = "analytic"
    SIMULATED = "simulated"
    MEASURED = "measured"


class Limiter(str, Enum):
    MEMORY = "memory"
    COMPUTE = "compute"
    NOC = "noc"
    DEPENDENCY = "dependency"
    THERMAL = "thermal"  # docs/decisions.md D64 — evaluators/thermal's real 3D-ICE-backed evaluator


class Metric(str, Enum):
    """Well-known metric names (docs/evaluator-abi.md). Not exhaustive by design: general-SoC
    evaluators (docs/decisions.md D1) may report metric names outside this set — `metrics` on
    a call and `Result.metrics` keys are plain strings; this enum is a shared vocabulary for the
    common cases, not an enforced whitelist.
    """

    LATENCY_CYCLES = "latency_cycles"
    ENERGY_PJ = "energy_pj"
    AREA_MM2 = "area_mm2"
    POWER_W = "power_w"
    EDP = "edp"
    TEMP_MAX_C = "temp_max_c"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One point to evaluate. `arch` and `mapping` both accept `None`, and it means the same kind
    of thing for each: "the evaluator supplies this". `mapping=None` is "the evaluator may choose
    one, and must declare that it did"; `arch=None` is "use your own default architecture", which
    an adapter either honours or refuses with `NotExpressibleError` — never silently substitutes
    something the caller didn't ask for. `arch` stays required as a *parameter* (no default), so
    passing `None` is a stated choice rather than an omission.
    """

    workload: WorkloadRef
    arch: ArchRef
    mapping: MappingRef = None


@dataclass(frozen=True, slots=True)
class Budget:
    wall_clock_s: float | None = None
    usd: float | None = None
    fidelity_floor: str | None = None


@dataclass(frozen=True, slots=True)
class Estimate:
    """A single metric value with its uncertainty (docs/gap-analysis.md G2). A bare scalar is a bug."""

    value: float
    ci_low: float
    ci_high: float
    unit: str
    method: Method

    def __post_init__(self) -> None:
        if not (self.ci_low <= self.value <= self.ci_high):
            raise ValueError(
                f"Estimate.value={self.value} must lie within "
                f"[ci_low={self.ci_low}, ci_high={self.ci_high}]"
            )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["method"] = self.method.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Estimate":
        return cls(
            value=d["value"], ci_low=d["ci_low"], ci_high=d["ci_high"], unit=d["unit"],
            method=Method(d["method"]),
        )


@dataclass(frozen=True, slots=True)
class Constraint:
    kind: str
    detail: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Constraint":
        return cls(kind=d["kind"], detail=d.get("detail", ""))


@dataclass(frozen=True, slots=True)
class Validity:
    """Computed by an independent checker, not by the cost model — the primary
    anti-reward-hacking mechanism (docs/gap-analysis.md G14)."""

    ok: bool
    violations: tuple[Constraint, ...] = ()
    checker_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "violations": [asdict(v) for v in self.violations],
            "checker_version": self.checker_version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Validity":
        return cls(
            ok=d["ok"],
            violations=tuple(Constraint.from_dict(v) for v in d.get("violations", ())),
            checker_version=d.get("checker_version", ""),
        )


@dataclass(frozen=True, slots=True)
class Domain:
    """Says whether the model is extrapolating (docs/gap-analysis.md G2)."""

    in_domain: bool
    distance: float = 0.0
    nearest_calibration: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Domain":
        return cls(
            in_domain=d["in_domain"], distance=d.get("distance", 0.0),
            nearest_calibration=d.get("nearest_calibration"),
        )


@dataclass(frozen=True, slots=True)
class Roofline:
    arithmetic_intensity: float
    peak: float
    achieved: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Roofline":
        return cls(
            arithmetic_intensity=d["arithmetic_intensity"], peak=d["peak"],
            achieved=d["achieved"],
        )


@dataclass(frozen=True, slots=True)
class Bottleneck:
    """Structured explanation, not prose — both a human and an agent get *why*, not just *what*
    (the Explainable-DSE insight made into an interface guarantee, docs/gap-analysis.md G4)."""

    limiter: Limiter
    per_level_utilisation: dict[str, float] = field(default_factory=dict)
    roofline: Roofline | None = None
    top_costs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "limiter": self.limiter.value,
            "per_level_utilisation": dict(self.per_level_utilisation),
            "roofline": self.roofline.to_dict() if self.roofline else None,
            "top_costs": list(self.top_costs),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Bottleneck":
        roofline = d.get("roofline")
        return cls(
            limiter=Limiter(d["limiter"]),
            per_level_utilisation=dict(d.get("per_level_utilisation", {})),
            roofline=Roofline.from_dict(roofline) if roofline is not None else None,
            top_costs=tuple(d.get("top_costs", ())),
        )


@dataclass(frozen=True, slots=True)
class Provenance:
    evaluator: str
    inputs: dict[str, str]
    calibration: str | None = None
    seed: int | None = None
    wall_clock_s: float | None = None
    usd_cost: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Provenance":
        return cls(
            evaluator=d["evaluator"], inputs=dict(d.get("inputs", {})),
            calibration=d.get("calibration"), seed=d.get("seed"),
            wall_clock_s=d.get("wall_clock_s"), usd_cost=d.get("usd_cost"),
        )


@dataclass(frozen=True, slots=True)
class Escalation:
    recommended: bool
    next_rung: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Escalation":
        return cls(
            recommended=d["recommended"], next_rung=d.get("next_rung"),
            reason=d.get("reason"),
        )


class MissingMetricError(KeyError):
    """Raised when a `Result` is asked for a metric it does not carry.

    A `KeyError` subclass so existing `except KeyError` handlers keep working, but one that says
    what happened: the bare `KeyError('energy_pj')` this replaces had no way to convey that an
    evaluator is *allowed* to omit a metric, which is the single fact a reader needs
    (docs/decisions.md D201).
    """

    def __str__(self) -> str:  # KeyError's own repr quotes the message, which reads badly here
        return self.args[0] if self.args else ""


class MetricMap(dict):
    """`Result.metrics`, with a failure message instead of a bare key.

    Still a plain `dict` for every other purpose — adapters construct one from a dict literal
    without knowing it exists, and `to_dict`/iteration/`in` are unchanged.
    """

    def __missing__(self, key: str) -> Estimate:
        raise MissingMetricError(
            f"evaluator returned no {key!r} metric (got {sorted(self)}). Evaluators may legally "
            "omit a metric that was requested — use Result.metric(name) to handle that case, or "
            "Result.refusal_for(name) to test for it."
        )


@dataclass(frozen=True, slots=True)
class MetricOutcome:
    """A metric's value, or the reason there isn't one — the value-or-refusal pair the ABI was
    missing (docs/decisions.md D201).

    `estimate` and `reason` are mutually exclusive: exactly one is set. Branch on `ok`, or call
    `.value` when the metric's presence has already been established and a failure would be a bug.
    """

    metric: str
    estimate: "Estimate | None"
    reason: str | None

    @property
    def ok(self) -> bool:
        return self.estimate is not None

    @property
    def value(self) -> float:
        if self.estimate is None:
            raise MissingMetricError(self.reason or f"no {self.metric!r} metric")
        return self.estimate.value

    def value_or(self, default: float) -> float:
        """The value, or `default` — for callers that genuinely have a sensible fallback. Most do
        not, which is why this is not the primary accessor."""
        return default if self.estimate is None else self.estimate.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "ok": self.ok,
            "estimate": self.estimate.to_dict() if self.estimate is not None else None,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class Result:
    """The Evaluator ABI's return shape (docs/evaluator-abi.md). Four things new relative to every
    existing DSE tool: `Estimate` carries an interval, not a scalar; `domain.in_domain` flags
    extrapolation; `bottleneck` is structured; `validity` is computed independently of the cost
    model.
    """

    metrics: dict[str, Estimate]
    validity: Validity
    domain: Domain
    bottleneck: Bottleneck
    provenance: Provenance
    escalation: Escalation
    # Per-metric domains (docs/decisions.md D140). `domain` above stays the *worst* across
    # metrics, deliberately — a result is only as calibrated as its least-calibrated number. But
    # that aggregate is unreadable as a statement about any particular metric: a latency measured
    # directly still reports `in_domain=False` because nothing ever calibrates `energy_pj`, which
    # neither the RTL reference nor a generated design can produce. Callers that care about one
    # metric can now read its own domain instead of inferring from an aggregate that is answering
    # a different question. Empty for uncalibrated results, so every existing constructor is
    # unchanged.
    metric_domains: dict[str, Domain] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Adapters construct `metrics` from a plain dict literal and should not have to know about
        # `MetricMap`; converting here means every Result carries the explanatory failure without a
        # single adapter changing (docs/decisions.md D201). frozen=True, hence object.__setattr__.
        if not isinstance(self.metrics, MetricMap):
            object.__setattr__(self, "metrics", MetricMap(self.metrics))

    def metric(self, name: str) -> MetricOutcome:
        """This Result's value for `name`, or the reason there isn't one.

        The accessor the ABI was missing: an evaluator may legally omit a metric that was
        requested, so every consumer has to handle that, and `metrics[name]` gives no way to do it
        without a `try` or a prior membership test. Branch on `outcome.ok`, or read
        `outcome.value` where absence would be a bug.
        """
        estimate = dict.get(self.metrics, name)
        return MetricOutcome(
            metric=name,
            estimate=estimate,
            reason=None if estimate is not None else (
                f"evaluator returned no {name!r} metric (got {sorted(self.metrics)})"
            ),
        )

    def refusal_for(self, metric: str) -> str | None:
        """`None` if this Result carries `metric`; otherwise one standard sentence saying it
        doesn't, ready to record as a per-candidate error.

        An evaluator may legally return a Result without the metric that was asked for — this is
        not an error the ABI forbids, and `evaluators/rtl` does it routinely (it populates its
        metrics dict only when `latency_cycles` was requested). Every consumer therefore has to
        handle it, and until docs/decisions.md D168 they each had to *remember* to: D112 fixed the
        resulting KeyError in `search/architecture/dse.py`, and the identical hole stayed open in
        the annealing, agentic and exhaustive strategies, where it killed whole searches over one
        candidate. This exists so the check is one call rather than eleven independently-recalled
        `.get`s, and so the message a user sees is the same wherever it comes from.
        """
        return self.metric(metric).reason

    def value_of(self, metric: str) -> float:
        """This Result's value for `metric`. Raises `KeyError` carrying `refusal_for`'s message —
        for the callers that have already established the metric is present (a `refusal_for` check
        above, or a filter that kept only Results carrying it) and want the failure to explain
        itself if that reasoning is ever wrong.
        """
        return self.metric(metric).value

    def estimate_of(self, metric: str) -> "Estimate":
        """The full `Estimate` for `metric` — `value_of`'s sibling for callers that need the CI
        bounds, unit or method, with the same contract: presence was established upstream, and a
        `MissingMetricError` that explains itself if that reasoning is ever wrong.
        """
        outcome = self.metric(metric)
        if outcome.estimate is None:
            raise MissingMetricError(outcome.reason or f"no {metric!r} metric")
        return outcome.estimate

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
            "validity": self.validity.to_dict(),
            "domain": self.domain.to_dict(),
            "bottleneck": self.bottleneck.to_dict(),
            "provenance": self.provenance.to_dict(),
            "escalation": self.escalation.to_dict(),
            "metric_domains": {k: v.to_dict() for k, v in self.metric_domains.items()},
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Result":
        """The exact inverse of `to_dict()` — reconstructs a typed `Result` from the plain dict
        `ResultStore.get_result`/`find_results` return (docs/stores.md: the store is deliberately
        decoupled from any one evaluator's in-memory types, so it hands back dicts; callers that
        need a typed `Result` back — warm-start, replay, an MCP client reconstructing a stored
        result — use this rather than each writing their own ad-hoc reconstruction.
        """
        return cls(
            metrics={k: Estimate.from_dict(v) for k, v in d["metrics"].items()},
            validity=Validity.from_dict(d["validity"]),
            domain=Domain.from_dict(d["domain"]),
            bottleneck=Bottleneck.from_dict(d["bottleneck"]),
            provenance=Provenance.from_dict(d["provenance"]),
            escalation=Escalation.from_dict(d["escalation"]),
        )
