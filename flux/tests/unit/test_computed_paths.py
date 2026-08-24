"""Paths a module computes from its own location must resolve (docs/decisions.md D335).

The reorganisation around the four module types (D296) inserted directory levels, and
`Path(__file__).parents[N]` does not move with them. Two modules were left pointing at the wrong
place, and neither failed loudly:

  * `flux_evaluator_native.build._CORE_DIR` lost the `rust` segment when `core/` became a
    directory of module dirs. Its guard checked `_CORE_DIR.exists()`, and `core/` still existed,
    so cargo ran in a directory with no manifest and reported "could not find Cargo.toml ... or
    any parent directory" — which reads like a parent-index bug and sent the first diagnosis after
    the wrong one.
  * `profile_exhaustive_search.FLUX_ROOT` resolved to `core` and then appended `"core/ir/..."`,
    giving `flux/core/core/ir/...`.

Both roots EXIST. That is the whole difficulty: a check that the base directory is there passes
while every path built from it is wrong, which is why this checks the composed paths.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]

# (module path, the attribute it computes, the number of parents it takes, appended segments seen
# in the file). Kept explicit rather than inferred: a check that re-derives the arithmetic from
# the source would repeat whatever mistake the source makes.
_COMPUTED = [
    ("evaluator/native/src/flux_evaluator_native/build.py", "_CORE_DIR"),
    ("core/rust/benches/profile_exhaustive_search.py", "FLUX_ROOT"),
    ("applications/interconnect/lib/src/flux_interconnect/vendored.py", "VENDOR_DIR"),
    ("mentor/protocols/src/flux_protocols/registry.py", "_SPECS_DIR"),
    ("applications/interconnect/lib/src/flux_interconnect/flow.py", "FLUX_ROOT"),
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


def test_the_native_crate_directory_holds_a_manifest():
    """The base existing is not enough — `core/` existed while the crate lived in `core/rust/`.
    Cargo needs the manifest, so that is what is checked."""
    crate = _resolve(FLUX_ROOT / "evaluator/native/src/flux_evaluator_native/build.py", "_CORE_DIR")
    assert (crate / "Cargo.toml").is_file(), f"no Cargo.toml under {crate}"


def test_every_path_the_bench_builds_from_its_root_exists():
    """The second failure's exact shape: a root that exists, and every path built on it missing."""
    source = FLUX_ROOT / "core/rust/benches/profile_exhaustive_search.py"
    root = _resolve(source, "FLUX_ROOT")
    appended = re.findall(r'FLUX_ROOT / "([^"]+)"', source.read_text())
    assert appended, "the bench no longer builds paths from FLUX_ROOT — update this test"
    missing = [s for s in appended if not (root / s).exists()]
    assert not missing, f"FLUX_ROOT={root} but these do not exist: {missing}"


def test_no_unit_test_relies_on_another_having_set_sys_path():
    """A test importing the study must reach it as the PACKAGE it now is.

    The study moved into `flux_interconnect.flow` (D346), which is on PYTHONPATH like every other
    package here — so no test needs a `sys.path` insert to find it, and one that still writes
    `import demo` is reaching for a script that is now only a command line.

    `test_screen_routability.py` did not, and passed anyway — some other file was collected first
    and did it. Run alone, nine of its nineteen tests failed with `ModuleNotFoundError: No module
    named 'demo'`. It went unnoticed because the suite is run as a directory; the nightly
    integration sweep runs FILE BY FILE, which is exactly where this shape of bug surfaces.
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
