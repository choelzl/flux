"""D1171: the test areas (tests/areas.py) stay whole -- `check.py --area` and `--changed` run a part of the suite
only while every unit test file and every e2e step is in an area, and the map names nothing that is not there."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

FLUX = Path(__file__).resolve().parents[2]
# loaded by path: tests/ and tests/e2e on sys.path would lend their `check`, `steps` ... to every test in the worker
_spec = importlib.util.spec_from_file_location("flux_test_areas", FLUX / "tests/areas.py")
areas = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = areas
_spec.loader.exec_module(areas)
ORDER = ast.literal_eval(next(n.value for n in ast.parse((FLUX / "tests/e2e/steps/__init__.py").read_text()).body
                              if isinstance(n, ast.Assign) and n.targets[0].id == "ORDER"))


def test_every_unit_test_file_is_in_an_area():
    files = {p.name for p in (FLUX / "tests/unit").glob("test_*.py")}
    mapped = {f for a in areas.AREAS for f in areas.unit_files(a)}
    assert sorted(files - mapped) == []


def test_every_unit_glob_names_a_file():
    empty = [(a, g) for a, spec in areas.AREAS.items() for g in spec["unit"] if not list((FLUX / "tests/unit").glob(g))]
    assert empty == []


def test_every_e2e_step_is_in_an_area_module():
    steps = areas.e2e_steps()
    assert len(steps) == len(ORDER) > 0
    assert {m for m, _ in steps.values()} <= set(areas.AREAS)
    assert sorted(set(ORDER) ^ set(steps)) == []            # web_ui.py runs them all, each in its place


def test_a_step_registered_is_one_the_text_reads():
    """areas.e2e_steps reads `r.step("...", fn[, setup=True])` from the modules' text: no step is written another way."""
    for p in sorted(areas.STEPS.glob("*.py")):
        calls = [n for n in ast.walk(ast.parse(p.read_text())) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "step"]
        named = {c.args[0].value for c in calls}
        assert named == {s for s, (m, _) in areas.e2e_steps().items() if m == p.stem}, p.name


def test_every_step_an_area_names_exists():
    steps = areas.e2e_steps()
    missing = [(a, s) for a, spec in areas.AREAS.items() for s in spec["e2e"] if s not in steps]
    assert missing == []


def test_every_source_glob_matches_a_file():
    root = FLUX.parent
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=root,
                            capture_output=True, text=True, check=True).stdout.split()
    files = [p[len("flux/"):] if p.startswith("flux/") else "../" + p for p in listed]
    dead = [(a, g) for a, spec in areas.AREAS.items() for g in spec["sources"] if not areas.matches_any(files, g)]
    dead += [("shared", g) for g in areas.SHARED if not areas.matches_any(files, g)]
    assert dead == []


def test_a_change_maps_to_its_areas():
    assert areas.areas_of("interfaces/web/src/flux_web/static/live/views.js") == ["live"]
    assert areas.areas_of("interfaces/web/src/flux_web/static/flux.css") is None           # shared: everything
    assert areas.areas_of("interfaces/web/src/flux_web/brand_new.py") is None              # in no area: everything
    assert areas.areas_of("../docs/decisions.md") == ["docs"]
    assert areas.areas_of("tests/unit/test_hidden.py") == ["hidden"]
    assert areas.areas_of("tests/e2e/steps/configurator.py") == ["configurator"]
    assert areas.areas_of("../fluxlet/main.py") == []                                    # another project's
