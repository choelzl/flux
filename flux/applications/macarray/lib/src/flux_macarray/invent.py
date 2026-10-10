"""An invented multiplier's rules and golden model (D365, D798): `invent.problem.yaml` has a
model write one module with a fixed interface (`a`, `w` in, `p` out, signed at the workload's
precision); `steps mult-check` refuses what the rules forbid, runs these vectors and keeps a
passing one in `out/invented/`, where the PE study's space finds it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .config import Shape

if TYPE_CHECKING:
    from .rtl_check import Golden


def sv_refusal(source: str, *, combinational: bool = True) -> str | None:
    """What the rules forbid that the text contains, before any tool runs (D557)."""
    if combinational and re.search(r"always\s*@\s*\(\s*posedge|always_ff|\breg\b.*<=", source):
        return "sequential logic: the module must be combinational"
    if re.search(r"\d+'\s*\(", source):
        return "size casts like 8'(x) are SystemVerilog; Yosys's front end rejects them"
    if "$" in re.sub(r"\$signed|\$unsigned", "", source):
        return "system tasks are not synthesizable"
    return None


def refusal_reason(source: str) -> str | None:
    """What the rules forbid that the text contains, checked before any tool runs: the
    harness's screen (sequential logic, size casts, system tasks), then the study's own rule."""
    why = sv_refusal(source, combinational=True)
    if why:
        return why.replace("the module", "the multiplier")
    if re.search(r"\bassign\s+p\s*=\s*a\s*\*\s*w\s*;", source):
        return "`p = a * w` is the behavioral design already in the space"
    return None


def multiplier_golden(shape: Shape) -> "Golden":
    """The multiplier as a golden model: signed a x w -> p, checked on every input when the
    operands total at most the harness's EXHAUSTIVE_MAX_BITS (D868, D882), else with its shared vectors
    (every corner pairwise, since sign combinations break multipliers, then random)."""
    from .rtl_check import Golden

    return Golden(ports=({"name": "a", "dir": "in", "bits": max(2, shape.in_bits)},
                         {"name": "w", "dir": "in", "bits": max(2, shape.w_bits)},
                         {"name": "p", "dir": "out", "bits": shape.product_bits}),
                  fn=lambda a, w: {"p": a * w}, count=12, seed=f"{shape.in_bits}x{shape.w_bits}",
                  behavior=f"signed int{shape.in_bits} x int{shape.w_bits} -> int{shape.product_bits}")


__all__ = ["multiplier_golden", "refusal_reason"]
