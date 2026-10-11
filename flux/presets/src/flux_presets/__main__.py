"""`fluxpreset <preset> ...` (= `python -m flux_presets <preset> ...`, D970): run one of Flux's presets.

    fluxpreset                      # the presets, one line each
    fluxpreset describe [PRESET]    # their catalog entries, as JSON
    fluxpreset rtl measure design.sv --stage place --clock-ps 1000

In a document `fluxpreset` runs with the interpreter running Flux (like `flux`), so nothing needs
to be on PATH. Every preset keeps one interface (see INTERFACE in flux_presets/__init__.py).
"""

from __future__ import annotations

import importlib
import json
import sys


def _presets() -> dict[str, list[dict]]:
    from . import catalog

    out: dict[str, list[dict]] = {}
    for entry in catalog():
        out.setdefault(entry["preset_module"], []).append(entry)
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    presets = _presets()
    if not args or args[0] in ("-h", "--help", "list"):
        print("fluxpreset PRESET ... -- Flux's prebuilt checks and measurements (fluxpreset PRESET --help for one):")
        for name, entries in presets.items():
            print(f"  {name:10} " + "; ".join(f"{e['role']} {e['id']}: {e['title']}" for e in entries))
        return 0
    if args[0] == "describe":
        wanted = args[1:] or list(presets)
        unknown = [w for w in wanted if w not in presets]
        if unknown:
            print(f"no preset {', '.join(unknown)}; the presets are {', '.join(presets)}", file=sys.stderr)
            return 2
        print(json.dumps([e for w in wanted for e in presets[w]], indent=1))
        return 0
    name, rest = args[0], args[1:]
    if name not in presets or name.startswith("_"):
        print(f"no preset {name!r}; the presets are {', '.join(presets)}", file=sys.stderr)
        return 2
    return int(importlib.import_module(f"flux_presets.{name}").main(rest) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
