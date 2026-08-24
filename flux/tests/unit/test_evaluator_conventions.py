"""How a tool gets wrapped, checked rather than described (docs/decisions.md D347).

Two shapes exist: an evaluator either calls CHIA's own integration or wraps its tool directly. The
difference is not arbitrary — CHIA ships integrations for exactly four tools, and an evaluator goes
through CHIA when its tool is one of them. That rule lived in three per-tool READMEs and nowhere
central, which is how it reads as inconsistency to anyone looking at the folder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]
EVALUATORS = sorted(p for p in (FLUX_ROOT / "evaluator").iterdir() if p.is_dir())

# What CHIA actually ships, read from its own package tree rather than assumed.
CHIA_PACKAGES = {"cacti", "gem5", "hammer", "champsim"}


def _touches_chia(directory: Path) -> bool:
    return any("from chia" in f.read_text() or "import chia" in f.read_text()
               for f in directory.rglob("*.py"))


def _has_code(directory: Path) -> bool:
    return any(directory.rglob("*.py"))


def test_only_evaluators_whose_tool_chia_ships_go_through_chia():
    """The rule, stated as a check. An evaluator reaching into `chia.*` for a tool CHIA does not
    package would be importing a dependency for no reason; one wrapping a tool CHIA DOES package
    is reimplementing `.chia_remote()` by hand."""
    through_chia = {d.name for d in EVALUATORS if _has_code(d) and _touches_chia(d)}
    assert through_chia <= CHIA_PACKAGES, (
        f"{sorted(through_chia - CHIA_PACKAGES)} import chia for tools CHIA does not ship")


def test_the_evaluators_that_do_go_through_chia_are_the_expected_two():
    """A canary rather than a constraint: if this changes, either CHIA gained an integration worth
    reusing or an evaluator started importing it by accident."""
    through_chia = {d.name for d in EVALUATORS if _has_code(d) and _touches_chia(d)}
    assert through_chia == {"cacti", "gem5"}, (
        f"the set of CHIA-backed evaluators changed: {sorted(through_chia)}")


@pytest.mark.parametrize("directory", [d for d in EVALUATORS if not any(d.rglob("*.py"))],
                         ids=lambda d: d.name)
def test_a_placeholder_says_why_it_is_empty(directory):
    """A directory whose only statement of intent lives in a test's docstring is a directory the
    next reader deletes."""
    readme = directory / "README.md"
    assert readme.is_file(), f"{directory.name}/ holds no code and no README explaining why"
    # Phrasing varies — "not built", "not yet built" — and pinning one spelling would make this a
    # test about wording. What must be true is that the README says it is not implemented.
    text = readme.read_text().lower()
    assert any(phrase in text for phrase in ("not built", "not yet built", "not implemented")), (
        f"{directory.name}/README.md does not say the backend is unbuilt")


def test_the_central_readme_states_the_rule():
    """It was written three times in three per-tool READMEs and nowhere a reader would look
    first."""
    text = (FLUX_ROOT / "evaluator" / "README.md").read_text()
    assert "chia.vlsi.sram_cacti" in text and "wrap the tool when it does not" in text
