"""Golden vectors from the workload, and the Verilator verdict on a generated PE.

The workload IR fixes the shape (its einsum's `precision`, the lanes the caller asks for) and
the golden data is seeded from the workload's content hash and the shape, so the same request
always drives the same vectors (D223's discipline). Every generated design is checked here
before any synthesis time is spent on it -- a PE that computes the wrong sum has no area worth
knowing -- and a pipelined design must also take exactly the number of cycles it claims.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PeConfig, Shape
from .rtl import Design

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


def golden_vectors(shape: Shape, *, seed: str, count: int = 6) -> list[dict[str, Any]]:
    """`count` (inputs, expected) pairs. The accumulator input is drawn from half the range so
    the sum can never overflow the port, which the width already guarantees for the products."""
    rng = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big"))
    lo, hi = _signed_range(shape.in_bits)
    wlo, whi = _signed_range(shape.w_bits)
    alo, ahi = _signed_range(shape.acc_bits - 1)
    out = []
    extremes = [(lo, wlo), (lo, whi), (hi, wlo), (hi, whi)]
    for n in range(count):
        if n < len(extremes):
            a = [extremes[n][0]] * shape.lanes
            w = [extremes[n][1]] * shape.lanes
        else:
            a = [rng.randint(lo, hi) for _ in range(shape.lanes)]
            w = [rng.randint(wlo, whi) for _ in range(shape.lanes)]
        inputs = {f"a{i}": a[i] for i in range(shape.lanes)}
        inputs.update({f"w{i}": w[i] for i in range(shape.lanes)})
        total = sum(x * y for x, y in zip(a, w))
        if shape.accumulate:
            inputs["acc_in"] = rng.randint(alo, ahi) if n >= len(extremes) else (alo if n % 2 else ahi)
            total += inputs["acc_in"]
        out.append({"inputs": inputs, "expected": {"acc": total}})
    return out


def pe_spec(shape: Shape, cfg: PeConfig, vectors: list[dict[str, Any]], *,
            module_name: str = "mac_pe") -> dict[str, Any]:
    """A `DesignSpec` document for the harness: the PE's ports, the vectors, the clocking."""
    ports = ([{"name": f"a{i}", "dir": "in", "dtype": "int", "bits": max(2, shape.in_bits)}
              for i in range(shape.lanes)]
             + [{"name": f"w{i}", "dir": "in", "dtype": "int", "bits": max(2, shape.w_bits)}
                for i in range(shape.lanes)]
             + ([{"name": "acc_in", "dir": "in", "dtype": "int", "bits": shape.acc_bits}]
                if shape.accumulate else [])
             + [{"name": "acc", "dir": "out", "dtype": "int", "bits": shape.acc_bits}])
    return {
        "schema_version": "0.1.0",
        "id": f"macarray/{cfg.label}/{shape.lanes}x{shape.in_bits}x{shape.w_bits}",
        "module_name": module_name,
        "ports": ports,
        "behavior": (f"{shape.lanes}-lane signed multiply-accumulate at int{shape.in_bits} x "
                     f"int{shape.w_bits}: acc = " + ("acc_in + " if shape.accumulate else "")
                     + "sum_i a_i * w_i."),
        "test_vectors": vectors,
        "is_clocked": cfg.clocked,
        "measures_latency": cfg.clocked,
    }


@dataclass(frozen=True)
class Verdict:
    ok: bool
    detail: str
    latency_cycles: int | None = None


def verify(design: Design, vectors: list[dict[str, Any]], *, timeout_s: float = 180.0
           ) -> Verdict:
    """Real Verilator on the generated PE against the golden vectors, latency checked too."""
    from flux_codegen_rtl_harness import CompileError, compile_and_run, design_spec_from_dict

    spec = design_spec_from_dict(pe_spec(design.shape, design.config, vectors,
                                         module_name=design.module_name))
    try:
        run = compile_and_run(design.source, spec, timeout_s=timeout_s,
                              extra_sources=design.extra_sources or None)
    except CompileError as exc:
        return Verdict(ok=False, detail=f"did not compile: {str(exc)[:300]}")
    if not run.all_passed:
        why = run.failing_vector_lines[0] if run.failing_vector_lines else (
            run.compile_stderr or run.stderr or "no vector passed")
        return Verdict(ok=False,
                       detail=f"{run.passed_vectors}/{run.total_vectors} vectors: {why[:300]}")
    latency = None
    if design.config.clocked and run.cycles_per_vector:
        # The harness counts clock edges AFTER the one that consumed `start` (D115): a PE whose
        # output is valid right after that edge -- one register stage -- reports 0. So a design
        # of `pipeline` stages must measure `pipeline - 1`; anything else is a wiring bug in
        # the generated handshake, and is refused rather than reported as a different latency.
        measured = max(run.cycles_per_vector)
        if measured != design.config.pipeline - 1:
            return Verdict(ok=False, latency_cycles=measured + 1,
                           detail=f"claims {design.config.pipeline} cycle(s) of latency, "
                                  f"measured {measured + 1}")
        latency = design.config.pipeline
    return Verdict(ok=True, detail=f"{run.passed_vectors}/{run.total_vectors} vectors",
                   latency_cycles=latency)


__all__ = ["DEFAULT_WORKLOAD", "Verdict", "golden_vectors", "pe_spec", "shape_from_workload",
           "verify"]
