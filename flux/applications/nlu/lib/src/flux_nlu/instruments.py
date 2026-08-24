"""INSTRUMENTS for the model's turn (docs/decisions.md D530, review step 11): what a designer
reaches for beside the check -- each a tool that answers on a prototype in about a second.

    error_map      the ULP error over the whole domain, by sign and binade -- max, mean, the
                   share over budget -- for a PASSING design too (the check only reports
                   failures)
    compare        two prototypes side by side: how many inputs differ, by how much, where,
                   and each one's own error against the reference
    quantisation   every table the prototype builds: its sampling error between entries
                   (zero-order for rom, first-order with a slope table), in table LSB and in
                   output ULP -- whether the table CAN be within budget before any logic runs

Before D530 the model's only instrument was the check, and a plot of the error over the
domain, the table's own error, the interpolation error were packed into prose in the failure
report (review §1.3.4). The placed critical path is the loop's `timing` tool (D526).
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from flux_loop.compute import run_compute
from flux_loop.prototype import verified_operators
from flux_loop.pyint.vectorize import vectorize

from .fp16 import all_inputs, reference, ulp_distance

__all__ = ["compare", "error_map", "family", "instrument_tools", "outputs_of", "quantisation"]

_HUGE = 1 << 20

#: Put between the toolkit and the prototype (D530): `rom`/`slope_rom` record what they build
#: and measure their own sampling error on a fine grid, printed as one `QUANT` line.
_QUANT_WRAPPER = '''
_QUANT = []
_rom_plain = rom
_slope_plain = slope_rom
def _quant_f(func):
    try:
        return _func(func)
    except Exception:
        return func if callable(func) else globals()[func]
def _quant_note(kind, func, lo, hi, entries, frac_bits, sample):
    f = _quant_f(func)
    entries = int(entries); step = (hi - lo) / entries
    off = 0.0 if sample == "left" else 0.5 if sample == "mid" else 1.0
    fine = 32
    t = lo + (np.arange(entries * fine) + 0.5) * step / fine
    with np.errstate(all="ignore"):
        exact = np.asarray(f(t), dtype=np.float64)
        anchor = lo + (np.arange(entries) + off) * step
        held = np.repeat(np.asarray(f(anchor), dtype=np.float64), fine)
        left = lo + np.arange(entries) * step
        fl = np.asarray(f(left), dtype=np.float64); fr = np.asarray(f(left + step), dtype=np.float64)
        frac = ((t - lo) / step) % 1.0
        lin = np.repeat(fl, fine) + np.repeat(fr - fl, fine) * frac
    ok = np.isfinite(exact) & np.isfinite(held) & np.isfinite(lin)
    lsb = 2.0 ** -int(frac_bits)
    def ulp_of(err):
        with np.errstate(all="ignore"):
            rel = np.abs(err[ok]) / np.maximum(np.abs(exact[ok]), 2.0 ** -24)
        return float(rel.max() * 1024) if rel.size else 0.0
    e0 = np.abs(exact - held); e1 = np.abs(exact - lin)
    _QUANT.append({"kind": kind, "func": getattr(f, "__name__", str(func)), "lo": float(lo), "hi": float(hi),
                   "entries": entries, "frac_bits": int(frac_bits), "sample": sample,
                   "hold_max_lsb": float(e0[ok].max() / lsb) if ok.any() else None, "hold_max_ulp": ulp_of(e0),
                   "linear_max_lsb": float(e1[ok].max() / lsb) if ok.any() else None, "linear_max_ulp": ulp_of(e1),
                   "value_max": float(np.abs(exact[ok]).max()) if ok.any() else None})
def rom(func, lo, hi, entries, frac_bits, sample="left"):
    _quant_note("rom", func, lo, hi, entries, frac_bits, sample)
    return _rom_plain(func, lo, hi, entries, frac_bits, sample)
def slope_rom(func, lo, hi, entries, frac_bits, sample="left"):
    _quant_note("slope_rom", func, lo, hi, entries, frac_bits, sample)
    return _slope_plain(func, lo, hi, entries, frac_bits, sample)
'''

_QUANT_TAIL = '\nimport json as _json\nprint("QUANT " + _json.dumps(_QUANT))\n'


def _compose(world: Any, code: str, part: str, state: Any, between: str = "") -> str:
    from .blocks import with_prelude

    vec = vectorize(code)[0]
    return with_prelude((between + "\n" if between else "") + vec, verified_operators(state, part))


def outputs_of(world: Any, code: str, part: str, state: Any, *, between: str = "", tail: str = "") -> tuple[np.ndarray | None, str, str]:
    """The prototype run over every FP16 input in the sandbox: (its outputs as uint16, the
    sandbox's text, an error) -- the outputs None when it did not run."""
    from .blocks import audit_harness
    from .world import PROTO_HARNESS

    try:
        full = _compose(world, code, part, state, between)
    except Exception as exc:  # noqa: BLE001
        return None, "", f"the prototype cannot be made array form: {exc!s:.200}"
    timeout = max(30.0, float(getattr(getattr(state, "request", None), "compute_timeout_s", 10.0)) * 6)
    (_name, out), = run_compute([{"name": "instrument", "code": full + audit_harness() + PROTO_HARNESS + tail}],
                                timeout_s=timeout, max_chars=600_000, trusted=True)
    line = next((ln for ln in out.splitlines() if ln.startswith("OUT ")), None)
    if line is None:
        tail_text = out[-800:]
        return None, out, "the prototype did not produce output: " + tail_text
    try:
        ys = np.frombuffer(bytes.fromhex(line[4:].strip()), dtype="<u2").astype(np.uint16)
    except ValueError as exc:
        return None, out, f"output unreadable: {exc}"
    return ys, out, ""


def _binade_table(xs: np.ndarray, dist: np.ndarray, budget: int) -> list[str]:
    """max / mean ULP and the share over budget per sign and exponent field, the rows that
    carry any error only."""
    ex = (xs.astype(np.int64) >> 10) & 0x1F
    sign = (xs.astype(np.int64) >> 15) & 1
    rows = []
    for s in (0, 1):
        for e in range(32):
            m = (sign == s) & (ex == e)
            if not m.any():
                continue
            d = dist[m]
            real = d[d < _HUGE]
            over = int((d > budget).sum())
            mx = "class" if bool((d >= _HUGE).any()) else int(d.max())
            if (isinstance(mx, int) and mx == 0) or (isinstance(mx, str)):
                if mx == 0:
                    continue
            label = ("+" if s == 0 else "-") + ("subnormal" if e == 0 else "Inf/NaN" if e == 31 else f"2^{e - 15}")
            rows.append(f"  {label:<12} n={int(m.sum()):5d}  max {mx!s:>5}  mean {float(real.mean()) if real.size else 0:.3f}  over {over}")
    return rows


def error_map(world: Any, code: str, part: str, state: Any) -> str:
    ys, _out, err = outputs_of(world, code, part, state)
    if ys is None:
        return err
    xs = all_inputs()
    if ys.shape != xs.shape:
        return f"the prototype returned {ys.size} values for {xs.size} inputs"
    want = reference(part, xs)
    dist = ulp_distance(ys, want)
    budget = int(world.ulp_budget)
    real = dist[dist < _HUGE]
    head = (f"{part}: {int((dist > budget).sum())} of {xs.size} beyond {budget} ULP; max "
            f"{'class-mismatch' if bool((dist >= _HUGE).any()) else int(dist.max())}, mean {float(real.mean()) if real.size else 0:.4f}, "
            f"{float((dist == 0).mean()):.1%} exact")
    rows = _binade_table(xs, dist, budget)
    if not rows:
        return head + "\n  exact on every binade"
    return head + "\n  by sign and binade (rows with any error; n inputs, max and mean ULP, over budget):\n" + "\n".join(rows)


def compare(world: Any, code_a: str, code_b: str, part: str, state: Any) -> str:
    ya, _o, ea = outputs_of(world, code_a, part, state)
    if ya is None:
        return "A: " + ea
    yb, _o, eb = outputs_of(world, code_b, part, state)
    if yb is None:
        return "B: " + eb
    xs = all_inputs()
    if ya.shape != xs.shape or yb.shape != xs.shape:
        return "a prototype returned the wrong number of values"
    want = reference(part, xs)
    da, db = ulp_distance(ya, want), ulp_distance(yb, want)
    between = ulp_distance(ya, yb)
    differ = between > 0
    n = int(differ.sum())
    budget = int(world.ulp_budget)
    lines = [f"A and B differ on {n} of {xs.size} inputs" + (f", by up to {'class' if bool((between >= _HUGE).any()) else int(between.max())} ULP" if n else ""),
             f"  A: {int((da > budget).sum())} over budget, max {'class' if bool((da >= _HUGE).any()) else int(da.max())} ULP;  "
             f"B: {int((db > budget).sum())} over budget, max {'class' if bool((db >= _HUGE).any()) else int(db.max())} ULP"]
    if n:
        a_better = int(((da < db) & differ).sum()); b_better = int(((db < da) & differ).sum())
        lines.append(f"  where they differ, A is closer to the reference on {a_better}, B on {b_better}, equal on {n - a_better - b_better}")
        rows = _binade_table(xs, np.where(differ, between, 0), 0)
        if rows:
            lines.append("  where (by sign and binade, max and mean ULP between A and B):")
            lines += rows[:12]
        idx = np.flatnonzero(differ)[:6]
        lines.append("  first differing inputs: " + "; ".join(
            f"x=0x{int(xs[i]):04x} A=0x{int(ya[i]):04x} B=0x{int(yb[i]):04x} want=0x{int(want[i]):04x}" for i in idx))
    return "\n".join(lines)


def quantisation(world: Any, code: str, part: str, state: Any) -> str:
    ys, out, err = outputs_of(world, code, part, state, between=_QUANT_WRAPPER, tail=_QUANT_TAIL)
    line = next((ln for ln in out.splitlines() if ln.startswith("QUANT ")), None)
    if line is None:
        return err or "the prototype ran but built no table through rom()/slope_rom()"
    try:
        tables = json.loads(line[6:])
    except ValueError:
        return "the table report was unreadable"
    if not tables:
        return "the prototype builds no table through rom()/slope_rom()"
    lines = [f"{len(tables)} table(s) built; the sampling error between entries, on a fine grid "
             "(output ULP taken as 2^-10 of the value):"]
    for i, t in enumerate(tables, 1):
        lines.append(f"  {i}. {t['kind']}({t['func']}, {t['lo']:g}, {t['hi']:g}, {t['entries']}, frac_bits={t['frac_bits']}, sample={t['sample']}):"
                     f" zero-order (lookup) error up to {t['hold_max_lsb'] or 0:.1f} LSB = {t['hold_max_ulp']:.1f} output ULP;"
                     f" first-order (interp1 with a slope table) up to {t['linear_max_lsb'] or 0:.2f} LSB = {t['linear_max_ulp']:.2f} output ULP")
        if t["hold_max_ulp"] > 1.0 and t["linear_max_ulp"] <= 1.0:
            lines.append("     -> a plain lookup cannot be within 1 ULP with this many entries; a first-order correction can")
        elif t["linear_max_ulp"] > 1.0:
            lines.append(f"     -> even first-order interpolation misses 1 ULP: more entries (x{2 ** int(np.ceil(np.log2(max(1.0, t['linear_max_ulp'] ** 0.5))))} halves the error fourfold each doubling) or a second-order term")
    return "\n".join(lines)


def family(world: Any, code: str, part: str, state: Any, limit: int = 24) -> str:
    """The FAMILY SEARCH as an instrument (D535, review §1.3.9): every member of the SPACE the
    prototype declares, run in one sandboxed pass and judged by the world's gate, reported
    with its over-count and its cost -- the model reads which knobs matter and which member
    the check would bind, and edits its own text; nothing here rewrites the reply."""
    from flux_loop.pyint import bind, configurations, describe_search, family_harness, parse_family_output, space_of

    space = space_of(code)
    if not space:
        return "the prototype declares no SPACE (module-level `SPACE = {\"T\": [5, 6, 7], ...}` over knobs it uses)"
    cap = world.problem.prototype()
    judge_src = cap.family_judge(part) if cap is not None and cap.family_judge else None
    if not judge_src:
        return "this world has no family judge"
    try:
        from .blocks import prelude_lines

        n_pre = prelude_lines(verified_operators(state, part))
        full = _compose(world, code, part, state)
    except Exception as exc:  # noqa: BLE001
        return f"the prototype cannot be made array form: {exc!s:.200}"
    members, cap_note = configurations(space)
    timeout = max(120.0, float(getattr(getattr(state, "request", None), "compute_timeout_s", 10.0)) * 6 * max(1, len(members) // 8))
    (_name, out), = run_compute([{"name": "family", "code": family_harness(full, members, judge=judge_src, prelude_lines=n_pre)}],
                                timeout_s=timeout, max_chars=600_000, trusted=True)
    overs, _best, _hexout = parse_family_output(out)
    measured = {i: v for i, v in overs.items() if isinstance(v, int)}
    if not measured:
        return "the family produced no result: " + out[-600:]
    zero = sorted(i for i, v in measured.items() if v == 0)
    cost: dict[int, int] = {}
    if zero and cap is not None and cap.cost:
        for i in zero[:limit]:
            try:
                cost[i] = int(cap.cost(_compose(world, bind(code, members[i]), part, state)))
            except Exception:  # noqa: BLE001
                cost[i] = 1 << 30
    picked = min(zero[:limit], key=lambda i: cost.get(i, 1 << 30)) if zero else min(measured, key=lambda i: measured[i])
    head = describe_search(members, overs, picked, cap_note, cost)
    rows = sorted(measured.items(), key=lambda kv: (kv[1], cost.get(kv[0], 1 << 30)))[:limit]
    table = "\n".join(f"  {members[i]}: {v} over" + (f", cost {cost[i]}" if i in cost else "") + ("   <- the check would bind this" if i == picked else "")
                      for i, v in rows)
    return head + "\n\nevery member, best first (over-count, cost proxy where it passes):\n" + table


def instrument_tools(world: Any, part: str, state: Any) -> list:
    """The three instruments as tools of a prototype turn on `part` (D530)."""
    from flux_llm import Tool

    def run_error_map(args: dict[str, Any]) -> str:
        code = str(args.get("prototype") or "")
        return error_map(world, code, part, state) if code.strip() else "error: `prototype` is empty"

    def run_compare(args: dict[str, Any]) -> str:
        a, b = str(args.get("a") or ""), str(args.get("b") or "")
        return compare(world, a, b, part, state) if a.strip() and b.strip() else "error: `a` and `b` are two complete prototype texts"

    def run_quant(args: dict[str, Any]) -> str:
        code = str(args.get("prototype") or "")
        return quantisation(world, code, part, state) if code.strip() else "error: `prototype` is empty"

    def run_family(args: dict[str, Any]) -> str:
        code = str(args.get("prototype") or "")
        return family(world, code, part, state) if code.strip() else "error: `prototype` is empty"

    text = {"type": "string", "description": "a complete prototype text"}
    return [
        Tool("error_map",
             f"The ULP error of a prototype of {part} over the WHOLE domain, by sign and binade: max, mean and the "
             "share over budget per row -- for a passing design too. Read it to see where the error lives before editing.",
             {"type": "object", "properties": {"prototype": text}, "required": ["prototype"]}, run_error_map),
        Tool("compare",
             "Two prototypes side by side on every input: how many outputs differ, by how much, where, and which is "
             "closer to the reference -- for an edit you are not sure of, or two methods.",
             {"type": "object", "properties": {"a": text, "b": text}, "required": ["a", "b"]}, run_compare),
        Tool("quantisation",
             "Every table the prototype builds with rom()/slope_rom(): its sampling error between entries, zero-order "
             "and first-order, in table LSB and in output ULP -- whether the table CAN be within budget before the "
             "logic around it is blamed.",
             {"type": "object", "properties": {"prototype": text}, "required": ["prototype"]}, run_quant),
        Tool("family",
             "Every member of the SPACE the prototype declares, judged in one pass: the over-count and cost of each, "
             "which knobs never changed the outcome, and the member the check would bind. Read it, then set the "
             "knobs in your own text -- the check binds the cheapest passing member, but you choose what to keep.",
             {"type": "object", "properties": {"prototype": text}, "required": ["prototype"]}, run_family),
    ]
