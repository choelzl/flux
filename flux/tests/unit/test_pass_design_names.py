"""D1062: one pass, one design -- its repairs and continuations are revisions of it (#4.2), not new designs."""

from __future__ import annotations

from flux_loop.task import PromptProblem
from flux_loop.types import PartState


class _State:
    def __init__(self):
        self.parts = {}

    def part(self, name):
        return self.parts.setdefault(name or "*", PartState())


def test_a_pass_numbers_its_design_once_and_its_drafts_as_revisions():
    p = object.__new__(PromptProblem)
    p._count = 3                                       # numbered on from the record
    one = _State()
    assert [p._design_name("x", None, one) for _ in range(3)] == ["x#4", "x#4.2", "x#4.3"]
    assert p._design_name("lo", "lo", one) == "lo#4"   # D1075: another part of the pass shares its number
    assert p._design_name("lo", "lo", one) == "lo#4.2"
    nxt = _State()                                      # the next pass: a new design
    assert p._design_name("x", None, nxt) == "x#5"
    assert p._design_name("x", None, None) == "x#6"     # outside a pass: every draft its own
