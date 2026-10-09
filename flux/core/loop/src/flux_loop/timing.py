"""A placed critical path described in words (D526), for the depth pass, the route and the
`timing` tool.

The path comes from OpenROAD's `report_checks`, read by `parse_critical_path` below. Synthesis (`abc`) renames
most nets, so steps are usually cells; a net that kept an RTL name is named.
"""

from __future__ import annotations

import re
from typing import Any


# The critical path as data (D526): the `full_clock_expanded` report parsed into its steps.
# Format (openroad 26Q2):
#     Fanout      Cap     Slew    Delay     Time   Description
#     ---------------------------------------------------------------------------------
#          2    1.550   25.734   49.502   49.502 v _6597_/QN (DFFHQNx1_ASAP7_75t_R)
#                                       2035.062   data arrival time
#                                       -792.996   slack (VIOLATED)
_PATH_STEP_RE = re.compile(
    r"^\s*(?:(?P<fanout>\d+)\s+)?(?:(?P<cap>[\d.]+)\s+)?(?:(?P<slew>[\d.]+)\s+)?(?P<delay>-?[\d.]+)\s+(?P<time>-?[\d.]+)\s+"
    r"(?P<edge>[v^])\s+(?P<pin>\S+)\s+\((?P<cell>[^)]+)\)\s*$", re.MULTILINE)
_PATH_NET_RE = re.compile(r"^\s+(?P<net>\S+) \(net\)\s*$", re.MULTILINE)   # `-fields {net}`: the line after its pin
_PATH_START_RE = re.compile(r"^Startpoint: (\S+)", re.MULTILINE)
_PATH_END_RE = re.compile(r"^Endpoint: (\S+)", re.MULTILINE)
_PATH_SLACK_RE = re.compile(r"^\s*(-?[\d.]+)\s+slack \((MET|VIOLATED)\)", re.MULTILINE)
_PATH_ARRIVAL_RE = re.compile(r"^\s*(-?[\d.]+)\s+data arrival time", re.MULTILINE)
_PATH_REQUIRED_RE = re.compile(r"^\s*(-?[\d.]+)\s+data required time", re.MULTILINE)


def parse_critical_path(log: str) -> dict[str, Any] | None:
    """The worst path of an OpenSTA `report_checks -format full_clock_expanded` report, from
    the log that holds it: `{startpoint, endpoint, slack_ps, met, arrival_ps, required_ps,
    steps}` with one step per pin `{pin, cell, net, delay_ps, time_ps, edge, fanout}` in
    path order (the clock pin and the endpoint's D pin included), or None when the log has
    no path. Times are the report's units (picoseconds on ASAP7)."""
    i = log.rfind("Startpoint:")
    if i < 0:
        return None
    text = log[i:]
    start, end = _PATH_START_RE.search(text), _PATH_END_RE.search(text)
    steps: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _PATH_STEP_RE.match(line)
        if m:
            steps.append({"pin": m.group("pin"), "cell": m.group("cell"), "net": None,
                          "delay_ps": float(m.group("delay")), "time_ps": float(m.group("time")),
                          "edge": m.group("edge"),
                          "fanout": int(m.group("fanout")) if m.group("fanout") else None})
            continue
        n = _PATH_NET_RE.match(line)
        if n and steps:
            steps[-1]["net"] = n.group("net")
        if "data arrival time" in line:
            break
    slack = _PATH_SLACK_RE.search(text)
    arrival = _PATH_ARRIVAL_RE.search(text)
    required = _PATH_REQUIRED_RE.search(text)
    if not steps and slack is None:
        return None
    return {"startpoint": start.group(1) if start else None, "endpoint": end.group(1) if end else None,
            "slack_ps": float(slack.group(1)) if slack else None,
            "met": (slack.group(2) == "MET") if slack else None,
            "arrival_ps": float(arrival.group(1)) if arrival else None,
            "required_ps": float(required.group(1)) if required else None,
            "steps": steps}

__all__ = ["describe", "parse_critical_path"]

_ANON = re.compile(r"^_\d+_$")


def _signal(net: str | None) -> str | None:
    """An RTL signal name, when synthesis kept one on the net: `u_exp.interp1__12_34[5]` ->
    `interp1`; `_3098_` -> None."""
    if not net:
        return None
    base = net.split(".")[-1].split("[")[0]
    if _ANON.match(base):
        return None
    base = re.sub(r"(__\d+)?(_\d+)?(_d\d+)?$", "", base) or base
    return base


def describe(path: dict[str, Any] | None, *, top: int = 5) -> str:
    """The placed critical path in one line: arrival against required, the slack, how many
    cells, the `top` costliest steps (delay, cell, fanout, the signal when it has a name),
    and whether the steps could be named at all."""
    if not path or not isinstance(path, dict):
        return ""
    steps = [s for s in (path.get("steps") or []) if isinstance(s, dict)]
    arrival, required, slack = path.get("arrival_ps"), path.get("required_ps"), path.get("slack_ps")
    head = "the placed critical path"
    if arrival is not None and required is not None:
        head += f": {arrival:.0f} ps of logic against {required:.0f} ps required"
    if slack is not None:
        head += f" (slack {slack:+.0f} ps, {'met' if path.get('met') else 'violated'})"
    cells = [s for s in steps if s.get("delay_ps") is not None and not str(s.get("pin", "")).endswith("/CLK")]
    if cells:
        head += f"; {len(cells)} cells"
    named = [s for s in cells if _signal(s.get("net"))]
    costly = sorted(cells, key=lambda s: -float(s.get("delay_ps") or 0.0))[:max(1, top)]
    if costly:
        parts = []
        for s in costly:
            cell = str(s.get("cell") or "?").split("_ASAP7")[0]
            fan = f" driving {s['fanout']}" if s.get("fanout") else ""
            sig = _signal(s.get("net"))
            parts.append(f"{float(s.get('delay_ps') or 0):.0f} ps {cell}{fan}" + (f" on {sig}" if sig else ""))
        head += "; the costliest steps: " + ", ".join(parts)
    heavy = [s for s in cells if (s.get("fanout") or 0) >= 8]
    if heavy:
        head += (f"; {len(heavy)} step(s) drive 8 or more loads -- a value used in many places at once "
                 "costs its fanout in delay")
    if cells and not named:
        head += "; synthesis kept no signal names on this path (abc renames the nets), so the steps are cells, not the prototype's signals"
    elif named:
        head += "; the signals named: " + ", ".join(dict.fromkeys(_signal(s.get("net")) for s in named))
    return head
