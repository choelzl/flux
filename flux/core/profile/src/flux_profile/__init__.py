"""Where the wall-clock actually went (docs/decisions.md D295).

A run of this repo's demo spends its time in four very different places — external tools
(Yosys, OpenROAD, Verilator), local model inference, the search's own bookkeeping, and waiting —
and until this existed none of them were separable. That mattered practically: a generation round
that produced nothing looked identical to one that was merely slow, and an hour was spent
diagnosing "the demo is stuck" when the answer was one 48-minute model call.

TWO CLOCKS, and reporting only one of them would lie. `phase()` sums the time spent INSIDE each
category across every thread, so with concurrent escalation the sum legitimately exceeds the
elapsed wall-clock — eight placements running together contribute eight seconds of tool time per
second of real time. Both numbers are reported, and their ratio is the concurrency actually
achieved rather than the concurrency requested.

Deliberately tiny and dependency-free: a dict, a lock, a context manager. It is a measuring
instrument, not a tracing framework, and it must never be able to fail a run — every entry point
swallows nothing but also allocates nothing that could raise.
"""

from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from typing import Any

_LOCK = threading.Lock()
_PHASES: dict[str, list] = {}          # name -> [calls, seconds]
_TREE: dict[tuple[str, ...], list] = {}   # path -> [calls, seconds]: WHERE it ran (D418c)
_OPEN: dict[int, list[tuple[str, float, Any]]] = {}   # thread id -> open (name, t0, token) stack
_STARTED = time.perf_counter()
# Optional observer (docs/decisions.md D391): the TUI subscribes so every phased block
# anywhere in a loop becomes a live task row with a real duration -- without any loop
# changing a line. The instrument must never fail a run, so every callback is wrapped;
# a listener that raises is a listener that gets ignored for that event.
_LISTENER = None


def set_listener(listener) -> None:
    """`listener.phase_start(name, why, params) -> token`,
    `listener.phase_end(token, name, seconds, failed, output)` and
    `listener.phase_update(token, name, output)` (D493: what a running phase has so far -- a
    model's thinking as it streams); any may be missing. `output` is the dict the block
    filled through `with phase(...) as out:` (D470)."""
    global _LISTENER
    _LISTENER = listener


def clear_listener() -> None:
    global _LISTENER
    _LISTENER = None


def progress(**fields) -> None:
    """What the innermost open phase has produced SO FAR (D493): a streaming model's
    thinking and answer as they arrive, a build's last log line. Reaches the observer's
    row live, under the same keys the block's final `out` will carry; costs nothing without
    a listener and never fails the run."""
    lis = _LISTENER
    if lis is None or not fields:
        return
    with _LOCK:
        stack = _stack()
        top = stack[-1] if stack else None
    if top is None:
        return
    try:
        lis.phase_update(top[2], top[0], dict(fields))
    except Exception:  # noqa: BLE001 -- the instrument never fails the run
        pass


def mark(name: str, why: str = "") -> None:
    """An instantaneous event for the observer (a stage headline, a decision); costs
    nothing and records nothing when no listener is attached."""
    lis = _LISTENER
    if lis is None:
        return
    try:
        lis.mark(name, why)
    except Exception:  # noqa: BLE001 -- the instrument never fails the run
        pass


def publish(key: str, payload: dict) -> None:
    """A loop's live STANDINGS for the observer (D418l): the latest value per key --
    what is proven, the best per part, how many judged -- the way `phase()` carries
    time. Costs nothing without a listener; the instrument never fails the run."""
    lis = _LISTENER
    if lis is None:
        return
    try:
        lis.publish(key, dict(payload))
    except Exception:  # noqa: BLE001
        pass


def reset() -> None:
    """Start a fresh measurement window."""
    global _STARTED
    with _LOCK:
        _PHASES.clear()
        _TREE.clear()
        _OPEN.clear()
        _STARTED = time.perf_counter()


def _stack() -> list[tuple[str, float, Any]]:
    return _OPEN.setdefault(threading.get_ident(), [])


