"""Reserved-identifier checking, over whichever word set a language supplies (D51, D453).

The word sets stay separate per language (`"wire"` is a Verilog keyword but a fine C++
identifier); the check and its error are shared. Without it a collision
surfaces as a raw syntax error deep inside a generated file the caller never wrote.
"""

from __future__ import annotations

from typing import Callable, Iterable

from .errors import InvalidSpecError


def reserved_check(words: Iterable[str], *, language: str, tool: str, decision: str
                   ) -> Callable[..., None]:
    """A `check_not_reserved(name, *, context)` for one language.

    `language` names what reserves the word ("Verilog/SystemVerilog"), `tool` what would have
    failed instead ("Verilator"), and `decision` the entry that records the finding -- so the
    message a caller reads says which identifier, in which role, and why this is caught here
    rather than by the compiler.
    """
    reserved = frozenset(words)

    def check_not_reserved(name: str, *, context: str) -> None:
        if name in reserved:
            raise InvalidSpecError(
                f"{context}={name!r} is a reserved {language} identifier -- choose a different "
                f"one (this is caught here, before {tool}, on purpose: a real reserved-word "
                "collision otherwise surfaces as a raw error deep in a generated file the "
                f"caller never wrote by hand, docs/decisions.md {decision})."
            )

    return check_not_reserved


__all__ = ["reserved_check"]
