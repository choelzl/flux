"""How a helper describes itself in the tool catalog (D654, D968): the same entry as any tool's."""

from __future__ import annotations

from typing import Any

N_FAILING = "passes when it prints `0 failing`; exit 3 = did not build"
HDL = ["systemverilog", "verilog"]


def param(label: str, default: Any, unit: str = "") -> dict[str, Any]:
    return {"label": label, "default": default, "unit": unit}


def metrics(*names: str) -> dict[str, str]:
    from flux_loop.objective import UNITS

    return {n: UNITS.get(n, "ps" if n.endswith("_ps") else "") for n in names}
