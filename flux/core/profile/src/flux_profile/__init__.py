"""Where the wall-clock went (D295): external tools, model inference, bookkeeping, waiting.

Two clocks: `phase()` sums time inside each category across all threads, so concurrent work
makes the sum exceed elapsed wall-clock. Both are reported; their ratio is the concurrency
achieved.

Tiny and dependency-free (a dict, a lock, a context manager); it must never fail a run.
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
# Optional observer (D391): the TUI subscribes so every phased block becomes a live task row.
# Every callback is wrapped; a listener that raises is ignored for that event.
_LISTENER = None
_PRIMARY = None                        # the TUI's (set_listener)
_EXTRA: list = []                      # others beside it: the run's journal (D683)
_TAGS = threading.local()              # D739: what this thread works on (a part), on every phase it starts
#: (D1233) the facts a phase says about its work -- given as a phase's params (`box="check"`) they are tags: the
#: phase's own and every phase started inside it (on this thread or one it hands work to, `carried`)
TAG_KEYS = ("box", "pass", "part", "design", "step", "try", "worker")
#: (D1233) what a phase's end says of how its work came out, read from its output
END_KEYS = ("outcome", "not reached", "failed at")
_OUTS = threading.local()              # the open phases' outputs on this thread (`said`)


class _Tee:
    """Several listeners as one: each gets every event, with its own token."""

    def __init__(self, listeners: list) -> None:
        self.listeners = listeners

    def phase_start(self, name, why, params):
        return [_call(lis, "phase_start", name, why, params) for lis in self.listeners]

    def phase_begun(self, name, why, params, started):
        return [_begun(lis, name, why, params, started) for lis in self.listeners]

    def phase_update(self, token, name, output):
        for lis, tok in zip(self.listeners, token or [None] * len(self.listeners)):
            _call(lis, "phase_update", tok, name, output)

    def phase_end(self, token, name, seconds, failed, output):
        for lis, tok in zip(self.listeners, token or [None] * len(self.listeners)):
            _call(lis, "phase_end", tok, name, seconds, failed, output)

    def phase_note(self, name, why, params, output):
        for lis in self.listeners:
            _call(lis, "phase_note", name, why, params, output)

    def phase_after(self, name, why, params, seconds, failed, output):
        for lis in self.listeners:
            _call(lis, "phase_after", name, why, params, seconds, failed, output)

    def phase_params(self, token, params):
        for lis, tok in zip(self.listeners, token or [None] * len(self.listeners)):
            _call(lis, "phase_params", tok, params)

    def adopt(self, token):
        for lis, tok in zip(self.listeners, token or [None] * len(self.listeners)):
            _call(lis, "adopt", tok)

    def mark(self, name, why):
        for lis in self.listeners:
            _call(lis, "mark", name, why)

    def publish(self, key, payload):
        for lis in self.listeners:
            _call(lis, "publish", key, payload)


def _call(lis, method: str, *args):
    fn = getattr(lis, method, None)
    if fn is None:
        return None
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 -- the instrument never fails the run
        return None


def _begun(lis, name: str, why: str, params, started: float):
    """D1143: a task that began at `started` (wall clock) -- a quiet step opened late; `phase_start` for a
    listener that keeps no time of its own."""
    if hasattr(lis, "phase_begun"):
        return _call(lis, "phase_begun", name, why, params, started)
    return _call(lis, "phase_start", name, why, params)


#: D1143: the quiet steps open on this thread with no task yet -- one is opened when a task starts under it
_PENDING = threading.local()


def _pending() -> list[dict]:
    if not hasattr(_PENDING, "frames"):
        _PENDING.frames = []
    return _PENDING.frames


def _open_quiet(lis) -> None:
    """D1143: a task starts under quiet steps still without one: each becomes a task from its own start, the
    outer first, so what runs in it is under it (an agent's turn in `knowledge: workbench` was a top task --
    Design -- and the step a task after it)."""
    for f in _pending():
        if not f["open"] and f["lis"] is lis:
            f["open"] = True
            f["token"] = _begun(lis, f["name"], f["why"], f["params"], time.time() - (time.perf_counter() - f["t0"]))
            with _LOCK:
                stack = _stack()
                for i, entry in enumerate(stack):
                    if entry[0] == f["name"] and entry[1] == f["t0"]:
                        stack[i] = (entry[0], entry[1], f["token"])
                        break


def _refresh() -> None:
    global _LISTENER
    ls = [x for x in (_PRIMARY, *_EXTRA) if x is not None]
    _LISTENER = None if not ls else ls[0] if len(ls) == 1 else _Tee(ls)


def set_listener(listener) -> None:
    """`listener.phase_start(name, why, params) -> token`,
    `listener.phase_end(token, name, seconds, failed, output)` and
    `listener.phase_update(token, name, output)` (what a running phase has so far, D493);
    any may be missing. `output` is the dict the block filled through
    `with phase(...) as out:` (D470)."""
    global _PRIMARY
    _PRIMARY = listener
    _refresh()


def clear_listener() -> None:
    global _PRIMARY
    _PRIMARY = None
    _refresh()


def add_listener(listener) -> None:
    """A listener beside the primary one (D683): the run's journal, whatever the TUI does."""
    if listener not in _EXTRA:
        _EXTRA.append(listener)
    _refresh()


