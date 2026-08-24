"""Capabilities: what a problem declares so the loop can run a stage it owns (D515).

    prototype:                       # the document's block, or `Problem.prototype()` in code
      language: python-int           # the numpy-integer DSL (`flux_loop.pyint`)
      toolkit: <Toolkit>             # the blocks a prototype may call, with their docs
      check: <callable>              # (code, part, state) -> Verdict, the problem's gate on prototypes
      family: true                   # knobs declared with a SPACE are searched at check

Prose about the language lives in the loop (`flux_loop.prototype`); a problem declares only
what it alone knows: what `design(x)` computes for a part, the input domain, the gate, the
toolkit and the check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover
    from .types import LoopState, Verdict

__all__ = ["Prototype", "Target", "Toolkit"]


@dataclass(frozen=True)
class Toolkit:
    """The blocks a prototype calls, as the prompt describes them: `docs` (one line per block,
    for the first prompt), `reminder` (the signatures, repeated on every repair turn so the
    model does not invent blocks, D483), `compose` (how the blocks fit together), `shape` (the skeleton every
    prototype takes), and `operator_docs` (the campaign's own verified parts as blocks the
    part being designed may call, D487; a dict of part -> array-form code in, prose out)."""

    docs: str = ""
    reminder: str = ""
    compose: str = ""
    shape: str = ""
    operator_docs: Callable[[dict[str, str]], str] | None = None
    # How the check composes and reads a run (D516): the prelude before the code and its line
    # count, toolkit misuse, the audit before the harness, sandbox-error explanation, and
    # relocating line numbers back to the model's text.
    prelude: Callable[[str, dict[str, str]], str] | None = None
    prelude_lines: Callable[[dict[str, str]], int] | None = None
    misuse: Callable[[str, set[str]], list[str]] | None = None
    audit: Callable[[], str] | None = None
    explain: Callable[..., str] | None = None
    relocate: Callable[[str, int], str] | None = None


@dataclass(frozen=True)
class Target:
    """What a passing prototype becomes (D516): `spell(full_code, part)` raises when the
    target's transpiler cannot spell it (the construct named); `depth(full_code)` is the
    logic-depth proxy the ladder's optimise mode scores; `describe_depth` says it in words;
    `faster_advice` is what the optimise prompt adds."""

    spell: Callable[[str, str | None], Any]
    depth: Callable[[str], dict[str, Any]] | None = None
    describe_depth: Callable[[dict[str, Any]], str] | None = None
    faster_advice: Callable[[], str] | None = None


@dataclass(frozen=True)
class Prototype:
    """The prototype stage, declared. `reference(part)` says what `design(x)` computes (the
    prompt's first line: "for e^x"); `domain` what an input is; `gate` what the check demands
    of every input; `contract` the static line of the stage's prefix (`{part}` is the part);
    `domain_size` how many inputs the gate runs, for the rule that refuses a rewrite next to
    a nearly-right seed (D501: fewer than 5% failing); `score_unit` what a score counts."""

    check: Callable[[str, str | None, "LoopState"], "Verdict"] | None = None   # the whole gate; or the parts below
    harness: str = ""                                          # run after the prototype: prints `OUT <hex>`
    judge: Callable[..., Any] | None = None                    # (part, bytes, code, state, extra) -> Judgement
    family_judge: Callable[[str | None], str] | None = None    # the gate as source, for the family harness
    cost: Callable[[str], int] | None = None                   # ranks a family's passing members
    target: Target | None = None
    reference: Callable[[str | None], str] = lambda part: f"the function `{part}`"
    domain: str = "an input"
    gate: str = "correct on every input the check runs"
    contract: str = ""
    language: str = "python-int"
    toolkit: Toolkit | None = None
    family: bool = True
    domain_size: int = 0
    score_unit: str = " inputs beyond the budget"
    extra: dict[str, Any] = field(default_factory=dict)
