"""Evaluator backend registry for the Flux CLI -- a facade (D426).

The name-to-evaluator map is an ABI concern and lives in `flux_evaluator_abi.registry`;
this module keeps the names its importers (the CHIA nodes, the campaign runner, the RTL
generator, the live tests) were written against. Backends are still resolved lazily:
`flux import` works with only `flux-ir` installed.
"""

from __future__ import annotations

from flux_evaluator_abi import (  # noqa: F401
    available_evaluators,
    evaluator_name_for,
    make_evaluator,
    register_evaluator,
)

__all__ = ["DEFAULT_METRICS", "available_backends", "backend_for_evaluator_string",
           "make_evaluator", "register_evaluator"]

DEFAULT_METRICS = frozenset({"latency_cycles", "energy_pj"})


def available_backends() -> list[str]:
    return available_evaluators()


def backend_for_evaluator_string(evaluator: str) -> str:
    """Map a stored `Result.provenance.evaluator` string (e.g. `'zigzag@3.8.5'`,
    `'timeloop-docker@image'`) back to a registered backend name, for `flux replay`."""
    return evaluator_name_for(evaluator)

