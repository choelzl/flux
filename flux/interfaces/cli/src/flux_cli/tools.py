"""`flux tools` (D654): the tool catalog, the checks a gate runs and the stages that measure."""

from __future__ import annotations

import argparse
import json

__all__ = ["cmd_tools"]


def cmd_tools(args: argparse.Namespace) -> int:
    from flux_loop.toolbox import catalog

    tools = catalog()
    if args.json:
        print(json.dumps(tools, indent=1))
        return 0
    for role, head in (("check", "CHECKS (a gate runs them in order; the first that fails refuses the design)"),
                       ("stage", "STAGES (cheapest first; a `cutoff` says what goes on to the next)")):
        print(head)
        for t in (t for t in tools if t["role"] == role):
            print(f"  {t['id']}: {t['title']} -- {t['what']}")
            if "run" in t:
                print(f"    run: {t['run']}")
            else:                     # an evaluator stage (D663): its keys, and the document's
                print("    stage: " + ", ".join(f"{k}: {v}" for k, v in t["stage"].items())
                      + "; document: " + ", ".join(f"{k}: {v}" for k, v in t.get("document", {}).items()))
            for name, p in t["params"].items():
                print(f"      {{{name}}}: {p['label']}" + (f", default {p['default']}" if p["default"] != "" else "")
                      + (f" {p['unit']}" if p["unit"] else ""))
            if role == "check":
                print(f"    gate: {t['pass']}")
            else:
                print("    metrics: " + (", ".join(f"{m} ({u})" if u else m for m, u in t["metrics"].items())
                                         or "the ones you name"))
            if t["needs"]:
                print(f"    needs: {', '.join(t['needs'])}")
        print()
    return 0
