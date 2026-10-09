"""Golden-model values and vectors for prototype validation, without an RTL tool dependency."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["EXHAUSTIVE_MAX_BITS", "Golden", "golden_vectors", "ulp_distance"]


@dataclass(frozen=True)
class Golden:
    ports: tuple[dict[str, Any], ...]
    fn: Callable[..., dict[str, int]]
    vectors: tuple[dict[str, Any], ...] = ()   # explicit rows: inputs, or {inputs, expected}
    count: int = 32                            # random rows after the corners
    seed: Any = 0
    clocked: bool = False
    latency: int | None = None                 # a clocked design's cycles, checked when given
    behavior: str = ""
    ulp: dict = field(default_factory=dict)    # output port -> the ULPs it may be off by
    exhaustive: bool = False                   # every input combination, not corners and samples (D865)

    def __post_init__(self) -> None:
        if not self.ports or not callable(self.fn):
            raise ValueError("a golden model is ports = [{name, dir, bits}, ...] and fn(**inputs) -> {output: value}")
        for p in self.ports:
            if not {"name", "dir", "bits"} <= set(p) or p["dir"] not in ("in", "out"):
                raise ValueError(f"port {p!r} needs name, dir (in|out) and bits")
        outs = {p["name"]: int(p["bits"]) for p in self.ports if p["dir"] == "out"}
        for name, n in (self.ulp or {}).items():
            if name not in outs:
                raise ValueError(f"TOLERANCE_ULP names {name!r}, which is not an output port ({', '.join(outs)})")
            if outs[name] not in _FLOAT_WIDTHS:
                raise ValueError(f"TOLERANCE_ULP on {name!r}: a {outs[name]}-bit port is not an IEEE float (16, 32 or 64 bits)")
            if int(n) < 0:
                raise ValueError(f"TOLERANCE_ULP on {name!r} must be 0 or more")
        bits = sum(int(p["bits"]) for p in self.ports if p["dir"] == "in")
        if self.exhaustive and bits > EXHAUSTIVE_MAX_BITS:
            raise ValueError(f"EXHAUSTIVE over {bits} input bits is 2**{bits} vectors; at most {EXHAUSTIVE_MAX_BITS} bits")

    @classmethod
    def from_module(cls, mod: Any) -> "Golden":
        """A `golden.py`: `PORTS`, `golden(**inputs)`; `VECTORS`, `COUNT`, `SEED`, `CLOCK`,
        `LATENCY`, `BEHAVIOR`, `EXHAUSTIVE` when it says them."""
        return cls(ports=tuple(getattr(mod, "PORTS", None) or ()), fn=getattr(mod, "golden", None),
                   vectors=tuple(getattr(mod, "VECTORS", None) or ()), count=int(getattr(mod, "COUNT", 32)),
                   seed=getattr(mod, "SEED", 0), clocked=bool(getattr(mod, "CLOCK", None)),
                   latency=getattr(mod, "LATENCY", None), ulp=dict(getattr(mod, "TOLERANCE_ULP", None) or {}),
                   exhaustive=bool(getattr(mod, "EXHAUSTIVE", False)),
                   behavior=str(getattr(mod, "BEHAVIOR", "") or getattr(getattr(mod, "golden", None), "__doc__", "") or ""))

    @property
    def unsigned(self) -> dict[str, int]:
        return {p["name"]: int(p["bits"]) for p in self.ports if p.get("unsigned")}


#: The most input bits an EXHAUSTIVE golden may have: 2**20 vectors (D865).
EXHAUSTIVE_MAX_BITS = 20


def _corners(bits: int, unsigned: bool) -> list[int]:
    if unsigned:
        return [0, 1, (1 << bits) - 1, 1 << (bits - 1)]
    return [-(1 << (bits - 1)), (1 << (bits - 1)) - 1, 0, -1, 1]


def golden_vectors(g: Golden) -> list[dict[str, Any]]:
    """Explicit rows when the model gives them; every input combination when it says EXHAUSTIVE
    (D865); else every input's corners (each against the others' corners, pairwise), then `count`
    random rows from `seed`; `expected` from `fn`."""
    def row(inputs: dict[str, int], expected: dict[str, int] | None = None) -> dict[str, Any]:
        return {"inputs": dict(inputs), "expected": {k: int(v) for k, v in (expected or g.fn(**inputs)).items()}}

    if g.vectors:
        return [row(r["inputs"], r.get("expected")) if "inputs" in r else row(r) for r in g.vectors]
    ins = [p for p in g.ports if p["dir"] == "in"]
    if g.exhaustive:
        import itertools

        def values(p: dict[str, Any]) -> range:
            n = int(p["bits"])
            return range(0, 1 << n) if p.get("unsigned") else range(-(1 << (n - 1)), 1 << (n - 1))

        return [row(dict(zip([p["name"] for p in ins], combo)))
                for combo in itertools.product(*(values(p) for p in ins))]
    rows: list[dict[str, Any]] = []
    seen: set = set()

    def add(inputs: dict[str, int]) -> None:
        key = tuple(sorted(inputs.items()))
        if key not in seen:
            seen.add(key)
            rows.append(row(inputs))

    corners = {p["name"]: _corners(int(p["bits"]), bool(p.get("unsigned"))) for p in ins}
    base = {p["name"]: 0 for p in ins}
    for p in ins:
        for c in corners[p["name"]]:
            add({**base, p["name"]: c})
    for i, p in enumerate(ins):
        for q in ins[i + 1:]:
            for c in corners[p["name"]]:
                for d in corners[q["name"]]:
                    add({**base, p["name"]: c, q["name"]: d})
    rng = random.Random(g.seed)
    target, tries = len(rows) + g.count, 0
    while len(rows) < target and tries < g.count * 20:
        tries += 1
        add({p["name"]: (rng.randrange(0, 1 << int(p["bits"])) if p.get("unsigned")
                         else rng.randrange(-(1 << (int(p["bits"]) - 1)), 1 << (int(p["bits"]) - 1))) for p in ins})
    return rows


#: IEEE binary widths -> exponent bits (half, single, double).
_FLOAT_WIDTHS = {16: 5, 32: 8, 64: 11}


def ulp_distance(a: int, b: int, bits: int) -> float:
    """How many representable values apart two IEEE bit patterns are: 0 for equal
    patterns, for +0/-0, and for two NaNs of any payload; infinity when one is a NaN."""
    e = _FLOAT_WIDTHS[bits]
    mask, man = (1 << bits) - 1, bits - 1 - e
    a, b = a & mask, b & mask

    def nan(v: int) -> bool:
        return (v >> man) & ((1 << e) - 1) == (1 << e) - 1 and v & ((1 << man) - 1) != 0

    if nan(a) or nan(b):
        return 0.0 if nan(a) and nan(b) else float("inf")

    def ordered(v: int) -> int:
        mag = v & ((1 << (bits - 1)) - 1)
        return -mag if v >> (bits - 1) else mag

    return float(abs(ordered(a) - ordered(b)))
