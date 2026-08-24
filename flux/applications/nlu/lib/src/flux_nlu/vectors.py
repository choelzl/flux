"""Test vectors for the NLU study (D408): a floor the loop cannot lower, a seam the
loop raises through, and exhaustion as the last word.

Three tiers, honest about who authored what:

  FLOOR      deterministic coverage the FRAMEWORK guarantees -- every exponent bucket,
             both signs, subnormals, the boundaries where methods break (powers of
             two, just-under/over one, max/min normals, specials). A design cannot
             pass by being tested only where its author looked.
  AUTHORED   the model's own adversarial vectors (the loop "builds its unit tests"):
             validated hex FP16 values, ADDED to the floor, never replacing it, and
             recorded so a resumed run keeps its accumulated test suite.
  EXHAUSTIVE all 65536 inputs -- the confirm rung's correctness word, and the number
             the report quotes. The tiers below only exist to fail fast and cheap.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

__all__ = ["floor_vectors", "parse_authored", "merge"]

_BOUNDARY = (
    0x0000, 0x8000,          # +/- zero
    0x0001, 0x8001,          # smallest subnormals
    0x03FF, 0x83FF,          # largest subnormals
    0x0400, 0x8400,          # smallest normals
    0x3BFF, 0xBBFF,          # just under +/-1
    0x3C00, 0xBC00,          # +/-1
    0x3C01, 0xBC01,          # just over +/-1
    0x7BFF, 0xFBFF,          # +/- max normal
    0x7C00, 0xFC00,          # +/- inf
    0x7E00,                  # a quiet NaN
)


def floor_vectors(seed: int = 0, per_exponent: int = 6) -> np.ndarray:
    """The guaranteed coverage floor: every exponent value x both signs, several
    mantissas each (endpoints always, the rest seeded-random so two runs share a
    floor), plus the boundary list. Deduplicated, sorted, deterministic."""
    rng = np.random.default_rng(seed)
    out = list(_BOUNDARY)
    for sign in (0x0000, 0x8000):
        for exp in range(0, 32):
            base = sign | (exp << 10)
            mans = {0, 0x3FF, 0x200}
            while len(mans) < per_exponent:
                mans.add(int(rng.integers(0, 0x400)))
            out.extend(base | m for m in sorted(mans))
    return np.array(sorted(set(out)), dtype=np.uint16)


def parse_authored(reply: str) -> tuple[dict[str, np.ndarray], list[str]]:
    """The test-author role's reply -> {op: vectors}, refusals with reasons.

    Expected shape: {"vectors": {"exp": ["0x3c00", ...], ...}, "why": "..."} -- hex
    strings because the model reasons about bit patterns here, not values. Anything
    unparseable is refused with the reason (a refusal is a teaching signal, D297);
    valid entries survive a partly-bad reply rather than dying with it."""
    from flux_llm import strip_markdown_fence

    refused: list[str] = []
    try:
        doc = json.loads(strip_markdown_fence(reply))
        raw: dict[str, Any] = dict(doc.get("vectors") or {})
    except Exception as exc:  # noqa: BLE001
        return {}, [f"unparseable test-author reply ({exc})"]
    out: dict[str, np.ndarray] = {}
    for op, values in raw.items():
        good: list[int] = []
        for v in values if isinstance(values, list) else []:
            try:
                iv = int(str(v), 16)
            except ValueError:
                refused.append(f"{op}: {v!r} is not a hex FP16 pattern")
                continue
            if 0 <= iv <= 0xFFFF:
                good.append(iv)
            else:
                refused.append(f"{op}: {v!r} does not fit in 16 bits")
        if good:
            out[op] = np.array(sorted(set(good)), dtype=np.uint16)
    return out, refused


def merge(*sets: np.ndarray) -> np.ndarray:
    """Union of vector sets, sorted and deduplicated -- the floor plus everything
    every author added, which is why authored vectors can only ever RAISE coverage."""
    stacked = np.concatenate([s for s in sets if s is not None and s.size])
    return np.unique(stacked.astype(np.uint16))
