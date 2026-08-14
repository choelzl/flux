"""Layer-fusion tile-size candidates (docs/decisions.md D104) — the first *mapping*-space search
axis in this repo. Every other generator here varies the architecture and leaves `mapping=None`;
this one holds the architecture fixed and varies a Mapping IR `fusion` block's tile size, which
`evaluators/stream` translates into real Stream `intra_core_tiling` (D103).

**Why this axis is worth searching at all, measured before it was built** (real Stream, dual-core
architecture, a two-op B=16 chain, bit-identical across repeated runs):

    unfused / tile=16   3768.0      tile=4    3648.0  (1.033x)
    tile=8              3568.0      tile=2    3824.0  (0.985x — WORSE than not fusing)
    (best, 1.056x)      tile=1      3736.0  (1.009x)

The space is **non-monotone with an interior optimum**, and a badly-chosen tile is genuinely
worse than not fusing. Two consequences: a search is justified (a monotone space would need only
"pick the smallest"), and the intuition from the smaller B=4 chain — where tile=1 won — does not
generalize. Tile size cannot be picked by rule of thumb; it has to be measured per workload.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


class NotAFusionSweepCandidate(ValueError):
    """The (workload, tile-dim) pair can't be swept: not a multi-op chain, or the named dim isn't
    a shared static row dim of every op. Raised at generation time — before any real evaluator
    call — the same fail-early contract every other generator module here uses."""


@dataclass(frozen=True, slots=True)
class FusionTileCandidate:
    """Carries `mapping` alongside `arch`: the optional attribute `dse.run_architecture_dse`
    reads (D104). `arch` is the untouched base architecture — this axis varies only the mapping.
    """

    arch: dict[str, Any]
    mapping: dict[str, Any]
    tile_dim: str
    tile_size: int
    op_group: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "arch": self.arch, "mapping": self.mapping, "tile_dim": self.tile_dim,
            "tile_size": self.tile_size, "op_group": list(self.op_group),
        }


def _row_dim(op: dict[str, Any]) -> str:
    _, _, out = op.get("expr", "").partition("->")
    dims = out.split()
    if not dims:
        raise NotAFusionSweepCandidate(f"op {op.get('id')!r}: cannot read a row dim from its expr")
    return dims[0]


def divisor_tile_sizes(bound: int) -> list[int]:
    """Every tile size that divides `bound` evenly, ascending — the whole feasible space for one
    dim (Stream has no ragged-final-tile support, so non-divisors aren't candidates at all)."""
    return [t for t in range(1, bound + 1) if bound % t == 0]


def generate_fusion_tile_candidates(
    workload: dict[str, Any],
    base_arch: dict[str, Any],
    *,
    tile_sizes: list[int] | None = None,
) -> list[FusionTileCandidate]:
    """One `FusionTileCandidate` per tile size — each a fusion-only Mapping IR document fusing
    every einsum op in `workload` as one group, tiled along their shared row dim. `tile_sizes`
    defaults to every divisor of that dim's bound (the complete feasible space).

    Raises `NotAFusionSweepCandidate` for a workload this axis doesn't apply to: fewer than two
    einsum ops (nothing to fuse), ops that don't share one row dim, a non-static bound, or a
    requested size that doesn't divide it evenly.
    """
    ops = [op for op in workload.get("ops", []) if op.get("kind") == "einsum"]
    if len(ops) < 2:
        raise NotAFusionSweepCandidate(
            f"workload {workload.get('id')!r} has {len(ops)} einsum ops — layer fusion needs at "
            "least two chained ops to fuse."
        )

    row_dims = {_row_dim(op) for op in ops}
    if len(row_dims) != 1:
        raise NotAFusionSweepCandidate(
            f"ops do not share one row dim (found {sorted(row_dims)}) — this axis tiles the one "
            "chained dim every fused op has in common."
        )
    tile_dim = row_dims.pop()

    # A list, not a set: a dynamic bound is a `{"dyn": [lo, hi]}` dict — unhashable, so building
    # a set here raised a bare TypeError instead of this module's own typed error (found by this
    # module's own test, the same bare-exception class D96 swept out of the tree).
    bounds = [op.get("bounds", {}).get(tile_dim) for op in ops]
    if not all(isinstance(b, int) for b in bounds):
        raise NotAFusionSweepCandidate(
            f"{tile_dim!r} is not a static integer bound in every op (found {bounds}) — dynamic "
            "bounds have no fusion-tiling translation."
        )
    if len(set(bounds)) != 1:
        raise NotAFusionSweepCandidate(
            f"ops disagree on {tile_dim!r}'s bound (found {bounds}) — a fused group must share "
            "one chained-dim extent."
        )
    bound = bounds[0]

    sizes = divisor_tile_sizes(bound) if tile_sizes is None else list(tile_sizes)
    bad = [t for t in sizes if not (isinstance(t, int) and 1 <= t <= bound and bound % t == 0)]
    if bad:
        raise NotAFusionSweepCandidate(
            f"tile sizes {bad} do not divide {tile_dim}={bound} evenly (or are out of range) — "
            "Stream has no ragged-final-tile support."
        )

    group = tuple(op["id"] for op in ops)
    base_id = workload.get("id", "workload")
    candidates: list[FusionTileCandidate] = []
    for size in sizes:
        mapping = {
            "schema_version": "0.1.0",
            "id": f"{base_id}/fusion-{tile_dim}{size}",
            "for_op": group[0],
            "operands": {},
            "fusion": {"group": list(group), "tile": {tile_dim: size}},
        }
        candidates.append(FusionTileCandidate(
            arch=copy.deepcopy(base_arch), mapping=mapping,
            tile_dim=tile_dim, tile_size=size, op_group=group,
        ))
    return candidates
