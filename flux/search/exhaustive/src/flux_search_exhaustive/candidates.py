"""Generates flat-mapping candidates (docs/04.md §3.3's Mapping IR) for a single-einsum-op
Workload IR document against a single-spatial-dim Architecture IR document.

Formalizes, as reusable code, the sweep docs/phase1-exit-criterion-report.md's Finding 4 did by
hand: "3 spatial splits × all 6 permutations of the 3 remaining loop dims" for
`mlp-gemm0.yaml` + `simple-npu-1d-v1.yaml`. That was 18 hand-run ZigZag calls with hand-written
YAML; this is the same search space, generated programmatically, for *any* (workload,
architecture) pair within the same v0.1 scope every evaluator adapter in this repo already
shares: one einsum op, one architecture spatial dimension, one shared flat loop order across
operands (no per-operand uneven mapping, no multi-level tiling yet — see evaluators/*/README.md).

`parse_flat_mapping_scope` + `build_flat_mapping_candidate` are split out from
`generate_flat_mapping_candidates` (which just calls both in a double loop) so a strategy that
doesn't want to enumerate the whole space up front — e.g. `search/annealing/`, which builds one
candidate per neighbor move — can reuse the exact same IR-construction logic instead of
duplicating it.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any


class NotAFlatMappingCandidate(Exception):
    """Raised when the given (workload, architecture, for_op) falls outside this strategy's
    v0.1 scope — same scope every evaluator adapter here already has, not a new limitation.
    """


@dataclass(frozen=True, slots=True)
class MappingCandidate:
    mapping: dict[str, Any]
    spatial_dim: str
    spatial_size: int
    temporal_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FlatMappingScope:
    """Everything about a (workload, architecture, for_op) triple needed to construct or mutate
    flat-mapping candidates, parsed once so repeated candidate construction (e.g. one call per
    annealing move) doesn't re-parse and re-validate the same IR documents every time.
    """

    workload_id: str
    arch_id: str
    for_op: str
    bounds: dict[str, int]
    loop_dims: tuple[str, ...]
    tensor_names: tuple[str, ...]
    array_dim_name: str
    array_size: int
    temporal_level: str


def _largest_divisor_at_most(bound: int, limit: int) -> int:
    """The largest whole divisor of `bound` that's <= `limit` — "use as much of the array as
    this dim allows, without leaving a fractional remainder." For bound=4, limit=8: 4 (the array
    is wider than the dim, so use all of it, no temporal remainder). For bound=32, limit=8: 8
    (the dim is wider than the array, use the whole array, remainder 4 stays temporal).
    """
    for candidate in range(min(bound, limit), 0, -1):
        if bound % candidate == 0:
            return candidate
    return 1  # unreachable: 1 always divides bound


def parse_flat_mapping_scope(
    workload: dict[str, Any], arch: dict[str, Any], *, for_op: str
) -> FlatMappingScope:
    """Validate and extract the fixed facts a flat-mapping strategy needs from `workload`/`arch`.
    Raises `NotAFlatMappingCandidate` if they fall outside the flat, single-spatial-dim scope.
    """
    matching_ops = [op for op in workload["ops"] if op["id"] == for_op]
    if len(matching_ops) != 1:
        raise NotAFlatMappingCandidate(f"expected exactly one op with id {for_op!r}")
    op = matching_ops[0]
    if op.get("kind") != "einsum":
        raise NotAFlatMappingCandidate(f"op {for_op!r} is kind={op.get('kind')!r}, not 'einsum'")
    bounds: dict[str, int] = op["bounds"]
    tensor_names = tuple(t["name"] for t in workload["tensors"])

    compute_levels = [h for h in arch["hierarchy"] if h["class"] == "compute"]
    memory_levels = [h for h in arch["hierarchy"] if h["class"] == "memory"]
    if len(compute_levels) != 1:
        raise NotAFlatMappingCandidate(
            f"expected exactly one compute hierarchy level, found {len(compute_levels)}"
        )
    if not memory_levels:
        raise NotAFlatMappingCandidate("architecture has no memory level to place a flat loop nest at")

    array_dims: dict[str, int] = compute_levels[0]["attrs"]["dims"]
    if len(array_dims) != 1:
        raise NotAFlatMappingCandidate(
            f"expected exactly one spatial array dimension, found {len(array_dims)} "
            "(matches every evaluator adapter's own single-spatial-dim v0.1 limit)"
        )
    (array_dim_name, array_size), = array_dims.items()
    temporal_level = memory_levels[-1]["level"]  # closest to compute — "gbuf" by this repo's convention

    return FlatMappingScope(
        workload_id=workload["id"],
        arch_id=arch["id"],
        for_op=for_op,
        bounds=bounds,
        loop_dims=tuple(bounds.keys()),
        tensor_names=tensor_names,
        array_dim_name=array_dim_name,
        array_size=array_size,
        temporal_level=temporal_level,
    )


def build_flat_mapping_candidate(
    scope: FlatMappingScope, *, spatial_dim: str, temporal_order: tuple[str, ...]
) -> MappingCandidate:
    """Build one Mapping IR document for a given spatial-split dim and temporal loop order.
    `spatial_dim` must be one of `scope.loop_dims`; `temporal_order` must be a permutation of
    `scope.loop_dims` (both preconditions, not re-validated here — callers are this module's own
    `generate_flat_mapping_candidates` and search/annealing's neighbor-move generator, both of
    which only ever construct valid inputs).
    """
    spatial_size = _largest_divisor_at_most(scope.bounds[spatial_dim], scope.array_size)
    temporal_sizes = dict(scope.bounds)
    temporal_sizes[spatial_dim] = scope.bounds[spatial_dim] // spatial_size

    loops = [{"dim": d, "size": temporal_sizes[d], "order": i} for i, d in enumerate(temporal_order)]
    mapping_id = (
        f"{scope.workload_id}/{scope.arch_id}/flat-mapping-{spatial_dim}{spatial_size}-"
        f"{'-'.join(temporal_order)}"
    )
    mapping: dict[str, Any] = {
        "schema_version": "0.1.0",
        "id": mapping_id,
        "for_op": scope.for_op,
        "operands": {
            name: [{"level": scope.temporal_level, "loops": loops}] for name in scope.tensor_names
        },
        "spatial": [{"dim": spatial_dim, "array_dim": scope.array_dim_name, "size": spatial_size}],
    }
    return MappingCandidate(
        mapping=mapping, spatial_dim=spatial_dim, spatial_size=spatial_size, temporal_order=temporal_order
    )


def generate_flat_mapping_candidates(
    workload: dict[str, Any], arch: dict[str, Any], *, for_op: str
) -> list[MappingCandidate]:
    """Every (spatial-split-dim × temporal-loop-order) combination for `for_op`, one candidate
    Mapping IR document each. Raises `NotAFlatMappingCandidate` if `workload`/`arch` don't fit
    this strategy's flat, single-spatial-dim scope.
    """
    scope = parse_flat_mapping_scope(workload, arch, for_op=for_op)
    return [
        build_flat_mapping_candidate(scope, spatial_dim=spatial_dim, temporal_order=order)
        for spatial_dim in scope.loop_dims
        for order in itertools.permutations(scope.loop_dims)
    ]
