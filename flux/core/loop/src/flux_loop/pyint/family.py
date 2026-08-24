"""The family search in the prototype stage (docs/decisions.md D479): the model writes the
algorithm with KNOBS, the harness finds and selects.

A prototype may declare module-level integer constants and a `SPACE` of their choices:

    T = 6                     # table index bits
    M = 13                    # table value bits
    SPACE = {"T": [5, 6, 7], "M": [12, 13, 14]}

`configurations` is the cross product (capped); `bind` rewrites the constants for one member;
`family_harness` runs every member in ONE sandboxed interpreter over all 65,536 inputs and
prints each member's count over the ULP budget and the best member's output. What the model
used to assert -- a table size, a fixed-point width, how many bits of correction -- becomes a
measurement, the way a generator's error analysis does it (D475's lesson, applied to the
model's own algorithm rather than to a hand-written one). Selection: the cheapest member at
0 over by the transpiler's measured widths, else the member with the fewest over; the
prototype stored and transpiled is the BOUND one, a concrete design.
"""

from __future__ import annotations

import ast
import itertools

__all__ = ["MAX_CONFIGS", "bind", "configurations", "family_harness", "parse_family_output",
           "space_of"]

MAX_CONFIGS = 256



def space_of(code: str) -> dict[str, list[int]]:
    """The module-level `SPACE = {...}` literal, or {}. Every choice must be an int. A knob's
    own assignment (`M = 10`) joins its choices, FIRST: it is the model's current preference,
    and an edit to it must have an effect (D483: the model set M = 10 and shifted v to match;
    the SPACE said 12/13/14, so the edit changed nothing and the audit then contradicted it)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    assigned: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, int) and not isinstance(node.value.value, bool):
            assigned[node.targets[0].id] = int(node.value.value)
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "SPACE":
            try:
                raw = ast.literal_eval(node.value)
            except Exception:  # noqa: BLE001
                return {}
            if not isinstance(raw, dict):
                return {}
            out: dict[str, list[int]] = {}
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, (list, tuple)) and v \
                        and all(isinstance(x, int) and not isinstance(x, bool) for x in v):
                    choices = [int(x) for x in v]
                    if k in assigned and assigned[k] not in choices:
                        choices = [assigned[k]] + choices
                    out[k] = list(dict.fromkeys(choices))
            return out
    return {}


def configurations(space: dict[str, list[int]], cap: int = MAX_CONFIGS
                   ) -> tuple[list[dict[str, int]], str]:
    """Every member of the space in a stable order, capped at `cap` with a note saying so
    (the first choices of each knob are kept: list the likeliest first)."""
    keys = sorted(space)
    total = 1
    for k in keys:
        total *= len(space[k])
    members = [dict(zip(keys, vals)) for vals in itertools.product(*(space[k] for k in keys))]
    note = ""
    if len(members) > cap:
        members = members[:cap]
        note = (f"the space has {total} members; only the first {cap} were tried -- list the "
                "likeliest choices first, or shrink it")
    return members, note


def bind(code: str, cfg: dict[str, int]) -> str:
    """`code` with each knob's module-level assignment set to the member's value; the SPACE
    line stays (it is documentation for the next reader). A knob named in the SPACE with no
    assignment of its own (D482: apex wrote the SPACE and used T and M unassigned) gets one,
    inserted before the first statement that is not an import."""
    tree = ast.parse(code)
    seen: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in cfg:
            node.value = ast.Constant(value=int(cfg[node.targets[0].id]))
            seen.add(node.targets[0].id)
    missing = [k for k in cfg if k not in seen]
    if missing:
        at = next((i for i, n in enumerate(tree.body)
                   if not isinstance(n, (ast.Import, ast.ImportFrom))), len(tree.body))
        for k in reversed(sorted(missing)):
            tree.body.insert(at, ast.Assign(targets=[ast.Name(id=k, ctx=ast.Store())],
                                            value=ast.Constant(value=int(cfg[k]))))
    return ast.unparse(ast.fix_missing_locations(tree))


def family_harness(code: str, members: list[dict[str, int]], *, judge: str,
                   prelude_lines: int = 0) -> str:
    """One script: the prototype's text, re-executed in a fresh namespace per member with
    its constants bound, each member's output judged by the problem's JUDGE; prints `CFG i
    over` per member, `BEST i` and the best member's output (`OUT hex`). An error names the
    deepest line in the MODEL's text (past `prelude_lines`), not the block it called.

    `judge` is Python source the problem supplies (D515) that defines `_xs` (the inputs, a
    numpy integer array), `_shape(y)` (a member's raw output as the gate's pattern array)
    and `_over(ys) -> int` (how many inputs the output fails); the language knows nothing
    of what is being computed."""
    return f'''
import math as _math
import numpy as np
_PROTO = {code!r}
_MEMBERS = {members!r}
_PRELUDE = {int(prelude_lines)}
{judge}

import ast as _ast
def _bound(cfg):
    tree = _ast.parse(_PROTO)
    seen = set()
    for node in tree.body:
        if isinstance(node, _ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], _ast.Name) and node.targets[0].id in cfg:
            node.value = _ast.Constant(value=int(cfg[node.targets[0].id])); seen.add(node.targets[0].id)
    at = next((i for i, n in enumerate(tree.body) if not isinstance(n, (_ast.Import, _ast.ImportFrom))), len(tree.body))
    for k in reversed(sorted(k for k in cfg if k not in seen)):
        tree.body.insert(at, _ast.Assign(targets=[_ast.Name(id=k, ctx=_ast.Store())], value=_ast.Constant(value=int(cfg[k]))))
    return compile(_ast.fix_missing_locations(tree), "<member>", "exec")

_best = None
for _i, _cfg in enumerate(_MEMBERS):
    _ns = {{"np": np, "math": _math, "numpy": np}}
    try:
        exec(_bound(_cfg), _ns)
        with np.errstate(all="ignore"):
            _ys = _shape(_ns["design"](_xs.astype(np.int64)))
        assert _ys.shape == _xs.shape, "design must return one value per input"
        _n = _over(_ys)
    except Exception as _exc:
        import traceback as _tb
        _frames = [f for f in _tb.extract_tb(_exc.__traceback__) if f.filename == "<member>"]
        _mine = [f for f in _frames if f.lineno > _PRELUDE]
        _where = f"line {{(_mine or _frames)[-1].lineno}}: " if _frames else ""
        print("CFG", _i, "error", (_where + str(_exc)[:160]).replace(chr(10), " "))
        continue
    print("CFG", _i, "over", _n)
    if _best is None or _n < _best[0]:
        _best = (_n, _i, _ys)
if _best is not None:
    print("BEST", _best[1])
    print("OUT " + _best[2].tobytes().hex())
'''


def parse_family_output(out: str) -> tuple[dict[int, int | str], int | None, str | None]:
    """(member index -> over or the error text, the best index, the best's hex output)."""
    overs: dict[int, int | str] = {}
    best: int | None = None
    hexout: str | None = None
    for ln in out.splitlines():
        parts = ln.split(" ", 3)
        if parts[0] == "CFG" and len(parts) >= 4:
            try:
                idx = int(parts[1])
            except ValueError:
                continue
            overs[idx] = int(parts[3]) if parts[2] == "over" else f"error: {parts[3]}"
        elif parts[0] == "BEST" and len(parts) >= 2:
            try:
                best = int(parts[1])
            except ValueError:
                pass
        elif parts[0] == "OUT" and len(parts) >= 2:
            hexout = parts[1].strip()
    return overs, best, hexout


def describe_search(members: list[dict[str, int]], overs: dict[int, int | str],
                    picked: int | None, note: str = "", cost: dict[int, int] | None = None
                    ) -> str:
    """The search in words for the model and the log: how many members, how many at 0, the
    pick and the runners-up, the knobs that never mattered."""
    n = len(members)
    measured = {i: v for i, v in overs.items() if isinstance(v, int)}
    errors = sum(1 for v in overs.values() if isinstance(v, str))
    zero = sorted(i for i, v in measured.items() if v == 0)
    lines = [f"FAMILY SEARCH: {n} member(s) tried, {len(zero)} at 0 over"
             + (f", {errors} raised an error" if errors else "") + "."]
    if note:
        lines.append(f"  {note}")
    if picked is not None:
        lines.append(f"  picked: {members[picked]} -> {measured.get(picked, '?')} over"
                     + (f", cost proxy {cost[picked]}" if cost and picked in cost else ""))
    ranked = sorted(measured.items(), key=lambda kv: (kv[1], cost.get(kv[0], 0) if cost else 0))
    others = [i for i, _v in ranked if i != picked][:4]
    if others:
        lines.append("  next: " + "; ".join(f"{members[i]} -> {measured[i]} over" for i in others))
    if measured and len(members) > 1:
        for k in sorted(members[0]):
            by_val: dict[int, list[int]] = {}
            for i, v in measured.items():
                by_val.setdefault(members[i][k], []).append(v)
            if len(by_val) > 1 and len({min(vs) for vs in by_val.values()}) == 1:
                lines.append(f"  knob {k} never changed the best outcome ({min(min(vs) for vs in by_val.values())} over at every value)")
    err = next((v for v in overs.values() if isinstance(v, str)), None)
    if err:
        lines.append(f"  first error: {err}")
    return "\n".join(lines)
