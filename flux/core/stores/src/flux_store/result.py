"""A measurement as a record row stores it (docs/records.md): `Result` -- each metric's value
and how it was obtained, and which stage produced it. Stored rows written with the older, wider
shape (intervals, validity, domain, bottleneck, escalation) read back: the extra keys are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Method(str, Enum):
    ANALYTIC = "analytic"
    SIMULATED = "simulated"
    MEASURED = "measured"


@dataclass(frozen=True, slots=True)
class Estimate:
    """One metric's value and how it was obtained (a model's prediction, or a tool's number)."""

    value: float
    method: Method

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "method": self.method.value}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Estimate":
        return cls(value=d["value"], method=Method(d["method"]))


@dataclass(frozen=True, slots=True)
class Provenance:
    evaluator: str                  # the stage that produced it, e.g. "flux@records"
    inputs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"evaluator": self.evaluator, "inputs": dict(self.inputs)}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Provenance":
        return cls(evaluator=d["evaluator"], inputs=dict(d.get("inputs", {})))


@dataclass(frozen=True, slots=True)
class Result:
    """A trial's measurement: its metrics and where they came from."""

    metrics: dict[str, Estimate]
    provenance: Provenance

    def value_of(self, metric: str) -> float:
        return self.metrics[metric].value

    def estimate_of(self, metric: str) -> Estimate:
        return self.metrics[metric]

    def to_dict(self) -> dict[str, Any]:
        return {"metrics": {k: v.to_dict() for k, v in self.metrics.items()},
                "provenance": self.provenance.to_dict()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Result":
        return cls(metrics={k: Estimate.from_dict(v) for k, v in d["metrics"].items()},
                   provenance=Provenance.from_dict(d["provenance"]))