def remove_listener(listener) -> None:
    if listener in _EXTRA:
        _EXTRA.remove(listener)
    _refresh()


def progress(**fields) -> None:
    """What the innermost open phase has produced so far (D493), e.g. a streaming model's
    thinking, sent live under the keys the block's final `out` will carry. Costs nothing
    without a listener and never fails the run."""
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


def _outs() -> list[dict]:
    if not hasattr(_OUTS, "stack"):
        _OUTS.stack = []
    return _OUTS.stack


def said(**fields) -> None:
    """(D1233) Facts for the output of the innermost phase open on this thread, read at its end -- what
    code inside the block found (the checks a gate never reached) without the block's `out` in hand."""
    outs = _outs()
    if outs and fields:
        outs[-1].update(fields)


def _tags() -> dict:
    return dict(getattr(_TAGS, "tags", None) or {})


@contextmanager
def tagged(**tags):
    """Every phase started inside, on this thread or one it hands work to (`carried`), carries
    `tags` among its params (D739): `with tagged(part="exp"):` puts a part's work under its part."""
    before = getattr(_TAGS, "tags", None)
    _TAGS.tags = {**(before or {}), **{k: v for k, v in tags.items() if v not in (None, "")}}
    try:
        yield
    finally:
        _TAGS.tags = before


def carried(fn):
    """`fn` for another thread, its phases under the phase open here (D739): a stage's
    measurements on the pool's threads sit under their stage, not at the top. A listener
    with `adopt(token)` hears the caller's token on the worker, then None."""
    lis = _LISTENER
    with _LOCK:
        stack = _stack()
        top = stack[-1][2] if stack else None
    tags = _tags()
    if lis is None or (top is None and not tags):
        return fn

    def run(*args, **kw):
        if top is not None:
            _call(lis, "adopt", top)
        with tagged(**tags):
            try:
                return fn(*args, **kw)
            finally:
                if top is not None:
                    _call(lis, "adopt", None)

    return run


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
    """A loop's live standings for the observer (D418l): the latest value per key (what is
    proven, the best per part, how many judged). Costs nothing without a listener."""
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
    """path -> (calls, seconds, running). Finished spans by where they ran, plus every
    phase still open on any thread with its elapsed so far, so a long call is attributed
    while it runs."""
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
    """path -> seconds the phase currently open there has run so far (D487)."""
    now = time.perf_counter() if now is None else now
    with _LOCK:
        out: dict[tuple[str, ...], float] = {}
        for stack in _OPEN.values():
            for depth, (name, t0, _tok) in enumerate(stack):
                key = tuple(n for n, *_ in stack[:depth + 1])
                out[key] = max(out.get(key, 0.0), now - t0)
        return out


#: D1047: a quiet phase that ended well within this is a note on its parent, not a task
QUIET_S = 0.5


#: D1050: the wrappers open on this thread that take in the one agent or model turn they hold
_ABSORB = threading.local()


def _absorbing() -> list[dict]:
    if not hasattr(_ABSORB, "frames"):
        _ABSORB.frames = []
    return _ABSORB.frames


