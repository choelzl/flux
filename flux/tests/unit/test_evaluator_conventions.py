"""The evaluator folder explains itself (D347): every adapter wraps its tool directly, and a
folder with no code says why."""

from __future__ import annotations

from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]
EVALUATORS = sorted(p for p in (FLUX_ROOT / "evaluator").iterdir() if p.is_dir())



@pytest.mark.parametrize("directory", [d for d in EVALUATORS if not any(d.rglob("*.py"))],
                         ids=lambda d: d.name)
def test_a_placeholder_says_why_it_is_empty(directory):
    """A folder with no code has a README saying the backend is not built."""
    readme = directory / "README.md"
    assert readme.is_file(), f"{directory.name}/ holds no code and no README explaining why"
    # Phrasing varies ("not built", "not yet built"); only require that it says so.
    text = readme.read_text().lower()
    assert any(phrase in text for phrase in ("not built", "not yet built", "not implemented")), (
        f"{directory.name}/README.md does not say the backend is unbuilt")


def test_the_central_readme_states_the_rule():
    """The central evaluator README states the rule once, where a reader looks first."""
    text = (FLUX_ROOT / "evaluator" / "README.md").read_text()
    assert "wraps its tool directly" in text
