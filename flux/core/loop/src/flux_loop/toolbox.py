"""The tool catalog (D654): the checks a gate may run and the stages a document may measure with,
in one place. `flux tools` prints it, `flux tools --json` writes it for the loop crafter
(website/docs/assets/tools.json, kept equal by a unit test).

Each entry: `id`, `role` ("check" or "stage"), `title` and `what` in plain words, `run` (the
command template: the loop's placeholders `{artifact}`, `{home}`, `{python}`, and the entry's
own `params` by name, which the author fills), `params` (name -> label, default, unit),
`metrics` (a stage's, name -> unit), `needs` (tools on PATH), `pass` (a check's pass rule),
`languages` (the artifacts it fits) and `kinds` (the crafter's kinds of problem it belongs to).
Every stage is a command (D954: no evaluator stages). `TOOLS` are the generic ones (your scripts,
any command); Flux's helpers list their own entries (`flux_helpers.catalog()`, marked `helper`,
D968), so the loop knows how a helper is named and run, not which helpers there are.
"""

from __future__ import annotations

import re
from typing import Any

from .objective import UNITS

__all__ = ["TOOLS", "catalog", "fill", "helpers", "tool"]

_N_FAILING = "passes when it prints `0 failing`; exit 3 = did not build"


def _p(label: str, default: Any, unit: str = "") -> dict[str, Any]:
    return {"label": label, "default": default, "unit": unit}


def _m(*names: str) -> dict[str, str]:
    return {n: UNITS.get(n, "ps" if n.endswith("_ps") else "") for n in names}


TOOLS: list[dict[str, Any]] = [
    # ---- your own scripts and commands (D654); the prebuilt ones are Flux's helpers (D968)
    {"id": "python-test-script", "role": "check", "title": "A test script of yours",
     "what": "A Python script beside the document runs the design on known cases and prints `N failing`.",
     "run": "{python} {script} {artifact}", "params": {"script": _p("Script", "{home}/check.py")},
     "needs": [], "pass": _N_FAILING, "languages": ["python", "text", "ini", "yaml", "c", "cpp"],
     "kinds": ["program", "python", "champsim", "own"]},
    {"id": "custom-check", "role": "check", "title": "Any command",
     "what": "Your own command; it prints `N failing` (or say `count_re` / `fail_re`), or its exit code decides.",
     "run": "{command}", "params": {"command": _p("Command", "")},
     "needs": [], "pass": "passes when it prints `0 failing`, or else exits 0; exit 3 = did not build",
     "languages": [], "kinds": ["own"]},
    {"id": "bench-script", "role": "stage", "title": "A timing script of yours",
     "what": "A Python script beside the document runs the design and prints `time_ms=...`.",
     "run": "{python} {script} {artifact}", "params": {"script": _p("Script", "{home}/bench.py")},
     "metrics": _m("time_ms"), "needs": [], "languages": ["python", "text", "c", "cpp"], "kinds": ["program", "python"]},
    {"id": "custom-stage", "role": "stage", "title": "Any command",
     "what": "Your own command; it prints `name=value` for each metric it measures, listed as the stage's `metrics`.",
     "run": "{command}", "params": {"command": _p("Command", "")},
     "metrics": {}, "needs": [], "languages": [], "kinds": ["own"]},
]


def helpers() -> list[dict[str, Any]]:
    """Flux's helpers as catalog entries, each marked `helper` (D968): what `flux_helpers.catalog()`
    says it has. The loop knows the framework -- a helper is `{python} -m flux_helpers.<name>`
    with an entry like any tool's -- never the helpers themselves; none when it is not installed."""
    try:
        from flux_helpers import catalog as helper_catalog
    except ImportError:
        return []
    return [dict(t, helper=True) for t in helper_catalog()]


def catalog() -> list[dict[str, Any]]:
    """The catalog as `flux tools --json` prints it: the generic tools, then the helpers."""
    return [dict(t) for t in TOOLS] + helpers()


def tool(tool_id: str) -> dict[str, Any]:
    every = catalog()
    found = next((t for t in every if t["id"] == tool_id), None)
    if found is None:
        raise KeyError(f"no tool {tool_id!r}; known: {', '.join(t['id'] for t in every)}")
    return found


def fill(tool_id: str, **params: Any) -> str:
    """A tool's command with its params filled (defaults for those not given); the loop's own
    placeholders stay for the loop."""
    t = tool(tool_id)
    if "run" not in t:
        raise ValueError(f"{tool_id} is an evaluator stage: its `stage` and `document` keys, not a command")
    values = {k: v["default"] for k, v in t["params"].items()} | params
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), t["run"])
