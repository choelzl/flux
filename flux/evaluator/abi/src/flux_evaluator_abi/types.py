"""Evaluator ABI v0.1 types (docs/evaluator-abi.md). Any cost model that implements the `Evaluator`
protocol (see protocol.py) becomes swappable behind these types; any search strategy that speaks
them becomes portable across evaluators.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Union

# A reference is a content hash (str, see flux_ir.content_hash) into the store, or an inline IR
# document (dict) hashed on first use. mapping=None: the evaluator chooses one and must say so.
WorkloadRef = Union[str, dict[str, Any]]
# arch=None: use the evaluator's own default architecture, or refuse with `NotExpressibleError`
# (D172, D173).
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
    THERMAL = "thermal"  # evaluator/thermal, 3D-ICE backed (D64)
    NONE = "none"        # no bottleneck claimed: a record written without a cost model (D440)


class Metric(str, Enum):
    """Well-known metric names (docs/evaluator-abi.md): a shared vocabulary, not a whitelist.
    Metric keys are plain strings and evaluators may report others.
    """

    LATENCY_CYCLES = "latency_cycles"
    ENERGY_PJ = "energy_pj"
    AREA_MM2 = "area_mm2"
    POWER_W = "power_w"
    EDP = "edp"
    TEMP_MAX_C = "temp_max_c"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One point to evaluate. `None` for `arch` or `mapping` means the evaluator supplies it:
    a mapping it must declare, or its default architecture (or a `NotExpressibleError`). `arch`
    has no default, so passing `None` is an explicit choice.
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
    """A single metric value with its uncertainty interval."""

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
    """Computed by an independent checker, not by the cost model, so a search cannot game it."""

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
    """Whether the model is extrapolating."""

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
    """Structured explanation of what limits the result, readable by both humans and agents."""

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
    """Whether this result is worth re-measuring on a costlier backend, and which one.

    `next_stage` is a registered evaluator name (D448) that `make_evaluator` can build. Emitters
    validate it with `escalation_stage(name)`; `from_dict` does not, so stored results naming a
    since-removed backend still load.
    """

    recommended: bool
    next_stage: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Escalation":
        return cls(
            recommended=d["recommended"], next_stage=d.get("next_stage"),
            reason=d.get("reason"),
        )


class MissingMetricError(KeyError):
    """Raised when a `Result` is asked for a metric it does not carry.

    A `KeyError` subclass for compatibility, with a message saying that evaluators may omit a
    requested metric (D201).
    """

    def __str__(self) -> str:  # KeyError's own repr quotes the message, which reads badly here
        return self.args[0] if self.args else ""


class MetricMap(dict):
    """`Result.metrics`, with a failure message instead of a bare key.

    Otherwise a plain `dict`.
    """

    def __missing__(self, key: str) -> Estimate:
        raise MissingMetricError(
            f"evaluator returned no {key!r} metric (got {sorted(self)}). Evaluators may legally "
            "omit a metric that was requested — use Result.metric(name) to handle that case, or "
            "Result.refusal_for(name) to test for it."
        )


@dataclass(frozen=True, slots=True)
class MetricOutcome:
    """A metric's value, or the reason there isn't one (D201).

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
        """The value, or `default`, for callers with a sensible fallback."""
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
    """The Evaluator ABI's return shape (docs/evaluator-abi.md): interval estimates, an
    extrapolation flag, a structured bottleneck and independently computed validity.
    """

    metrics: dict[str, Estimate]
    validity: Validity
    domain: Domain
    bottleneck: Bottleneck
    provenance: Provenance
    escalation: Escalation
    # Per-metric domains (D140). `domain` above is the worst across metrics; this says which
    # metric is out of domain. Empty for uncalibrated results.
    metric_domains: dict[str, Domain] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Adapters pass a plain dict; wrap it so missing keys explain themselves (D201).
        # frozen=True, hence object.__setattr__.
        if not isinstance(self.metrics, MetricMap):
            object.__setattr__(self, "metrics", MetricMap(self.metrics))

    def metric(self, name: str) -> MetricOutcome:
        """This Result's value for `name`, or the reason there isn't one.

        An evaluator may omit a requested metric, so branch on `outcome.ok`, or read
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

        Evaluators may legally omit a requested metric, so every consumer must handle it; this
        gives one check and one message everywhere (D168).
        """
        return self.metric(metric).reason

    def value_of(self, metric: str) -> float:
        """This Result's value for `metric`, for callers that already know it is present.
        Raises `MissingMetricError` (a `KeyError`) with `refusal_for`'s message otherwise.
        """
        return self.metric(metric).value

    def estimate_of(self, metric: str) -> "Estimate":
        """The full `Estimate` for `metric`; same contract as `value_of`."""
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
        """The exact inverse of `to_dict()`, e.g. for dicts returned by `ResultStore`."""
        return cls(
            metrics={k: Estimate.from_dict(v) for k, v in d["metrics"].items()},
            validity=Validity.from_dict(d["validity"]),
            domain=Domain.from_dict(d["domain"]),
            bottleneck=Bottleneck.from_dict(d["bottleneck"]),
            provenance=Provenance.from_dict(d["provenance"]),
            escalation=Escalation.from_dict(d["escalation"]),
            metric_domains={k: Domain.from_dict(v)
                            for k, v in (d.get("metric_domains") or {}).items()},
        )