@contextmanager
def phase(name: str, why: str = "", quiet: bool = False, absorb: bool = False, inline: bool = False, silent: bool = False,
          quiet_s: float | None = QUIET_S, **params):
    """Time a block into `name`. Records even when the block raises, so a slow failure is
    not free.

    `why` and `params` go to the observer only; aggregation stays keyed by `name`. The block
    receives a dict for its output (`with phase(...) as out: out["reply"] = text`), which
    reaches the observer at exit (D470).

    D1047: `quiet` -- bookkeeping (a frontier, a brief, the tools' gate): no task while it runs; at its
    end a note on the task it ran in (`phase_note`) when it went well and fast, else a task after
    the fact (`phase_after`) -- a failure or a slow step is still seen as one. D1143: a task that starts in it
    (an agent's turn) makes it a task from its own start, the task under it.

    D1050: `absorb` -- a wrapper whose job is one agent or model turn (a draft, a critique, the plan);
    `inline` -- that turn. The wrapper's first inline turn opens no task of its own: its inputs join
    the wrapper's (`phase_params`), its live updates and its output are the wrapper's -- one task,
    named as the wrapper, showing the agent. A command after it is a task as ever. (D1247) The same agent's or
    model's next turns in the wrapper (a nudge, an answer, a resumed session) are the wrapper's too: the try is one
    task, its time the turns' total; each turn's time and words are data of its output (`turns`, `turn times`)."""
    if silent:
        yield from _silent(name, params)
        return
    frames = _absorbing()
    with _LOCK:
        top = _stack()[-1] if _stack() else None
    host = frames[-1] if inline and frames and top is not None and top[2] is frames[-1]["token"] else None
    if host is not None and host["used"] and not (params.get("worker") and params.get("worker") == host.get("worker")):
        host = None                            # a command after the turn: a task of its own
    if host is not None:
        yield from _inline(host, name, why, params)
        return
    own = {k: params[k] for k in TAG_KEYS if k in params and params[k] not in (None, "")}
    before = getattr(_TAGS, "tags", None)
    if own:                                    # (D1233) a phase's tags are its inner phases' too
        _TAGS.tags = {**(before or {}), **own}
    lis = _LISTENER
    token = None
    if lis is not None:
        tags = _tags()
        if tags:
            params = {**tags, **params}
        if not quiet:
            _open_quiet(lis)
            try:
                token = lis.phase_start(name, why, params)
            except Exception:  # noqa: BLE001
                lis = None
    t0 = time.perf_counter()
    pending = {"name": name, "why": why, "params": params, "t0": t0, "token": None, "open": False, "lis": lis} if quiet and lis is not None else None
    if pending is not None:
        _pending().append(pending)
    with _LOCK:
        stack = _stack()
        stack.append((name, t0, token))
        path = tuple(n for n, _t, _k in stack)
    failed = True
    output: dict = {}
    frame = {"token": token, "output": output, "used": False, "lis": lis} if absorb and token is not None else None
    if frame is not None:
        frames.append(frame)
    outs = _outs()
    outs.append(output)
    try:
        yield output
        failed = bool(output.pop("failed", False))     # D1046: a phase that ended but failed (a check that found failures)
    finally:
        work = bool(output.pop("_task", False))        # (D1235) a quiet step that did real work: a task, however fast
        if outs and outs[-1] is output:
            outs.pop()
        if own:
            _TAGS.tags = before
        if frame is not None and frames and frames[-1] is frame:
            frames.pop()
        if pending is not None:
            _PENDING.frames = [f for f in _pending() if f is not pending]
            token = pending["token"]
            quiet = not pending["open"]    # D1143: a task opened under it -- it is a task, ended as one
        secs = time.perf_counter() - t0
        with _LOCK:
            stack = _stack()
            if stack and stack[-1][0] == name:
                stack.pop()
        record(name, secs, path)
        if lis is not None:
            try:
                if not quiet:
                    lis.phase_end(token, name, secs, failed, output)
                elif failed or (quiet_s is not None and secs >= quiet_s) or work:   # quiet_s None: a note however long
                    _open_quiet(lis)       # D1143: the quiet steps it ran in, a task when it is one
                    _call(lis, "phase_after", name, why, params, secs, failed, output)
                else:
                    _call(lis, "phase_note", name, why, params, output)
            except Exception:  # noqa: BLE001
                pass


def _silent(name: str, params: dict):
    """(D1235) A span the timing tree keeps (where the time went) that is no task: its listener hears nothing, what
    starts in it is the task around it's -- the climb through the stages, the choice inside a decision."""
    own = {k: params[k] for k in TAG_KEYS if k in params and params[k] not in (None, "")}
    before = getattr(_TAGS, "tags", None)
    if own:
        _TAGS.tags = {**(before or {}), **own}
    t0 = time.perf_counter()
    with _LOCK:
        stack = _stack()
        stack.append((name, t0, stack[-1][2] if stack else None))     # the task around it stays the one its own work is under
        path = tuple(n for n, _t, _k in stack)
    output: dict = {}
    try:
        yield output
    finally:
        if own:
            _TAGS.tags = before
        with _LOCK:
            stack = _stack()
            if stack and stack[-1][0] == name:
                stack.pop()
        record(name, time.perf_counter() - t0, path)


