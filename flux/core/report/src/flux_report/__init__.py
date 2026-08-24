"""The shared report grammar (D397 phase 4): the sections every loop's report ends with.

The demos converged on one closing grammar -- an answer-first banner, then WHAT THIS
RUN ESTABLISHED, NOT ESTABLISHED, REFUSED (N) capped with an honest "... and N more",
and since D398 the operator's notes. This module is that grammar's single home: each
helper returns LINES (the caller prints, so a TUI can capture them the same way), and
the section headers are written here once so no loop can drift into a synonym.

What stays per loop: everything above the closing sections. The answer itself, the
front table, the per-domain columns -- those are the loop's own voice.
"""

from __future__ import annotations

from typing import Callable, Sequence

__all__ = ["banner", "established", "not_established", "notes", "refused"]


def banner(title: str, *, width: int = 79) -> str:
    """The answer-first rule: `══ THE ANSWER ═════...` out to `width` columns."""
    lead = f"══ {title.strip()} "
    return lead + "═" * max(0, width - len(lead))


def established(lessons: Sequence[str]) -> list[str]:
    """WHAT THIS RUN ESTABLISHED, one dash per lesson; [] when there are none."""
    if not lessons:
        return []
    return ["WHAT THIS RUN ESTABLISHED", *[f"  - {line}" for line in lessons]]


def not_established(items: Sequence[str]) -> list[str]:
    """NOT ESTABLISHED: what the run honestly cannot claim; [] when nothing is owed."""
    if not items:
        return []
    return ["NOT ESTABLISHED", *[f"  - {line}" for line in items]]


def refused(items: Sequence, *, cap: int = 6,
            render: Callable[[object], str] = str) -> list[str]:
    """REFUSED (N), each with its reason, capped -- the tail is counted, never
    silently dropped. `render` turns the loop's own refusal type into a line."""
    if not items:
        return []
    lines = [f"REFUSED ({len(items)})"]
    lines += [f"  - {render(item)}" for item in items[:cap]]
    if len(items) > cap:
        lines.append(f"  ... and {len(items) - cap} more")
    return lines


def notes(texts: Sequence[str]) -> list[str]:
    """The operator's guidance during the run (D388/D398): echoed so a typed line is
    visible in the same report its influence would show up in."""
    if not texts:
        return []
    return [f"OPERATOR GUIDANCE ({len(texts)} note(s), advisory; persisted in the "
            "campaign record)", *[f'  * "{t}"' for t in texts]]