def record(name: str, seconds: float, path: tuple[str, ...] | None = None) -> None:
    """Add a finished span. `path` is the chain of enclosing phase names ending in
    `name`; without one the span is recorded at top level."""
    with _LOCK:
        row = _PHASES.get(name)
        if row is None:
            _PHASES[name] = [1, seconds]
        else:
            row[0] += 1
            row[1] += seconds
        key = tuple(path) if path else (name,)
        node = _TREE.get(key)
        if node is None:
            _TREE[key] = [1, seconds]
        else:
            node[0] += 1
            node[1] += seconds


def tree(now: float | None = None) -> dict[tuple[str, ...], tuple[int, float, bool]]:
    """path -> (calls, seconds, running). Finished spans by WHERE they ran, plus every
    phase still OPEN on any thread with its elapsed so far -- so a model call in its
    twentieth minute is attributed while it runs, not after (Cedric: "we should be
    able to attribute the time already since we called the functions")."""
    now = time.perf_counter() if now is None else now
    with _LOCK:
        out: dict[tuple[str, ...], tuple[int, float, bool]] = {
            k: (v[0], v[1], False) for k, v in _TREE.items()}
        for stack in _OPEN.values():
            for depth, (name, t0, _tok) in enumerate(stack):
                key = tuple(n for n, *_ in stack[:depth + 1])
                calls, secs, _r = out.get(key, (0, 0.0, False))
                out[key] = (calls + 1, secs + (now - t0), True)
        return out


def open_spans(now: float | None = None) -> dict[tuple[str, ...], float]:
    """path -> seconds the phase currently OPEN there has run so far (D487: the timing tab
    shows a running phase's own elapsed beside its per-call average)."""
    now = time.perf_counter() if now is None else now
    with _LOCK:
        out: dict[tuple[str, ...], float] = {}
        for stack in _OPEN.values():
            for depth, (name, t0, _tok) in enumerate(stack):
                key = tuple(n for n, *_ in stack[:depth + 1])
                out[key] = max(out.get(key, 0.0), now - t0)
        return out


@contextmanager
def phase(name: str, why: str = "", **params):
    """Time a block into `name`. Records even when the block raises — a tool that failed still
    spent the time, and hiding that would make a slow failure look free.

    `why` and `params` are for the OBSERVER only (the TUI's task rows: what this call
    is for, which trace/model/config it ran) — aggregation stays keyed by `name` alone,
    so adding them never splits a timing bucket. The block receives a dict for its
    OUTPUT (`with phase(...) as out: out["reply"] = text`): what the call produced --
    a model's reply, a verdict, a build error -- reaches the observer at exit (D470),
    beside the parameters it was asked with."""
    lis = _LISTENER
    token = None
    if lis is not None:
        try:
            token = lis.phase_start(name, why, params)
        except Exception:  # noqa: BLE001
            lis = None
    t0 = time.perf_counter()
    with _LOCK:
        stack = _stack()
        stack.append((name, t0, token))
        path = tuple(n for n, _t, _k in stack)
    failed = True
    output: dict = {}
    try:
        yield output
        failed = False
    finally:
        secs = time.perf_counter() - t0
        with _LOCK:
            stack = _stack()
            if stack and stack[-1][0] == name:
                stack.pop()
        record(name, secs, path)
        if lis is not None:
            try:
                lis.phase_end(token, name, secs, failed, output)
            except Exception:  # noqa: BLE001
                pass


def snapshot() -> dict[str, tuple[int, float]]:
    with _LOCK:
        return {k: (v[0], v[1]) for k, v in _PHASES.items()}


def elapsed_s() -> float:
    return time.perf_counter() - _STARTED


def human_s(seconds: float) -> str:
    """Seconds in the unit a reader can act on (D418): 45s, 3m12, 2h05, 1d3h -- so
    nobody divides by 60 in their head to compare two rows of a table."""
    seconds = max(0.0, float(seconds))
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, sec = divmod(int(seconds), 60)
    if m < 60:
        return f"{m}m{sec:02d}"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h{m:02d}"
    d, h = divmod(h, 24)
    return f"{d}d{h}h"


