"""Flux's presets (D968): prebuilt commands a document names for its checks and measurements, so an
application carries no copy of them. `fluxpreset PRESET ...` runs one (D970; in a document it runs
with the interpreter running Flux, like `flux`):

    fluxpreset golden DESIGN.sv --golden golden.py        # RTL against a golden model (Verilator)
    fluxpreset rtl lint DESIGN.sv                         # RTL hardware defects (Verilator)
    fluxpreset rtl measure DESIGN.sv --stage place --clock-ps 1000   # OpenROAD-flow-scripts on ASAP7
    fluxpreset prog time --build "c++ -O2 -o {out} prog.cpp"         # a program's time, count, size
    fluxpreset bench design.py --call f --args "(1000,)"  # a Python function's time
    fluxpreset champsim run prefetcher.h --traces traces --warmup N --sim N   # ChampSim

`fluxpreset` alone lists them; `fluxpreset describe [PRESET]` prints their catalog entries.

INTERFACE -- what every preset keeps (D970; tests/unit/test_flux_presets.py checks it):

- A module (or package) `flux_presets.NAME` with `main(argv) -> int` (its command line) and
  `catalog() -> [entry]` (what `flux tools` and the configurator show). A new preset is a new module.
- Arguments: `fluxpreset NAME [SUBCOMMAND] ARTIFACT [--option VALUE ...]`. The design file is the
  first positional argument; everything else is an option with a default. `--help` describes it.
- stdin: never read. The loop gives a command none.
- stdout: what the loop reads. A check prints each failure on a line of its own, then
  `N failing` (or `N failing of M`) last. A measurement prints `name=value` for each metric it
  reports (one line, or one per line); nothing else on stdout looks like `name=value`.
- stderr: diagnostics for a person; it never carries the count or the numbers.
- Exit code: 0 passed / measured; 1 failed (a check found failures, or a measurement could not
  be taken); 2 wrong arguments; 3 the design did not build, parse or load.
- Files: only in a temporary folder of its own (or the working folder the loop gives it); never
  in the loop's folder.
- Catalog entry: `id`, `role` (check | stage), `title`, `what`, `run` (`fluxpreset NAME ...` with
  `{artifact}` and each param as `{param}`), `params` ({name: {label, default, unit}}), `pass` (a
  check's) or `metrics` (a stage's, {name: unit}), `needs` (programs on PATH), `languages`,
  `kinds`, `short` (a stage's or check's default name); `infer_needs: true` when a document need
  not say `needs:` for it.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

__all__ = ["catalog"]


def catalog() -> list[dict[str, Any]]:
    """Every preset's catalog entries, module by module (a module or package with a `catalog()`)."""
    out: list[dict[str, Any]] = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        listed = getattr(importlib.import_module(f"{__name__}.{info.name}"), "catalog", None)
        if callable(listed):
            out += [dict(t, preset_module=info.name) for t in listed()]
    return out
