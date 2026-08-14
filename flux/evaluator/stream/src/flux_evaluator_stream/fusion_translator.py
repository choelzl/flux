"""Flux Mapping IR `fusion` block → Stream `intra_core_tiling` (docs/decisions.md D103): the
layer-fusion capability D80–D82's own README named as the not-wired remainder, and the first real
consumer of the Mapping IR's `fusion` block (in the IR schema since day one, docs/ir.md, never
translated by any adapter before this).

Empirically pinned facts this translation stands on (real Stream runs, not read from docs):
- Stream's `intra_core_tiling` entries are `{"dim": "<node>.<D-dim>", "tile": <size>}`, and its
  per-group filter is `entry["dim"].split(".")[0] in group_node_names` — a FIRST-dot split, so a
  node name containing a dot can never match. Flux op ids conventionally contain dots
  (`ffn.down`), and the ONNX exporter names nodes by op id — entries built from raw op ids are
  silently dropped (verified: identical latency with and without them). The adapter therefore
  sanitizes node names dot→underscore before handing the model to Stream, and this module builds
  entries against the sanitized names.
- `D0` is the Gemm row dim — the chained/batch dim every op in a Flux chained-GEMM workload
  shares — and `tile` is a tile SIZE, not a split count: tiling with size == the full dim bound
  reproduces the trivial unfused latency exactly (1080.0 = 1080.0 on the two-op reference), and
  finer tiles genuinely pipeline (1040.0 at size 2, 976.0 at size 1).

v0.1 contract, every violation a loud `NotExpressibleError` — no silent ignoring (the D27
lesson: an input field an adapter accepts but doesn't honor is worse than a rejection):
`fusion.group` must be exactly the workload's einsum op ids; `fusion.tile` exactly one entry
whose dim is every group op's own row dim and whose size divides that dim's bound; `operands`
must be empty; `spatial`/`placement` absent; `for_op` one of the group ops.
"""

from __future__ import annotations

from typing import Any

from .errors import NotExpressibleError


def sanitize_node_name(op_id: str) -> str:
    return op_id.replace(".", "_")


def _row_dim(op: dict[str, Any]) -> str:
    """The op's output row dim — the first dim of the einsum output (`"B C, C H -> B H"` → B),
    which is the chained dim layer fusion tiles across."""
    expr = op.get("expr", "")
    _, _, out = expr.partition("->")
    out_dims = out.split()
    if not out_dims:
        raise NotExpressibleError(f"op {op.get('id')!r}: cannot determine row dim from expr {expr!r}")
    return out_dims[0]


def mapping_fusion_to_intra_core_tiling(
    mapping: dict[str, Any], workload: dict[str, Any]
) -> list[dict[str, Any]]:
    """Translate a fusion-only Flux Mapping IR document into Stream `intra_core_tiling` entries.
    See module docstring for the v0.1 contract; every violation raises `NotExpressibleError`."""
    if mapping.get("operands"):
        raise NotExpressibleError(
            "StreamEvaluator translates only the mapping's `fusion` block — per-operand loop "
            "nests have no Stream translation target (Stream's value is its own automatic "
            "allocation+mapping search). Refusing to silently ignore non-empty `operands`."
        )
    for banned in ("spatial", "placement"):
        if banned in mapping:
            raise NotExpressibleError(
                f"StreamEvaluator has no translation target for the mapping's {banned!r} block — "
                "refusing to silently ignore it."
            )

    fusion = mapping.get("fusion")
    if not isinstance(fusion, dict) or "group" not in fusion or "tile" not in fusion:
        raise NotExpressibleError(
            "StreamEvaluator accepts a mapping only for its `fusion` block "
            "({group: [op ids], tile: {<row dim>: <tile size>}}) — leave Candidate.mapping as "
            "None for Stream's own trivial per-group default."
        )

    ops = {op.get("id"): op for op in workload.get("ops", []) if op.get("kind") == "einsum"}
    group = list(fusion["group"])
    if set(group) != set(ops):
        raise NotExpressibleError(
            f"fusion.group {sorted(set(group))} must be exactly the workload's einsum op ids "
            f"{sorted(set(ops))} — Stream v0.1 here fuses the whole chain as one group; partial "
            "groups have no translation yet."
        )
    if mapping.get("for_op") not in ops:
        raise NotExpressibleError(
            f"mapping.for_op={mapping.get('for_op')!r} does not name a workload op; use one of "
            f"the fusion group's own ops {sorted(set(ops))}."
        )

    tile = dict(fusion["tile"])
    if len(tile) != 1:
        raise NotExpressibleError(
            f"fusion.tile must have exactly one entry (the shared row dim) — got {sorted(tile)}. "
            "Stream's intra-core tiling here tiles the one chained dim every group op shares."
        )
    (tile_dim, tile_size), = tile.items()

    entries: list[dict[str, Any]] = []
    for op_id in group:
        op = ops[op_id]
        row = _row_dim(op)
        if row != tile_dim:
            raise NotExpressibleError(
                f"fusion.tile names dim {tile_dim!r} but op {op_id!r}'s own row dim is {row!r} — "
                "the tiled dim must be every group op's shared row (chained) dim."
            )
        bound = op.get("bounds", {}).get(tile_dim)
        if not isinstance(bound, int):
            raise NotExpressibleError(
                f"op {op_id!r} has no static integer bound for {tile_dim!r} (got {bound!r}) — "
                "dynamic bounds have no fusion-tiling translation."
            )
        if not (isinstance(tile_size, int) and 1 <= tile_size <= bound and bound % tile_size == 0):
            raise NotExpressibleError(
                f"fusion.tile {tile_dim}={tile_size!r} must be an integer tile SIZE in [1, {bound}] "
                f"dividing op {op_id!r}'s {tile_dim} bound ({bound}) evenly — Stream's own tiling "
                "has no ragged-final-tile support this adapter is willing to hand it."
            )
        # "D0" is the Gemm row dim, empirically pinned (see module docstring) — the sanitized
        # node name is what the adapter renames the ONNX node to before Stream parses it.
        entries.append({"dim": f"{sanitize_node_name(op_id)}.D0", "tile": tile_size})
    return entries
