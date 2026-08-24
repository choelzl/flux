"""One bounded ask-check-repair round, shared (D450).

Ask, parse, check for real, and on failure ask again with the failure text attached, up to N
attempts, keeping a transcript. What to ask, how to parse and what passing means are the
caller's. `flux_loop`'s generation loop is separate: it adds patching, reverts and sandboxes a
one-shot generator does not need.

`ask_until` when the model must produce the first value, `refine` when the caller already has
one and the model is only asked to fix it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["Refined", "ask_until", "refine"]


@dataclass(frozen=True)
class Refined:
    """What a bounded round produced. `attempts` counts the first attempt too, so
    `attempts=1` means it passed first time."""

    ok: bool
    value: Any
    why: str = ""                                   # why the last attempt was refused; "" if ok
    attempts: int = 0
    transcript: tuple[str, ...] = field(default_factory=tuple)


def _said(why: Any) -> tuple[str, str]:
    """A check's answer: `""`/None (passed), a failure text, or (text, kind) to name the kind
    in the transcript ("compile error", "schema error")."""
    if not why:
        return "", ""
    if isinstance(why, tuple):
        text, kind = why
        return str(text), str(kind)
    return str(why), "refused"


def refine(value: Any, *, check: Callable[[Any], Any], ask: Callable[[str], str],
           parse: Callable[[str], Any], repair: Callable[[Any, str, str], str],
           attempts: int = 3, reply: str = "",
           on_attempt: Callable[[int, Any, str], None] | None = None) -> Refined:
    """Check `value`; while it is refused and attempts remain, ask for a fix and check that.

    `check(value)` runs the real verification (a compiler, a schema, a simulator) and returns
    "" when it passes, else the failure text -- or `(text, kind)` to name the kind. `repair`
    builds the next prompt from `(value, why, reply)`; `parse` reads the fix out of the reply
    and returns None when there is none, which ends the round. `on_attempt(n, value, why)` is
    the caller's own logging.
    """
    transcript: list[str] = []
    n = 0
    for n in range(1, max(1, attempts) + 1):
        why, kind = _said(check(value))
        if not why:
            return Refined(True, value, "", n, tuple(transcript))
        transcript.append(f"--- attempt {n} {kind} ---\n{why}")
        if on_attempt is not None:
            on_attempt(n, value, why)
        if n >= max(1, attempts):
            break
        prompt = repair(value, why, reply)
        transcript.append(f"--- attempt {n + 1} prompt ---\n{prompt}")
        reply = ask(prompt)
        fixed = parse(reply)
        transcript.append(f"--- attempt {n + 1} response ---\n{reply}")
        if fixed is None:
            return Refined(False, value, f"{why}; the repair returned nothing usable",
                           n + 1, tuple(transcript))
        value = fixed
    return Refined(False, value, why, n, tuple(transcript))


def ask_until(prompt: str, *, ask: Callable[[str], str], parse: Callable[[str], Any],
              check: Callable[[Any], Any], repair: Callable[[Any, str, str], str],
              attempts: int = 3,
              unparsable: str | Callable[[str], str] = "the reply could not be parsed",
              on_attempt: Callable[[int, Any, str], None] | None = None) -> Refined:
    """Ask, parse, check; on failure ask again with the failure attached. Bounded by
    `attempts`, the first attempt included.

    An unparsable reply is a refusal like any other: `unparsable` says why (a string, or a
    callable over the reply, which is how a truncated answer gets told it was cut off), and
    the next prompt is built by `repair(None, why, reply)` from the reply itself. The
    transcript is the prompt, the reply and the refusal of every attempt, in order.
    """
    transcript: list[str] = []
    value: Any = None
    why = kind = ""
    n = 0
    for n in range(1, max(1, attempts) + 1):
        transcript.append(f"--- attempt {n} prompt ---\n{prompt}")
        reply = ask(prompt)
        transcript.append(f"--- attempt {n} response ---\n{reply}")
        value = parse(reply)
        if value is None:
            why = unparsable(reply) if callable(unparsable) else str(unparsable)
            kind = "unparsable"
        else:
            why, kind = _said(check(value))
            if not why:
                return Refined(True, value, "", n, tuple(transcript))
        transcript.append(f"--- attempt {n} {kind} ---\n{why}")
        if on_attempt is not None:
            on_attempt(n, value, why)
        if n >= max(1, attempts):
            break
        prompt = repair(value, why, reply)
    return Refined(False, value, why, n, tuple(transcript))
