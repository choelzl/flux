"""CAPABILITIES (docs/decisions.md D515, review step 5.2): what a problem DECLARES so the loop
can run a stage it owns -- the first is the prototype stage.

    prototype:                       # the document's block, or `Problem.prototype()` in code
      language: python-int           # the numpy-integer DSL (`flux_loop.pyint`)
      toolkit: <Toolkit>             # the blocks a prototype may call, with their docs
      check: <callable>              # (code, part, state) -> Verdict, the problem's gate on prototypes
      family: true                   # knobs declared with a SPACE are searched at check

Before D515 the stage read eight hooks off the problem -- the spec prompt, the prefix, the
reminder, the check, the history, the example, the "prefer edits" rule, the score's unit --
and the NLU wrote every one of them, most of it prose about the LANGUAGE (the subset, the
shape, "declare knobs") that no other problem should have to repeat. The language's prose is
the loop's now (`flux_loop.prototype`); what a problem says is what only it knows: what
`design(x)` must compute for a part, what an input is, what the gate demands, the toolkit
and its documentation, the check.
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
    for the first prompt), `reminder` (the signatures, for every repair turn -- by the third
    attempt a model invents blocks that do not exist unless the list is in front of it
    again, D483), `compose` (how the blocks fit together), `shape` (the skeleton every
    prototype takes), and `operator_docs` (the campaign's own verified parts as blocks the
    part being designed may call, D487; a dict of part -> array-form code in, prose out)."""

    docs: str = ""
    reminder: str = ""
    compose: str = ""
    shape: str = ""
    operator_docs: Callable[[dict[str, str]], str] | None = None
    # how the check composes and reads a run (D516): the blocks and the other parts' verified
    # prototypes put before the code, how many lines that is, what counts as fighting the
    # toolkit, the audit appended before the harness, how a sandbox error is explained and
    # how a message's line numbers are moved back to the model's text
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
