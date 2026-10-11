"""`fluxhelper <helper> ...` (= `python -m flux_helpers <helper> ...`, D970): run one of Flux's helpers.

    fluxhelper                      # the helpers, one line each
    fluxhelper describe [HELPER]    # their catalog entries, as JSON
    fluxhelper rtl measure design.sv --stage place --clock-ps 1000

In a document `fluxhelper` runs with the interpreter running Flux (like `flux`), so nothing needs
to be on PATH. Every helper keeps one interface (see INTERFACE in flux_helpers/__init__.py).
"""

from __future__ import annotations

import importlib
import json
import sys


def _helpers() -> dict[str, list[dict]]:
    from . import catalog

    out: dict[str, list[dict]] = {}
    for entry in catalog():
        out.setdefault(entry["helper_module"], []).append(entry)
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    helpers = _helpers()
    if not args or args[0] in ("-h", "--help", "list"):
        print("fluxhelper HELPER ... -- Flux's prebuilt checks and measurements (fluxhelper HELPER --help for one):")
        for name, entries in helpers.items():
            print(f"  {name:10} " + "; ".join(f"{e['role']} {e['id']}: {e['title']}" for e in entries))
        return 0
    if args[0] == "describe":
        wanted = args[1:] or list(helpers)
        unknown = [w for w in wanted if w not in helpers]
        if unknown:
            print(f"no helper {', '.join(unknown)}; the helpers are {', '.join(helpers)}", file=sys.stderr)
            return 2
        print(json.dumps([e for w in wanted for e in helpers[w]], indent=1))
        return 0
    name, rest = args[0], args[1:]
    if name not in helpers or name.startswith("_"):
        print(f"no helper {name!r}; the helpers are {', '.join(helpers)}", file=sys.stderr)
        return 2
    return int(importlib.import_module(f"flux_helpers.{name}").main(rest) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
