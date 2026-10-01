"""Passes (D593): a campaign runs pass after pass until someone stops it -- `flux stop <record>`,
Ctrl-C, quitting the TUI -- or until the cap a caller asked for (`--passes N`, the document's
`budget.passes`), never on its own.

A pass that ends at rest (every ladder spent, nothing sent back) is followed by an
exploring pass (`LoopRequest.explore`): every admitted design goes back to the model or the
coding agent with its numbers and what better means from here. The gate and the decision do
not change, so exploring never admits a design that fails, nor decides one that misses the
goal over one that meets it.

When nothing can draft a new design (no model or agent generates for the campaign -- a
renderer over a finite space that is fully measured), another pass would change nothing: the
run waits for a stop or an operator's note instead of spinning or leaving.
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import time
from typing import Any, Callable, Iterator

from . import ops

__all__ = ["between_passes", "carrying", "mark", "run_passes", "this_run"]

#: The run whose passes share one search (D738): a policy's place carries from pass to pass of
#: a run, and a `run_loop` outside one (or a new run) starts the search afresh from the record.
_RUN: contextvars.ContextVar[object | None] = contextvars.ContextVar("flux_passes_run", default=None)


def this_run() -> object | None:
    return _RUN.get()


@contextlib.contextmanager
def carrying(mark: object | None = None) -> Iterator[object]:
    """The passes run inside share one search (D738); the same `mark` again carries it on."""
    mark = object() if mark is None else mark
    token = _RUN.set(mark)
    try:
        yield mark
    finally:
        _RUN.reset(token)


class _Held:
    """A feedback channel with notes put back in front: the note that woke a waiting run
    reaches the next pass's prompts as if it had just been typed."""

    def __init__(self, inner: Any, notes: list) -> None:
        self._inner, self._held = inner, list(notes)
        self.active = getattr(inner, "active", True)

    def drain(self) -> list:
        out, self._held = self._held, []
        return out + (list(self._inner.drain()) if self._inner is not None else [])

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _scripted_spent(proposer: Any) -> bool:
    """A scripted proposer is a fixed script, not a model: once every reply was given, more
    passes would replay its last one. A tuple: any of them spent."""
    if isinstance(proposer, (tuple, list)):
        return any(_scripted_spent(p) for p in proposer)
    replies, prompts = getattr(proposer, "replies", None), getattr(proposer, "prompts", None)
    return isinstance(replies, list) and isinstance(prompts, list) and len(prompts) >= len(replies)


def mark(name: str, **what: Any) -> None:
    """A run's milestone in its journal (D739): `pass` as each pass starts, `ended` with why the
    run ended, `waiting` while it waits for a note -- what the live tree hangs its branches on."""
    import json

    from flux_profile import mark as _mark

    _mark(name, json.dumps(what, default=str))


def _ended(why: str, rests: int, feedback: Any) -> tuple[bool, int, Any]:
    mark("ended", why=why)
    return False, rests, feedback


def between_passes(out: Any, n: int, *, passes: int = 0, rests: int = 0, feedback: Any = None,
                   proposer: Any = None, say: Callable[[str], None] = print,
                   poll_s: float = 2.0, sleep: Callable[[float], None] = time.sleep) -> tuple[bool, int, Any]:
    """After pass `n` (1-based) ended with `out`: (go on?, rests in a row, the feedback channel
    for the next pass). Stops only for a cap, a stop asked for, or a script that is spent."""
    if passes and n >= passes:
        return _ended(f"{passes} pass{'es' if passes != 1 else ''} done", rests, feedback)
    asked = ops.stop_requested()
    if asked:
        ops.clear_stop()
        say(f"stopping at the pass boundary: {asked}")
        return _ended(f"stopped: {asked}", rests, feedback)
    if proposer is not None and _scripted_spent(proposer):   # one proposer, or a tuple of them
        say("the scripted replies are spent; a script has nothing more to try")
        return _ended("the script is spent", rests, feedback)
    rests = rests + 1 if getattr(out, "at_rest", False) else 0
    if rests and not getattr(out, "explorable", True) and passes:
        say("at rest, and nothing here drafts a new design; the remaining passes would change nothing")
        return _ended("at rest: nothing new to try", rests, feedback)
    if rests and not getattr(out, "explorable", True):
        say("at rest, and nothing here drafts a new design (no model or coding agent generates for this "
            "campaign): waiting for a note, or `flux stop` / Ctrl-C to end")
        mark("waiting", why="at rest: waiting for a note or a stop")
        while True:
            asked = ops.stop_requested()
            if asked:
                ops.clear_stop()
                say(f"stopping: {asked}")
                return _ended(f"stopped: {asked}", rests, feedback)
            notes = list(feedback.drain()) if feedback is not None else []
            if notes:
                say(f"a note arrived: {getattr(notes[-1], 'text', notes[-1])!s:.120}; another pass")
                return True, 0, _Held(feedback, notes)
            sleep(poll_s)
    return True, rests, feedback


