"""THE TIMING REPORT AS DATA, in words (docs/decisions.md D526, review step 8): what a placed
critical path says, for the depth pass's ask, the route's why and the `timing` tool.

The path itself is `flux_evaluator_openroad.parse_critical_path`'s: the start, the end, the
slack, and one step per cell with its delay, its fanout and the net it drives. Synthesis
(`abc`) renames every net it touches, so on a placed NLU part the steps are cells, not the
prototype's signals -- the words say so instead of guessing; a net that kept an RTL name
is named.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["describe"]

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
