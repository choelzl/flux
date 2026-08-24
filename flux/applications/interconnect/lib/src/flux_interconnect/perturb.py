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


def has_path_choice(spec: dict[str, Any]) -> bool:
    """Whether any destination is reachable by more than one port, asked of the built fabric.

    PUBLIC because three callers need the same answer and three copies would drift — which is not
    hypothetical, it happened: enumeration filtered routing variants by FAMILY and measured a
    single-path butterfly three times (D307); perturbation offered routing neighbours
    unconditionally and wasted two of five on a single-path fabric; and the decision rung keyed
    fabrics by routing and placed the same silicon under three names. One invariant — a routing
    variant is a distinct fabric only where paths are — and now one implementation of it.
    """
    try:
        from .fabric import routing_tables

        tables = routing_tables(build(spec))
    except Exception:  # noqa: BLE001 — a fabric we cannot route has no choice to offer
        return False
    return any(len(ports) > 1 for stage in tables for switch in stage for ports in switch)


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

    # Routing is a neighbour only where the fabric HAS a choice of path. On a single-path fabric
    # the policy is a no-op and the variant is the parent under another name — two of five
    # neighbours wasted, measured. D307 removed exactly this waste from enumeration by testing
    # path multiplicity instead of assuming a family has it, and the same test belongs here: a
    # fix applied in one place is not applied.
    if radius == "small" and has_path_choice(spec):
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


def has_path_choice_topo(topo) -> bool:
    """`has_path_choice` for an already-built fabric, so callers holding a Topology need not
    reconstruct a spec to ask."""
    try:
        from .fabric import routing_tables

        tables = routing_tables(topo)
    except Exception:  # noqa: BLE001
        return False
    return any(len(ports) > 1 for stage in tables for switch in stage for ports in switch)


def structural_key_of(topo) -> tuple:
    """The identity of an already-built fabric. See `structural_key` for what it means."""
    routing = topo.params.get("routing", "rotate") if has_path_choice_topo(topo) else "-"
    return (tuple(sorted(topo.blocks.items())), topo.stages, routing)


def structural_key(spec: dict[str, Any]) -> tuple:
    """What makes two fabrics THE SAME fabric: its blocks, its depth, and its routing policy
    where routing can matter.

    One definition, because five had grown and they drifted (docs/decisions.md D311). The
    question "are these the same design" was answered separately by the enumerator's deduplicator,
    the proposer's repeat check, the proposer's validator, the decision rung's shortlist and the
    demo's throughput cache — and when one of them gained routing and another did not, structural
    deduplication stopped working entirely while still appearing to run.

    Routing is included ONLY where the fabric has a choice of path, because on a single-path
    fabric every policy produces identical silicon: keying by it put a fabric in a shortlist
    beside two of its own aliases and spent three real placements measuring one design.
    """
    return structural_key_of(build(spec))
