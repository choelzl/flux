"""Paths a module computes from its own location must resolve (D335).

`Path(__file__).parents[N]` does not move when directory levels are inserted, and the base
directory usually still exists, so a check on the base passes while every composed path is
wrong. This checks the composed paths.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]

# (module path, computed attribute, number of parents, appended segments). Kept explicit: a
# check that re-derives the arithmetic from the source would repeat its mistake.
_COMPUTED = [
]


def _resolve(source: Path, name: str) -> Path | None:
    """Evaluate the module-level `NAME = Path(__file__).resolve().parents[N] / ...` assignment."""
    tree = ast.parse(source.read_text())
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == name for t in node.targets)):
            continue
        text = ast.unparse(node.value)
        match = re.search(r"parents\[(\d+)\]", text)
        if not match:
            return None
        base = source.resolve().parents[int(match.group(1))]
        for segment in re.findall(r"/ '([^']+)'|/ \"([^\"]+)\"", text):
            base = base / (segment[0] or segment[1])
        return base
    return None


@pytest.mark.parametrize("relative,name", _COMPUTED, ids=[f"{n}" for _r, n in _COMPUTED])
def test_a_computed_path_points_at_something(relative, name):
    resolved = _resolve(FLUX_ROOT / relative, name)
    assert resolved is not None, f"{name} is not a simple parents[N] expression any more"
    assert resolved.exists(), f"{relative}: {name} resolves to {resolved}, which does not exist"


def test_no_unit_test_relies_on_another_having_set_sys_path():
    """Tests import the study as the package `flux_interconnect.flow` (D346), not `import demo`.

    Such a test can pass in a directory run when another file sets up `sys.path` first, and fail
    when run alone, as the nightly integration sweep does.
    """
    unit = FLUX_ROOT / "tests" / "unit"
    guilty = []
    for path in sorted(unit.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue          # this file names the pattern it looks for, in prose and in code
        text = path.read_text()
        if "import demo\n" in text or "import demo " in text:
            guilty.append(path.name)
    assert not guilty, (
        "these import the study as a script rather than as `flux_interconnect.flow`, which only "
        f"works when something else has put it on sys.path: {guilty}")
