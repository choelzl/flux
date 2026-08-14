"""Evaluator backend registry for the Flux CLI.

Backends are registered as lazy factory functions, not imported eagerly: `flux import` should
work with only `flux-ir` installed, without pulling in `zigzag-dse` or requiring a `docker`
daemon just to validate a schema. This mirrors docs/roadmap.md's risk-table note that "adapters are
thin and independently installable" — the CLI itself stays that way too.
"""

from __future__ import annotations

from typing import Any, Callable

DEFAULT_METRICS = frozenset({"latency_cycles", "energy_pj"})

_BACKEND_FACTORIES: dict[str, Callable[[], Any]] = {}


def _register_defaults() -> None:
    if _BACKEND_FACTORIES:
        return

    def _zigzag() -> Any:
        from flux_evaluator_zigzag import ZigZagEvaluator

        return ZigZagEvaluator()

    def _timeloop() -> Any:
        from flux_evaluator_timeloop import TimeloopEvaluator

        return TimeloopEvaluator()

    def _rtl() -> Any:
        from flux_evaluator_rtl import RTLEvaluator

        return RTLEvaluator()

    def _systemc() -> Any:
        from flux_evaluator_systemc import SystemCEvaluator

        return SystemCEvaluator()

    def _booksim() -> Any:
        from flux_evaluator_booksim import BooksimEvaluator

        return BooksimEvaluator()

    def _noxim() -> Any:
        from flux_evaluator_noxim import NoximEvaluator

        return NoximEvaluator()

    def _cacti() -> Any:
        from flux_evaluator_cacti import CactiEvaluator

        return CactiEvaluator()

    def _gem5() -> Any:
        from flux_evaluator_gem5 import Gem5Evaluator

        return Gem5Evaluator()

    def _thermal() -> Any:
        from flux_evaluator_thermal import ThermalEvaluator

        return ThermalEvaluator()

    def _dramsim3() -> Any:
        from flux_evaluator_dramsim3 import DramSim3Evaluator

        return DramSim3Evaluator()

    def _native() -> Any:
        from flux_evaluator_native import NativeEvaluator

        return NativeEvaluator()

    def _stream() -> Any:
        from flux_evaluator_stream import StreamEvaluator

        return StreamEvaluator()

    def _openroad() -> Any:
        from flux_evaluator_openroad import OpenRoadEvaluator

        return OpenRoadEvaluator()

    def _interconnect_struct() -> Any:
        from flux_evaluator_interconnect_struct import InterconnectStructuralEvaluator

        return InterconnectStructuralEvaluator()

    def _interconnect_phys() -> Any:
        from flux_evaluator_interconnect_phys import InterconnectPhysicalEvaluator

        return InterconnectPhysicalEvaluator()

    _BACKEND_FACTORIES["zigzag"] = _zigzag
    _BACKEND_FACTORIES["timeloop"] = _timeloop
    _BACKEND_FACTORIES["rtl"] = _rtl
    _BACKEND_FACTORIES["systemc"] = _systemc
    _BACKEND_FACTORIES["booksim"] = _booksim
    _BACKEND_FACTORIES["noxim"] = _noxim
    _BACKEND_FACTORIES["cacti"] = _cacti
    _BACKEND_FACTORIES["gem5"] = _gem5
    _BACKEND_FACTORIES["openroad"] = _openroad
    _BACKEND_FACTORIES["thermal"] = _thermal
    _BACKEND_FACTORIES["dramsim3"] = _dramsim3
    _BACKEND_FACTORIES["native"] = _native
    _BACKEND_FACTORIES["stream"] = _stream
    _BACKEND_FACTORIES["interconnect_struct"] = _interconnect_struct
    _BACKEND_FACTORIES["interconnect_phys"] = _interconnect_phys


def available_backends() -> list[str]:
    _register_defaults()
    return sorted(_BACKEND_FACTORIES)


def make_evaluator(name: str) -> Any:
    _register_defaults()
    if name not in _BACKEND_FACTORIES:
        raise ValueError(f"unknown backend {name!r}; available: {available_backends()}")
    return _BACKEND_FACTORIES[name]()


def backend_for_evaluator_string(evaluator: str) -> str:
    """Map a stored `Result.provenance.evaluator` string (e.g. `'zigzag@3.8.5'`,
    `'timeloop-docker@image'`) back to a registered backend name, for `flux replay`.
    """
    _register_defaults()
    for name in _BACKEND_FACTORIES:
        if evaluator.startswith(name):
            return name
    raise ValueError(
        f"cannot infer a backend from evaluator string {evaluator!r}; known backend prefixes: "
        f"{available_backends()}"
    )