def _inline(host: dict, name: str, why: str, params: dict):
    """D1050: the one turn a wrapper holds, as the wrapper itself: no task of its own -- its inputs
    (the prompt, the command, which agent) added to the wrapper's, its live updates under the
    wrapper's token, its output into the wrapper's output."""
    later = host["used"]
    host["used"] = True
    host.setdefault("worker", params.get("worker") or "")
    lis, token = host["lis"], host["token"]
    own = {k: params[k] for k in TAG_KEYS if k in params and params[k] not in (None, "")}
    before = getattr(_TAGS, "tags", None)
    if own:                                    # (D1233) the turn's worker is the wrapper's, and its inner phases'
        _TAGS.tags = {**(before or {}), **own}
    if lis is not None and not later:          # a later turn's inputs are words in its conversation, not the try's
        _call(lis, "phase_params", token, {**params, "turn": name, **({"turn why": why} if why else {})})
    t0 = time.perf_counter()
    with _LOCK:
        stack = _stack()
        stack.append((name, t0, token))         # its live updates are the wrapper's
        path = tuple(n for n, _t, _k in stack)
    output: dict = {}
    outs = _outs()
    outs.append(output)
    try:
        yield output
    finally:
        if outs and outs[-1] is output:
            outs.pop()
        if own:
            _TAGS.tags = before
        with _LOCK:
            stack = _stack()
            if stack and stack[-1][0] == name:
                stack.pop()
        secs = time.perf_counter() - t0
        record(name, secs, path)
        if output.pop("failed", None) and name.startswith("tool:"):   # (D1235) a command it took in failed: so did it
            host["output"]["failed"] = True
        if host.get("worker"):                   # (D1247) each turn's time, kept for its conversation
            times = host.setdefault("times", [])
            times.append({"turn": len(times) + 1, "seconds": round(secs, 3), **({"why": why} if why else {}),
                          **{k: output[k] for k in ("exit", "timed out") if k in output}})
        host["output"].update(output)            # the wrapper's end carries the turn's output
        if len(host.get("times") or ()) > 1:
            host["output"]["turns"] = len(host["times"])
            host["output"]["turn times"] = list(host["times"])


def turn_carry(worker: str) -> dict | None:
    """(D1247) What the turns of `worker` in the wrapper open here share -- the agent's conversation carried from
    one turn to the next (its steps file, its count, its tokens) -- or None when a turn of it now would be no
    wrapper's (a task of its own)."""
    frames = _absorbing()
    with _LOCK:
        top = _stack()[-1] if _stack() else None
    if not frames or top is None or top[2] is not frames[-1]["token"]:
        return None
    host = frames[-1]
    if host["used"] and host.get("worker") != worker:
        return None
    return host.setdefault("carry", {})


def snapshot() -> dict[str, tuple[int, float]]:
    with _LOCK:
        return {k: (v[0], v[1]) for k, v in _PHASES.items()}


def elapsed_s() -> float:
    return time.perf_counter() - _STARTED


def human_s(seconds: float) -> str:
    """Seconds in a readable unit (D418): 45s, 3m12, 2h05, 1d3h."""
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


#: The five roles (D418k), by the first word of a phase name. A phase that is a model
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
                  "invent", "prototype", "patched", "compute", "try"),
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
    registered node wins over the word list above."""
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

    `total_s` overrides the denominator: the TUI passes its active run clock, since this
    module's window keeps running while idle between reruns."""
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
        # the per-call average counts a phase still open too; the state column carries
        # that call's own elapsed
        per = human_s(secs / calls) if calls else ""
        state = f"▶ {human_s(open_now[key])}" if running and key in open_now else ("running" if running else "")
        if folded is None:
            mark = ""
        else:
            mark = ("▸ " if is_folded else "▾ ") if kids else "  "
        # The model is a marker on a role, not a role (D418k): a model-call phase takes
        # its caller's color and shows "(model)"; a phase with a model call directly
        # inside gets a "·model" suffix.
        own = role_of(key[-1])
        calls_model = any(role_of(k[-1]) == "model" for k in kids)
        label = key[-1]
        if own == "model":
            parent_role = next((role_of(n) for n in reversed(key[:-1])
                                if role_of(n) not in (None, "model")), None)
            role = f"{parent_role or ''}+model"
        elif calls_model:
            role = f"{own or ''}+model"
            # the marker must survive the label width: shorten the name, never the mark
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
        # a gap this size is real work no phase names yet
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