#: The slides' five roles (D418k), by the first word of a phase name. The model is
#: marked separately, as the legend's purple border does: a phase that IS a model
#: call gets "model" whatever role called it.
_ROLE_WORDS = {
    # phase names, and the first word of the loop's own log lines (D441): one table
    "mentor": ("prepare", "reload", "author", "mentor", "knowledge", "records",
               "extract", "feedback", "setup", "field", "resuming", "resumed", "campaign",
               "brief"),
    "orchestrator": ("main", "step", "plan", "evaluate", "evaluation", "decide", "frontier",
                     "gate", "dse", "propose", "llm-propose", "orchestrate", "decision",
                     "decompose"),
    "generator": ("sub", "generation", "generate", "generate-loop", "llm-gen", "patch",
                  "repair", "rewrite", "design", "template", "template-fill", "oracle",
                  "invent", "prototype", "patched", "compute"),
    "evaluator": ("build", "test", "judge", "llm-judge", "prove", "measure", "screen",
                  "confirm", "sanity", "analytical", "simulation", "physical", "calibrate",
                  "critique", "tool", "verify", "check", "admitted"),
    "io": ("input", "output", "report", "io", "problem"),
}


_NODE_ROLES: dict[str, str] = {}

#: The loop's standings states (D443): the words `flux_loop` publishes per part and the
#: TUI renders, with the color each takes. One table, so neither side spells them.
STANDINGS: dict[str, str | None] = {"proven": "ok", "best so far": "bad", "not yet tried": "dim",
                                     "trying": "warn"}
PROVEN, BEST_SO_FAR, NOT_YET_TRIED, TRYING = "proven", "best so far", "not yet tried", "trying"


def register_roles(roles: dict[str, str]) -> None:
    """Node name -> role, from whoever owns the graph (flux_loop.graph, D427). A
    registered node wins over the word list above, which stays for the older loops'
    phase names."""
    _NODE_ROLES.update({k.strip().lower(): v for k, v in roles.items()})


def role_of(name: str) -> str | None:
    """mentor / orchestrator / generator / evaluator / io / model, from a phase name."""
    head = name.strip().lower()
    if head.startswith("llm:") or head.startswith("llm "):
        return "model"
    word = re.split(r"[\s:]", head, maxsplit=1)[0]
    if word in _NODE_ROLES:
        return _NODE_ROLES[word]
    for role, words in _ROLE_WORDS.items():
        if word in words:
            return role
    return None


