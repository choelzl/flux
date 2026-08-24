"""The evaluator registry (docs/evaluator-abi.md): every backend by name, resolved lazily.

What a name means is an ABI concern, so the map lives here (D426); `flux_cli.registry` is a
facade over it.

Backends are a data table of `(module, class)` imported on first use, so `flux import` works
with only `flux-ir` installed. Applications and tests add evaluators with `register_evaluator`;
nothing here imports an application.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

__all__ = ["DEFAULT_METRICS", "available_evaluators", "escalation_stage", "evaluator_class", "evaluator_name_for",
           "make_evaluator", "register_evaluator", "translates"]

#: What `flux eval` asks an evaluator for when the caller names no metrics.
DEFAULT_METRICS = frozenset({"latency_cycles", "energy_pj"})

# name -> (module, class). The order is irrelevant; `available_evaluators` sorts.
_DEFAULTS: dict[str, tuple[str, str]] = {
    "zigzag": ("flux_evaluator_zigzag", "ZigZagEvaluator"),
    "timeloop": ("flux_evaluator_timeloop", "TimeloopEvaluator"),
    "rtl": ("flux_evaluator_rtl", "RTLEvaluator"),
    "openroad": ("flux_evaluator_openroad", "OpenRoadEvaluator"),
    "champsim": ("flux_evaluator_champsim", "ChampSimEvaluator"),
}

_FACTORIES: dict[str, Callable[[], Any]] = {}


def register_evaluator(name: str, factory: Callable[[], Any], *, replace: bool = False) -> None:
    """Add (or, with `replace`, override) an evaluator under `name`. `factory` is called
    on every `make_evaluator(name)`; it should be cheap to call and may import lazily."""
    if not name or not isinstance(name, str):
        raise ValueError(f"an evaluator name must be a non-empty string, not {name!r}")
    if not replace and (name in _FACTORIES or name in _DEFAULTS):
        raise ValueError(f"evaluator {name!r} is already registered; pass replace=True to "
                         "override it")
    _FACTORIES[name] = factory


def available_evaluators() -> list[str]:
    """Every registered name, sorted. Says nothing about whether the backend's tool is
    installed: `make_evaluator` is where that is discovered."""
    return sorted(set(_DEFAULTS) | set(_FACTORIES))


def evaluator_class(name: str) -> type:
    """The adapter class registered under `name`, imported but not constructed, for asking
    what the backend declares (`translates`, `name`) without an instance."""
    if name in _DEFAULTS:
        module, cls = _DEFAULTS[name]
        return getattr(importlib.import_module(module), cls)
    if name in _FACTORIES:
        return type(_FACTORIES[name]())
    raise ValueError(f"unknown evaluator {name!r}; available: {available_evaluators()}")


def translates(name: str) -> frozenset[str]:
    """The IR blocks the backend honours, from its class's `translates`: `"mapping"` (an
    explicit Mapping IR), `"memory_size"` (a level's `size_kb`). Absent means every block."""
    got = getattr(evaluator_class(name), "translates", None)
    return frozenset(got) if got is not None else frozenset({"mapping", "memory_size"})


def make_evaluator(name: str) -> Any:
    """A fresh evaluator for `name`, carrying `.name` (adapters declare it; the registry
    fills it in for one that does not)."""
    if name in _FACTORIES:
        ev = _FACTORIES[name]()
    elif name in _DEFAULTS:
        module, cls = _DEFAULTS[name]
        ev = getattr(importlib.import_module(module), cls)()
    else:
        raise ValueError(f"unknown evaluator {name!r}; available: {available_evaluators()}")
    if getattr(ev, "name", None) in (None, ""):
        try:
            ev.name = name
        except (AttributeError, TypeError):
            pass
    return ev


def escalation_stage(name: str) -> str:
    """The name an `Escalation.next_stage` may carry: a registered evaluator (D448), so
    `make_evaluator(result.escalation.next_stage)` is always a valid step.

    Emitters call this. `Escalation.from_dict` does not: a stored result naming a backend
    this checkout no longer registers must still read back.
    """
    if name not in available_evaluators():
        raise ValueError(
            f"{name!r} cannot be an escalation stage: it is not an evaluator this checkout "
            f"registers. Available: {available_evaluators()}")
    return name


def evaluator_name_for(provenance: str) -> str:
    """Map a stored `Result.provenance.evaluator` string (`'zigzag@3.8.5'`,
    `'timeloop-docker@image'`, `'champsim@0.1'`) back to a registered name --
    the longest registered prefix wins, so `interconnect_struct` is never read as
    `interconnect`."""
    hits = [n for n in available_evaluators() if provenance.startswith(n)]
    if not hits:
        raise ValueError(
            f"cannot infer an evaluator from provenance string {provenance!r}; known names: "
            f"{available_evaluators()}")
    return max(hits, key=len)
