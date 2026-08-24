"""The evaluator registry (docs/evaluator-abi.md): every backend by name, resolved lazily.

The only name-to-evaluator map used to live in the CLI (`flux_cli.registry`), which meant
fourteen CHIA nodes, the campaign runner and the RTL generator all imported an interface
package to find an evaluator, and a task document could not say "measure on `rtl`" without
going through the CLI. The map is an ABI concern -- what a name means -- so it lives here
(D426). The CLI's module is a facade over this one, so its importers keep working.

Backends are registered as a data table of `(module, class)` and imported on first use, not
eagerly: `flux import` must work with only `flux-ir` installed, without pulling in
`zigzag-dse` or requiring a `docker` daemon just to validate a schema. Applications and tests
add their own evaluators with `register_evaluator`; nothing here imports an application.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable

__all__ = ["available_evaluators", "escalation_stage", "evaluator_class", "evaluator_name_for",
           "make_evaluator", "register_evaluator", "translates"]

# name -> (module, class). The order is irrelevant; `available_evaluators` sorts.
_DEFAULTS: dict[str, tuple[str, str]] = {
    "zigzag": ("flux_evaluator_zigzag", "ZigZagEvaluator"),
    "timeloop": ("flux_evaluator_timeloop", "TimeloopEvaluator"),
    "rtl": ("flux_evaluator_rtl", "RTLEvaluator"),
    "systemc": ("flux_evaluator_systemc", "SystemCEvaluator"),
    "booksim": ("flux_evaluator_booksim", "BooksimEvaluator"),
    "noxim": ("flux_evaluator_noxim", "NoximEvaluator"),
    "cacti": ("flux_evaluator_cacti", "CactiEvaluator"),
    "gem5": ("flux_evaluator_gem5", "Gem5Evaluator"),
    "openroad": ("flux_evaluator_openroad", "OpenRoadEvaluator"),
    "thermal": ("flux_evaluator_thermal", "ThermalEvaluator"),
    "dramsim3": ("flux_evaluator_dramsim3", "DramSim3Evaluator"),
    "native": ("flux_evaluator_native", "NativeEvaluator"),
    "stream": ("flux_evaluator_stream", "StreamEvaluator"),
    "interconnect_struct": ("flux_evaluator_interconnect_struct",
                            "InterconnectStructuralEvaluator"),
    "interconnect_phys": ("flux_evaluator_interconnect_phys", "InterconnectPhysicalEvaluator"),
    "champsim_bingo": ("flux_evaluator_champsim_bingo", "ChampSimBingoEvaluator"),
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
    """The adapter class registered under `name`, imported but not constructed (D440): for a
    caller that wants to ask what the backend declares (`translates`, `name`) before paying
    for an instance."""
    if name in _DEFAULTS:
        module, cls = _DEFAULTS[name]
        return getattr(importlib.import_module(module), cls)
    if name in _FACTORIES:
        return type(_FACTORIES[name]())
    raise ValueError(f"unknown evaluator {name!r}; available: {available_evaluators()}")


def translates(name: str) -> frozenset[str]:
    """The IR blocks the backend honours, from its class's `translates` (D440): `"mapping"`
    (an explicit Mapping IR), `"memory_size"` (a level's `size_kb`). Absent, every block --
    the ABI's default. rtl and systemc declare neither: they model one fixed schedule and
    read only the compute width."""
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
    """The name an `Escalation.next_stage` may carry: a REGISTERED evaluator (D448).

    `next_stage` was a free string nothing checked. Two places wrote `"rtl"` into it and a
    caller that wanted to ACT on the recommendation had a word rather than a step: a typo,
    or a backend this checkout does not register, read exactly like a valid stage until
    something tried to use it. A stage IS an evaluator name, so it is validated here, where
    the map lives, and `make_evaluator(result.escalation.next_stage)` is the step it names.

    Emitters call this. `Escalation.from_dict` deliberately does not: a stored result naming
    a backend this checkout no longer registers must still read back, and a reader that wants
    to invoke it finds out then, with the registry's own error.
    """
    if name not in available_evaluators():
        raise ValueError(
            f"{name!r} cannot be an escalation stage: it is not an evaluator this checkout "
            f"registers. Available: {available_evaluators()}")
    return name


def evaluator_name_for(provenance: str) -> str:
    """Map a stored `Result.provenance.evaluator` string (`'zigzag@3.8.5'`,
    `'timeloop-docker@image'`, `'interconnect_struct@1'`) back to a registered name --
    the longest registered prefix wins, so `interconnect_struct` is never read as
    `interconnect`."""
    hits = [n for n in available_evaluators() if provenance.startswith(n)]
    if not hits:
        raise ValueError(
            f"cannot infer an evaluator from provenance string {provenance!r}; known names: "
            f"{available_evaluators()}")
    return max(hits, key=len)
