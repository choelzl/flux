"""Per-op engine-assignment candidates for composition DSE (docs/decisions.md D236).

The physical model, stated because the numbers only mean something under it: each einsum op in a
multi-op workload gets its OWN dedicated engine, sized independently. A chained workload then
composes as: latency and energy are sums over the chain (each op runs once, sequentially, on its
own engine), and area is the sum of the engines (they are separate silicon). Widening one op's
engine buys that op's latency and costs total area — the trade-off an Objective can actually
drive, which a uniform-width sweep structurally cannot express.

Geometry is delegated: each per-op arch is `generate_width_candidates`' own product, so a
composition candidate is exactly N of the documents every evaluator already accepts — this
module adds the assignment bookkeeping, never a new architecture shape.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

from .candidates import generate_width_candidates


class NotACompositionCandidate(Exception):
    """The workload or base architecture falls outside this generator's scope."""


@dataclass(frozen=True, slots=True)
class CompositionCandidate:
    """One per-op width assignment. `arch` is the composition document: not Architecture IR but
    a typed wrapper carrying one real Architecture IR document per op — stored under its own
    document kind ("composition"), never passed whole to a single-arch evaluator."""

    arch: dict[str, Any]
    assignment: dict[str, Any]  # op id -> engine width (D236), or {width, size_kb} (D251)

    def to_dict(self) -> dict[str, Any]:
        return {"assignment": dict(self.assignment), "arch": self.arch}


def einsum_op_ids(workload: dict[str, Any]) -> list[str]:
    ids = [op["id"] for op in workload.get("ops", []) if op.get("kind") == "einsum"]
    if not ids:
        raise NotACompositionCandidate(
            f"workload {workload.get('id')!r} has no einsum ops to assign engines to"
        )
    if len(ids) != len(set(ids)):
        raise NotACompositionCandidate(f"duplicate op ids in {ids} — assignment would be ambiguous")
    return ids


def generate_system_candidates(
    base_arch: dict[str, Any], workload: dict[str, Any], widths: list[int],
    level: str, sizes_kb: list[float], *, word_width_bits: int | None = None,
) -> list[CompositionCandidate]:
    """System-level composition (docs/decisions.md D251): every einsum op gets its own engine
    sized in BOTH compute width AND the named memory level — the per-op grid over
    (widths x sizes_kb) points. The memory analog of D236's area story: a small op deserves a
    small buffer, and a uniform memory size cannot express that. Geometry is delegated to
    `generate_joint_candidates` (the proven width x memory single-arch generator), so every
    engine is a document every evaluator already accepts; the composed evaluator, calibration
    and caching machinery run unchanged on top."""
    from .memory_candidates import generate_joint_candidates

    op_ids = einsum_op_ids(workload)
    arch_by_point = {
        (c.width, c.size_kb): c.arch
        for c in generate_joint_candidates(base_arch, widths, level, sizes_kb)
    }
    if word_width_bits is not None:
        # The searched level gains the SRAM interface width CACTI needs (D252): the cacti
        # adapter refuses to guess it (size_kb alone does not determine depth), and this is
        # the one place that knows an engine's buffers are being sized for characterization.
        for arch in arch_by_point.values():
            node = next(n for n in arch["hierarchy"] if n.get("level") == level)
            node.setdefault("attrs", {})["word_width_bits"] = word_width_bits
    points = sorted(arch_by_point)
    candidates = []
    for combo in itertools.product(points, repeat=len(op_ids)):
        assignment = {
            op_id: {"width": w, "size_kb": s}
            for op_id, (w, s) in zip(op_ids, combo)
        }
        composition = {
            "kind": "engine_per_op",
            "id": f"{base_arch.get('id', 'arch')}-system-" + "-".join(
                f"{w}x{s:g}" for (w, s) in combo),
            "base_arch_id": base_arch.get("id"),
            "memory_level": level,
            "components": {op_id: arch_by_point[(a['width'], a['size_kb'])]
                           for op_id, a in assignment.items()},
            "assignment": assignment,
        }
        candidates.append(CompositionCandidate(arch=composition, assignment=assignment))
    return candidates


def generate_composition_candidates(
    base_arch: dict[str, Any], workload: dict[str, Any], widths: list[int] | None = None,
    *, widths_per_op: dict[str, list[int]] | None = None,
) -> list[CompositionCandidate]:
    """One candidate per point of the full per-op width grid: every op assigned an engine that
    is `base_arch` at one of ITS OWN allowed widths — `widths_per_op[op_id]` where given,
    `widths` otherwise (docs/decisions.md D241: a 10-wide classifier head admits engine widths
    {2, 10} while its heavy layers want {8, 16}; a single global list cannot express a chain
    whose ops have different divisibility). Deliberately the complete product — the campaign's
    budget latch and strategies decide how much of it to buy, not the geometry.
    """
    op_ids = einsum_op_ids(workload)
    per_op = widths_per_op or {}
    unknown = sorted(set(per_op) - set(op_ids))
    if unknown:
        raise NotACompositionCandidate(
            f"widths_per_op names ops {unknown} that are not in workload "
            f"{workload.get('id')!r} (its einsum ops: {op_ids})"
        )
    lists: list[list[int]] = []
    for op_id in op_ids:
        allowed = per_op.get(op_id, widths)
        if not allowed:
            raise NotACompositionCandidate(
                f"op {op_id!r} has no allowed widths: give it a widths_per_op entry or "
                "provide a global widths list"
            )
        lists.append(list(allowed))
    every_width = sorted({w for ws in lists for w in ws})
    arch_by_width = {c.width: c.arch for c in generate_width_candidates(base_arch, every_width)}

    candidates = []
    for point in itertools.product(*lists):
        assignment = dict(zip(op_ids, point))
        composition = {
            "kind": "engine_per_op",
            "id": f"{base_arch.get('id', 'arch')}-composed-" + "-".join(
                str(w) for w in point),
            "base_arch_id": base_arch.get("id"),
            "components": {op_id: arch_by_width[w] for op_id, w in assignment.items()},
            "assignment": assignment,
        }
        candidates.append(CompositionCandidate(arch=composition, assignment=assignment))
    return candidates
