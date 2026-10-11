"""Flux's helpers (D968): prebuilt commands a document names for its checks and measurements, so an
application carries no copy of them. Each is a module with a command line:

    python -m flux_helpers.golden DESIGN.sv --golden golden.py        # RTL against a golden model (Verilator)
    python -m flux_helpers.rtl lint DESIGN.sv                         # RTL hardware defects (Verilator)
    python -m flux_helpers.rtl measure DESIGN.sv --stage place --clock-ps 1000   # OpenROAD-flow-scripts on ASAP7
    python -m flux_helpers.prog time --build "c++ -O2 -o {out} prog.cpp"         # a program's time, count, size
    python -m flux_helpers.bench design.py --call f --args "(1000,)"  # a Python function's time
    python -m flux_helpers.champsim run prefetcher.h --traces traces --warmup N --sim N   # ChampSim

In a document: `{python} -m flux_helpers.rtl measure {artifact} --stage synth --clock-ps 1000`.
`flux tools` lists them as the catalog's checks and stages: each module says its own entries with a
`catalog()` function, and `catalog()` here gathers them, so a new helper is a new module.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

__all__ = ["catalog"]


def catalog() -> list[dict[str, Any]]:
    """Every helper's catalog entries, module by module (a module or package with a `catalog()`)."""
    out: list[dict[str, Any]] = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        listed = getattr(importlib.import_module(f"{__name__}.{info.name}"), "catalog", None)
        if callable(listed):
            out += [dict(t, helper_module=info.name) for t in listed()]
    return out
