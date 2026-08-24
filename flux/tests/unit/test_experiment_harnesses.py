"""The experiment harnesses in `experiments/` have to survive refactors of the code they drive.

They are the one class of code here that CI never executes: each costs an hour of real
place-and-route or a few hundred local-model rounds, so they are scheduled by hand and may sit
untouched for months. That is exactly how `escalation_speedup.py` came to accumulate three
independent breaks without anyone noticing -- it called `run_round` with the pre-D308 arity, called
a `check_toolchain` that had moved into the ABI as `require_tools`, and imported the demo from a
`demos/` directory the D296 reorganisation had deleted. It would have died on its first line of
real work, and it had never had one.

So this checks statically what running them would check dynamically: that every import resolves and
every call into the demo binds against the signature the demo actually has today. It cannot tell
you a harness produces a meaningful number. It can tell you the harness still fits its sockets,
which is the failure that was actually happening.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = sorted(p for p in (FLUX_ROOT / "experiments").glob("*.py")
                     if not p.name.startswith("_"))

# What the harnesses put on sys.path for themselves at runtime, done up-front so their
# function-local imports resolve here too.
# `applications/interconnect` is gone from this list: the study is an importable package now
# (D346) and only the harnesses themselves still need a path.
for _extra in ("tests/integration", "experiments"):
    sys.path.insert(0, str(FLUX_ROOT / _extra))


def _ids(paths):
    return [p.name for p in paths]


assert EXPERIMENTS, "no experiment harnesses found -- has experiments/ moved?"


@pytest.mark.parametrize("path", EXPERIMENTS, ids=_ids(EXPERIMENTS))
def test_the_harness_imports(path):
    """Module scope only. Importing must not run the experiment -- every one of these is
    guarded by `if __name__ == '__main__'`, and a harness that did real work on import could
    not be checked by anything, including this."""
    importlib.import_module(path.stem)


@pytest.mark.parametrize("path", EXPERIMENTS, ids=_ids(EXPERIMENTS))
def test_every_import_resolves_including_the_deferred_ones(path):
    """Harnesses import inside functions to keep module import cheap, which also hides a stale
    module name from every check short of running the thing. `from interconnect_dse import
    fabrics_attempted` sat in a function body across a reorganisation that deleted both the
    module and the name."""
    tree = ast.parse(path.read_text())
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module] if node.module and not node.level else []
        else:
            continue
        for name in names:
            try:
                if importlib.util.find_spec(name) is None:
                    missing.append(f"{name} (line {node.lineno})")
            except (ImportError, ValueError) as exc:
                missing.append(f"{name} (line {node.lineno}): {exc}")
    assert not missing, f"{path.name} imports modules that do not exist: {missing}"


@pytest.mark.parametrize("path", EXPERIMENTS, ids=_ids(EXPERIMENTS))
def test_calls_into_the_demo_bind_against_its_real_signature(path):
    """The break that motivated this file: `run_round(db, 1, max_stages, breadth, None)` against a
    `run_round(db, round_no, family, budget)` that had lost a parameter when nested
    depth/breadth scopes became flat families (D308). Arity is checked here rather than trusted,
    because the harness only finds out an hour into a run."""
    demo = importlib.import_module("flux_interconnect.flow")
    tree = ast.parse(path.read_text())
    problems = []
    for node in ast.walk(tree):
        call = node
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
            continue
        target = call.func.value
        if not (isinstance(target, ast.Name) and target.id == "demo"):
            continue
        attr = call.func.attr
        func = getattr(demo, attr, None)
        if func is None:
            problems.append(f"demo.{attr} does not exist (line {call.lineno})")
            continue
        if not callable(func):
            continue
        if any(isinstance(a, ast.Starred) for a in call.args) or \
                any(k.arg is None for k in call.keywords):
            continue  # unpacked call: arity is not statically knowable
        try:
            inspect.signature(func).bind(
                *([object()] * len(call.args)),
                **{k.arg: object() for k in call.keywords})
        except TypeError as exc:
            problems.append(f"demo.{attr} (line {call.lineno}): {exc}")
    assert not problems, f"{path.name} calls the demo wrongly: {problems}"


def test_the_family_the_speedup_experiment_asks_for_is_a_real_one():
    """A family name is a string handed to the campaign, so a stale one fails as an empty
    enumeration rather than a crash -- an experiment measuring nothing, reporting a speedup."""
    demo = importlib.import_module("flux_interconnect.flow")
    speedup = importlib.import_module("escalation_speedup")
    assert speedup.FAMILY in demo.SCOPE_KEYS, (
        f"escalation_speedup asks for the {speedup.FAMILY!r} family, but the demo offers "
        f"{demo.SCOPE_KEYS}")


@pytest.mark.parametrize("path", EXPERIMENTS, ids=_ids(EXPERIMENTS))
def test_the_directories_a_harness_puts_on_sys_path_exist(path):
    """Checked separately, and it has to be: this file puts those same directories on `sys.path`
    itself so that function-local imports resolve, which means the import checks above pass
    whether or not the harness's own insert points anywhere real. `demos/` had been deleted for a
    whole reorganisation and every other check here stayed green."""
    tree = ast.parse(path.read_text())
    missing = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "insert" and ast.unparse(node.func.value) == "sys.path"):
            continue
        for literal in [n.value for n in ast.walk(node)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str)]:
            if not (FLUX_ROOT / literal).is_dir():
                missing.append(f"{literal!r} (line {node.lineno})")
    assert not missing, (
        f"{path.name} adds directories to sys.path that do not exist: {missing}")
