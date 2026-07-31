"""Evaluator ABI v0.1 types (docs/04.md §4). Any cost model that implements the `Evaluator`
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
# it did" (docs/04.md §4.1).
WorkloadRef = Union[str, dict[str, Any]]
ArchRef = Union[str, dict[str, Any]]
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


class Metric(str, Enum):
    """Well-known metric names (docs/04.md §4.1). Not exhaustive by design: general-SoC
    evaluators (docs/00-decisions.md D1) may report metric names outside this set — `metrics` on
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
    """A single metric value with its uncertainty (docs/03.md G2). A bare scalar is a bug."""

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


@dataclass(frozen=True, slots=True)
class Constraint:
    kind: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Validity:
    """Computed by an independent checker, not by the cost model — the primary
    anti-reward-hacking mechanism (docs/03.md G14)."""

    ok: bool
    violations: tuple[Constraint, ...] = ()
    checker_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "violations": [asdict(v) for v in self.violations],
            "checker_version": self.checker_version,
        }


@dataclass(frozen=True, slots=True)
class Domain:
    """Says whether the model is extrapolating (docs/03.md G2)."""

    in_domain: bool
    distance: float = 0.0
    nearest_calibration: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Roofline:
    arithmetic_intensity: float
    peak: float
    achieved: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Bottleneck:
    """Structured explanation, not prose — both a human and an agent get *why*, not just *what*
    (the Explainable-DSE insight made into an interface guarantee, docs/03.md G4)."""

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


@dataclass(frozen=True, slots=True)
class Escalation:
    recommended: bool
    next_rung: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Result:
    """The Evaluator ABI's return shape (docs/04.md §4.2). Four things new relative to every
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
            "validity": self.validity.to_dict(),
            "domain": self.domain.to_dict(),
            "bottleneck": self.bottleneck.to_dict(),
            "provenance": self.provenance.to_dict(),
            "escalation": self.escalation.to_dict(),
        }