def report_rows(*, derived: dict[str, float] | None = None,
                total_s: float | None = None,
                folded: set[tuple[str, ...]] | None = None) -> list[dict]:
    """The timing table as structured rows: {"text", "path", "depth", "has_children",
    "folded"} -- `path` is None for the header and the derived/unattributed lines.
    When `folded` is given (the TUI drives folding, D418d) rows with children carry a
    marker (▾ open, ▸ folded) and the descendants of folded paths are omitted; with
    `folded=None` the rendering is plain, so text consumers see no markers.

    `total_s` overrides the denominator: the TUI passes its ACTIVE run clock, because
    this module's own window keeps running while an operator reads results between
    reruns, and percentages against idle-polluted wall time are noise."""
    snap = snapshot()
    total = total_s if total_s and total_s > 0 else elapsed_s()
    if not snap and not derived and not any(_OPEN.values()):
        return [{"text": "(no timing recorded)", "path": None, "depth": 0,
                 "has_children": False, "folded": False}]
    rows: list[dict] = [{"text": f"{'phase':<34}{'calls':>7}{'total':>9}{'per call':>10}"
                                 f"{'% of run':>10}{'state':>9}",
                         "path": None, "depth": 0, "has_children": False, "folded": False}]
    t = tree()
    label = "ELAPSED (active run)" if total_s else "ELAPSED (wall clock)"
    rows.append({"text": f"{label:<34}{'':>7}{human_s(total):>9}{'':>10}{100.0:>9.1f}%"
                         f"{'':>9}",
                 "path": (), "depth": 0, "has_children": True, "folded": False})

    def children(prefix: tuple[str, ...]) -> list[tuple[str, ...]]:
        kids = [k for k in t if len(k) == len(prefix) + 1 and k[:len(prefix)] == prefix]
        return sorted(kids, key=lambda k: -t[k][1])

    open_now = open_spans()

    def emit(key: tuple[str, ...], depth: int) -> None:
        calls, secs, running = t[key]
        kids = children(key)
        is_folded = folded is not None and key in folded
        # the per-call average counts a phase still open too (Cedric: "show the per call
        # time for running tasks"); the state column carries that call's own elapsed
        per = human_s(secs / calls) if calls else ""
        state = f"▶ {human_s(open_now[key])}" if running and key in open_now else ("running" if running else "")
        if folded is None:
            mark = ""
        else:
            mark = ("▸ " if is_folded else "▾ ") if kids else "  "
        # The model is a MARKER on a role, not a role (D418k, Cedric): a phase that
        # is a model call takes the color of the phase that made it and shows
        # "(model)" highlighted; a phase with a model call directly inside it gets
        # a highlighted "·model" suffix, as the slides' purple border does.
        own = role_of(key[-1])
        calls_model = any(role_of(k[-1]) == "model" for k in kids)
        label = key[-1]
        if own == "model":
            parent_role = next((role_of(n) for n in reversed(key[:-1])
                                if role_of(n) not in (None, "model")), None)
            role = f"{parent_role or ''}+model"
        elif calls_model:
            role = f"{own or ''}+model"
            # the marker must survive the label width: shorten the NAME, never the mark
            room = 34 - len("  " * depth + mark) - len(" ·model")
            label = key[-1][:max(4, room)] + " ·model"
        else:
            role = own
        rows.append({"text": f"{('  ' * depth + mark + label)[:34]:<34}{calls:>7}"
                             f"{human_s(secs):>9}{per:>10}{secs / total * 100:>9.1f}%"
                             f"{state:>10}",
                     "path": key, "depth": depth, "has_children": bool(kids),
                     "folded": is_folded, "role": role})
        if not is_folded:
            for kid in kids:
                emit(kid, depth + 1)

    tops = children(())
    root_folded = folded is not None and () in folded
    if root_folded:
        rows[-1]["folded"] = True
    else:
        for key in tops:
            emit(key, 1)
    for name, secs in (derived or {}).items():
        rows.append({"text": f"{('  ' + name)[:34]:<34}{'':>7}{human_s(secs):>9}{'':>10}"
                             f"{secs / total * 100:>9.1f}%{'':>9}",
                     "path": None, "depth": 1, "has_children": False, "folded": False})
    tool = sum(v[1] for k, v in t.items() if k[-1].startswith("tool:"))
    unattributed = total - sum(t[k][1] for k in tops)   # children sit inside parents
    if unattributed > max(1.0, 0.02 * total) and not root_folded:
        # Transparency over tidiness: a gap this size is real work no phase names yet.
        rows.append({"text": f"{'  unattributed (no phase yet)':<34}{'':>7}"
                             f"{human_s(unattributed):>9}{'':>10}"
                             f"{unattributed / total * 100:>9.1f}%{'':>9}",
                     "path": None, "depth": 1, "has_children": False, "folded": False})
    if tool > total:
        rows.append({"text": f"    tool time sums to {tool:.0f}s over {total:.0f}s elapsed "
                             f"= {tool / total:.1f}x concurrency actually achieved",
                     "path": None, "depth": 1, "has_children": False, "folded": False})
    return rows


def report_lines(*, derived: dict[str, float] | None = None,
                 total_s: float | None = None) -> list[str]:
    """The timing table as plain text (no fold markers): `report_rows` flattened."""
    return [r["text"] for r in report_rows(derived=derived, total_s=total_s)]


def seconds(name: str) -> float:
    """Measured seconds recorded under `name`, or 0."""
    return snapshot().get(name, (0, 0.0))[1]


def outside(total_phase: str, *inner_prefixes: str) -> float:
    """Seconds a phase spent NOT inside the phases named by these prefixes.

    A difference of two measurements, which is why callers label the result as derived rather
    than measured: "proposing, outside the model" is the proposing total minus the model time
    within it, and it is only as trustworthy as the instrumentation on both sides.
    """
    total = seconds(total_phase)
    if not total:
        return 0.0
    inner = sum(secs for name, (_, secs) in snapshot().items()
                if any(name.startswith(p) for p in inner_prefixes))
    return max(0.0, total - inner)
