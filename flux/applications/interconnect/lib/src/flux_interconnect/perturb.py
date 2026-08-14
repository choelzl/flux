"""Neighbours of a fabric: small changes and large ones, for searching AROUND a known-good design.

WHY THIS EXISTS. Enumeration answers "what can the rules express" and proposing answers "what can
a model invent". Neither answers "is there something slightly better next door", which is the
question a person asks after seeing a good result, and the one a Monte-Carlo or annealing search is
built around. The demo's `narrow` and `wide` looked like they meant this and did not: they were
nested subsets of one static enumeration, with zero candidates unique to the narrow one (D308).

A perturbation is defined against an INCUMBENT, so the space it explores depends on where the
search already is. That is the difference from enumeration, and the reason the two are worth
having together: enumeration covers the codified space once, perturbation exploits whatever turned
out to be good in it.

RADIUS is the one knob, and it means what it says:

  small   one decision changed by one step: a switch count, a fan-out, a radix, a routing policy.
          The result is recognisably the same design.
  large   the shape changes: a rank is added or removed, counts double or halve, a mesh becomes a
          torus. The result is a different design in the same neighbourhood.

Every mutant is BUILT before it is returned, so a perturbation that cannot chain — the failure the
staged form makes easy — is simply not a neighbour rather than an error later.
"""

from __future__ import annotations

import copy
from typing import Any

from .topology import ROUTING_POLICIES, build, derive_stage_inputs

RADII = ("small", "large")


def _ok(spec: dict[str, Any]) -> bool:
    try:
        build(spec)
        return True
    except Exception:  # noqa: BLE001 — an unbuildable neighbour is not a neighbour
        return False


def _staged(spec: dict[str, Any], radius: str) -> list[dict[str, Any]]:
    stages = spec.get("stages")
    if not stages:
        return []
    out: list[dict[str, Any]] = []

    def emit(new_stages: list[dict[str, Any]]) -> None:
        # `in` is derived, never carried over: it follows from the stage before, and copying the
        # old value is exactly how a mutated fabric stops chaining.
        candidate = {**spec, "stages": derive_stage_inputs(int(spec["clients"]), new_stages)}
        out.append(candidate)

    for i, stage in enumerate(stages):
        for field in ("switches", "out"):
            for delta in ((-1, 1) if radius == "small" else (-2, 2)):
                mutated = copy.deepcopy(stages)
                mutated[i] = {**stage, field: int(stage[field]) + delta}
                if mutated[i][field] >= 1:
                    emit(mutated)
        if radius == "large":
            for factor in (2, 0.5):
                mutated = copy.deepcopy(stages)
                scaled = max(1, int(int(stage["switches"]) * factor))
                mutated[i] = {**stage, "switches": scaled}
                emit(mutated)
    if radius == "large":
        # A rank added or removed is the largest move that keeps the family: it changes latency by
        # a cycle and the arity of everything downstream.
        if len(stages) > 1:
            for i in range(len(stages)):
                emit([s for j, s in enumerate(stages) if j != i])
        last = stages[-1]
        emit([*copy.deepcopy(stages), {"switches": int(last["switches"]), "out": int(last["out"])}])
    return out


def _params(spec: dict[str, Any], radius: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    deltas = (-1, 1) if radius == "small" else (-2, 2, -4, 4)
    out = []
    for field in fields:
        if field not in spec:
            continue
        for delta in deltas:
            value = int(spec[field]) + delta
            if value >= 1:
                out.append({**spec, field: value})
    return out


def mutate(spec: dict[str, Any], *, radius: str = "small",
           limit: int = 12) -> list[dict[str, Any]]:
    """Buildable neighbours of `spec`, nearest first, at most `limit`.

    Deduplicated against the original, because a mutation that lands back on its own parent is
    not a neighbour and would spend a screening slot proving it.
    """
    if radius not in RADII:
        raise ValueError(f"radius must be one of {RADII}, got {radius!r}")
    kind = str(spec.get("kind", ""))
    out: list[dict[str, Any]] = []
    if kind in ("xbar_staged", "xbar_multistage"):
        out += _staged(spec, radius)
    elif kind == "butterfly":
        out += _params(spec, radius, ("radix",))
    elif kind == "clos":
        out += _params(spec, radius, ("n", "m"))
    elif kind in ("mesh", "torus"):
        out += _params(spec, radius, ("rows", "cols"))
        if radius == "large":
            # The wrap links are the whole difference between the two, and they halve the
            # diameter for the same router count — the largest single change available here.
            out.append({**spec, "kind": "torus" if kind == "mesh" else "mesh"})
    elif kind == "ring":
        out += _params(spec, radius, ("routers",))

    # Routing is a neighbour of every multi-path fabric and costs nothing to try: the same
    # silicon serves 8.90 or 13.55 words/cycle depending on it (D302).
    if radius == "small":
        for policy in ROUTING_POLICIES:
            if policy != spec.get("routing", "rotate"):
                out.append({**spec, "routing": policy})

    seen = {_key(spec)}
    unique: list[dict[str, Any]] = []
    for candidate in out:
        key = _key(candidate)
        if key in seen or not _ok(candidate):
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) >= limit:
            break
    return unique


def _key(spec: dict[str, Any]) -> str:
    import json

    return json.dumps(spec, sort_keys=True)
