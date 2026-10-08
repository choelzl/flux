"""Numeric measurements, including sparse dictionaries of named tests.

Store dictionary leaves as parent.test so records, objectives and caches use the same numbers.
The separate group metadata retains test names even when their value is unavailable.
"""

from __future__ import annotations

import math
import statistics
from typing import Any


def finite_number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def metric_groups(values: dict[str, Any]) -> dict[str, list[str]]:
    groups = dict(values.get("_metric_groups") or {})
    for name, value in values.items():
        if name != "_metric_groups" and isinstance(value, dict):
            groups[name] = [key for key in value if isinstance(key, str) and key]
    return groups


AGGREGATES = {"mean": statistics.mean, "median": statistics.median, "min": min, "max": max, "sum": sum}


def numeric_metrics(values: dict[str, Any], definitions: dict[str, dict[str, Any]] | None = None) -> dict[str, float]:
    out = {}
    for name, value in values.items():
        if name == "_metric_groups":
            continue
        items = ((f"{name}.{key}", v) for key, v in value.items() if isinstance(key, str) and key) if isinstance(value, dict) else ((name, value),)
        for key, v in items:
            number = finite_number(v)
            if number is not None:
                if key in out:
                    raise ValueError(f"ambiguous dictionary measurement {key!r}")
                out[key] = number
        aggregate = (definitions or {}).get(name, {}).get("aggregate")
        if aggregate in AGGREGATES and isinstance(value, dict):
            numbers = [n for v in value.values() if (n := finite_number(v)) is not None]
            if numbers:
                number = finite_number(AGGREGATES[aggregate](numbers))
                if number is not None:
                    out[name] = number
    return out
