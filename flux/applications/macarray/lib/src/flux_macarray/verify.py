"""Golden vectors from the workload, and the Verilator verdict on a generated PE.

The workload fixes the shape (einsum `precision`, caller's lanes); golden data is seeded from
the workload's content hash and the shape, so the same request drives the same vectors. Every
design is checked before synthesis, and a pipelined design must take exactly the cycles it
claims.
"""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .rtl_check import Golden

from .config import PeConfig, Shape

DEFAULT_WORKLOAD = (Path(__file__).resolve().parents[5] / "core" / "ir" / "workload" /
                    "examples" / "mlp-gemm0.yaml")


def shape_from_workload(workload: dict[str, Any], lanes: int, *, accumulate: bool = True
                        ) -> Shape:
    """Precision from the workload's one einsum op; lanes from the caller (the array is given)."""
    ops = [op for op in workload.get("ops", []) if op.get("kind") == "einsum"]
    if len(ops) != 1:
        raise ValueError(f"workload {workload.get('id')!r} has {len(ops)} einsum ops; one is "
                         "what a PE study derives its precision from")
    precision = ops[0].get("precision", {})
    return Shape(lanes=lanes, in_bits=int(precision.get("I", 8)),
                 w_bits=int(precision.get("W", 8)), accumulate=accumulate)


def _signed_range(bits: int) -> tuple[int, int]:
    return -(1 << (bits - 1)), (1 << (bits - 1)) - 1


def golden_vectors(shape: Shape, *, seed: str, count: int = 200) -> list[dict[str, Any]]:
    """The PE's (inputs, expected) rows: every lane at the same corner (the widest sums), each
    lane alone at each corner with the others 0 (a lane's own sign handling), then `count`
    random rows with every lane its own value (D868: four corners and two random rows passed a
    PE wrong whenever a3[6:4] == 3'b101 and w3[0]). The accumulator input is drawn from half
    the range so the sum can never overflow the port, which the width already guarantees for
    the products."""
    rng = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big"))
    lo, hi = _signed_range(shape.in_bits)
    wlo, whi = _signed_range(shape.w_bits)
    alo, ahi = _signed_range(shape.acc_bits - 1)
    extremes = [(lo, wlo), (lo, whi), (hi, wlo), (hi, whi)]
    rows: list[tuple[list[int], list[int], int]] = []
    for n, (x, y) in enumerate(extremes):
        rows.append(([x] * shape.lanes, [y] * shape.lanes, alo if n % 2 else ahi))
    for lane in range(shape.lanes):
        for x, y in extremes:
            a, w = [0] * shape.lanes, [0] * shape.lanes
            a[lane], w[lane] = x, y
            rows.append((a, w, rng.randint(alo, ahi)))
    for _ in range(count):
        rows.append(([rng.randint(lo, hi) for _ in range(shape.lanes)],
                     [rng.randint(wlo, whi) for _ in range(shape.lanes)], rng.randint(alo, ahi)))
    out = []
    for a, w, acc_in in rows:
        inputs = {f"a{i}": a[i] for i in range(shape.lanes)}
        inputs.update({f"w{i}": w[i] for i in range(shape.lanes)})
        total = sum(x * y for x, y in zip(a, w))
        if shape.accumulate:
            inputs["acc_in"] = acc_in
            total += acc_in
        out.append({"inputs": inputs, "expected": {"acc": total}})
    return out


def pe_golden(shape: Shape, cfg: PeConfig, vectors: list[dict[str, Any]]) -> "Golden":
    """The PE as a golden model for the harness's shared check: ports, the sum it must
    compute, this world's vectors and, when pipelined, the claimed latency."""
    from .rtl_check import Golden

    ports = ([{"name": f"a{i}", "dir": "in", "bits": max(2, shape.in_bits)} for i in range(shape.lanes)]
             + [{"name": f"w{i}", "dir": "in", "bits": max(2, shape.w_bits)} for i in range(shape.lanes)]
             + ([{"name": "acc_in", "dir": "in", "bits": shape.acc_bits}] if shape.accumulate else [])
             + [{"name": "acc", "dir": "out", "bits": shape.acc_bits}])

    def mac(**x: int) -> dict[str, int]:
        return {"acc": sum(x[f"a{i}"] * x[f"w{i}"] for i in range(shape.lanes)) + x.get("acc_in", 0)}

    return Golden(ports=tuple(ports), fn=mac, vectors=tuple(vectors), clocked=cfg.clocked,
                  latency=cfg.pipeline if cfg.clocked else None,
                  behavior=(f"{shape.lanes}-lane signed multiply-accumulate at int{shape.in_bits} x "
                            f"int{shape.w_bits}: acc = " + ("acc_in + " if shape.accumulate else "")
                            + "sum_i a_i * w_i."))


__all__ = ["DEFAULT_WORKLOAD", "golden_vectors", "pe_golden", "shape_from_workload"]