def _wave(run: Callable[[Any, Any], Any], request: Any, feedback: Any, nums: list[int], run_mark: object) -> list[Any]:
    """D747: passes `nums` at once, each on a thread of its own, sharing the run's search; each
    phase of a pass carries its number (`tagged(pass=…)`), so the tree keeps them apart."""
    import contextvars
    import threading

    from flux_profile import tagged

    outs: list[Any] = [None] * len(nums)
    errs: list[BaseException | None] = [None] * len(nums)

    def one(j: int, i: int) -> None:
        with carrying(run_mark), tagged(**{"pass": i}):
            try:
                outs[j] = run(request, feedback)
            except BaseException as exc:  # noqa: BLE001 -- said on the caller's thread
                errs[j] = exc

    threads = [threading.Thread(target=contextvars.copy_context().run, args=(one, j, i), name=f"flux-pass-{i}", daemon=True)
               for j, i in enumerate(nums)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    bad = next((e for e in errs if e is not None), None)
    if bad is not None:
        raise bad
    return outs


def run_passes(run: Callable[[Any, Any], Any], request: Any, *, passes: int | None = None, feedback: Any = None,
               proposer: Any = None, say: Callable[[str], None] = print, notes: bool = True) -> Any:
    """`run(request, feedback)` pass after pass, as `between_passes` says; the last result.
    `passes` None: the request's (the document's `budget.passes`; 0 = until stopped).
    `notes` False (`feedback: none`, D666): no channel, so a note never wakes a waiting run.
    D747: `request.parallel` passes at once (a wave), the next wave once all of them ended; on a
    server one at a time unless an admin allows parallel work. Passes at once do not see each
    other's designs, so the run ends with a short pass that decides over everything recorded."""
    from .pool import capped

    feedback = feedback if notes else None
    cap = int(request.passes if passes is None else passes)
    asked = max(1, int(getattr(request, "parallel", 1) or 1))
    width = capped(asked)
    if width < asked:
        say(f"  one pass at a time: the document asks {asked} at once; an admin allows parallel work in the loop's Advanced settings")
    n = rests = 0
    together = False
    with carrying() as run_mark:
        while True:
            k = min(width, cap - n) if cap else width
            if k <= 1:
                mark("pass", n=n + 1, explore=rests)
                out = run(dataclasses.replace(request, explore=rests), feedback)
            else:
                nums = list(range(n + 1, n + k + 1))
                for i in nums:
                    mark("pass", n=i, explore=rests, together=nums)
                outs = _wave(run, dataclasses.replace(request, explore=rests), feedback, nums, run_mark)
                together = True
                out = dataclasses.replace(outs[-1], at_rest=all(o.at_rest for o in outs),
                                          explorable=any(o.explorable for o in outs))
            n += max(1, k)
            go, rests, feedback = between_passes(out, n, passes=cap, rests=rests, feedback=feedback,
                                                 proposer=proposer, say=say)
            if not go:
                if together:                        # D747: one decision over what every pass recorded
                    mark("pass", n=n + 1, conclude=True)
                    say("\n── the decision, over every pass ──")
                    out = run(dataclasses.replace(request, explore=0, steps=0), feedback)
                return out
            nxt = min(width, cap - n) if cap else width
            said = f"passes {n + 1}–{n + nxt} at once" if nxt > 1 else f"pass {n + 1}"
            say(f"\n── {said}" + (f": at rest, exploring for a better design ({rests} in a row)" if rests else "") + " ──")
