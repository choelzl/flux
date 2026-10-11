"""Flux's helpers keep one interface (D968, D970): each module has `main(argv)` and `catalog()`, its
entries run as `fluxhelper NAME ...`, `--help` works, a wrong argument is exit 2, and a document's
`fluxhelper ...` runs with the interpreter running Flux, read by the loader like any catalog tool."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys

import pytest

KEYS = {"id", "role", "title", "what", "run", "params", "needs", "languages", "kinds", "short"}


def _helpers() -> dict[str, list[dict]]:
    from flux_helpers import catalog

    out: dict[str, list[dict]] = {}
    for e in catalog():
        out.setdefault(e["helper_module"], []).append(e)
    return out


def _fluxhelper(*args: str, stdin=subprocess.DEVNULL) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "flux_helpers", *args], capture_output=True, text=True, timeout=60, stdin=stdin)


def test_the_helpers_are_the_modules_that_say_their_entries():
    helpers = _helpers()
    assert {"golden", "rtl", "prog", "bench", "champsim"} <= set(helpers)
    for name, entries in helpers.items():
        mod = importlib.import_module(f"flux_helpers.{name}")
        assert callable(getattr(mod, "main", None)), f"{name}: main(argv)"
        for e in entries:
            assert KEYS <= set(e), (name, e["id"], KEYS - set(e))
            assert e["run"].split()[:2] == ["fluxhelper", name], (name, e["run"])
            assert e["role"] in ("check", "stage") and (("pass" in e) == (e["role"] == "check")) and (("metrics" in e) == (e["role"] == "stage"))
            assert all({"label", "default", "unit"} <= set(p) for p in e["params"].values()), e["id"]
            assert all("{" + k + "}" in e["run"] for k in e["params"]), (e["id"], "every param is in the command")


@pytest.mark.parametrize("name", sorted(_helpers()))
def test_every_helper_answers_help_and_refuses_wrong_arguments_with_2(name):
    ok = _fluxhelper(name, "--help")
    assert ok.returncode == 0 and ok.stdout.strip(), ok.stderr
    bad = _fluxhelper(name, "--no-such-option")
    assert bad.returncode == 2, (bad.returncode, bad.stderr)


def test_fluxhelper_lists_describes_and_refuses_an_unknown_helper():
    listed = _fluxhelper()
    assert listed.returncode == 0 and "rtl" in listed.stdout and "golden" in listed.stdout
    described = json.loads(_fluxhelper("describe", "rtl").stdout)
    assert {e["id"] for e in described} == {"rtl-lint", "rtl-stat", "rtl-synth", "rtl-place", "rtl-route"}
    assert _fluxhelper("nope").returncode == 2 and _fluxhelper("describe", "nope").returncode == 2


def test_the_bench_helper_keeps_the_exit_codes(tmp_path):
    good = tmp_path / "d.py"
    good.write_text("def f(n):\n    return sum(range(n))\n")
    r = _fluxhelper("bench", str(good), "--call", "f", "--args", "(1000,)", "--runs", "3")
    assert r.returncode == 0 and r.stdout.startswith("time_ms=") and "runs=3" in r.stdout, r.stderr
    (tmp_path / "broken.py").write_text("def f(:\n")
    assert _fluxhelper("bench", str(tmp_path / "broken.py"), "--call", "f").returncode == 3, "did not load"
    (tmp_path / "raises.py").write_text("def f():\n    raise ValueError('no')\n")
    r = _fluxhelper("bench", str(tmp_path / "raises.py"), "--call", "f")
    assert r.returncode == 1 and "ValueError" in r.stderr and "ValueError" not in r.stdout


def test_a_documents_fluxhelper_runs_with_flux_and_reads_like_a_catalog_tool():
    from flux_loop import TaskSpec
    from flux_loop.document import _flux_program_tools

    task = TaskSpec.from_dict({"id": "t", "statement": "s", "flow": {
        "test": "fluxhelper golden {artifact} --golden {home}/golden.py",
        "measure": {"synth": {"command": "fluxhelper rtl measure {artifact} --stage synth --clock-ps 1000",
                              "metrics": ["fmax_mhz"]}}}})
    assert task.gate.named("test").run[:6] == ("{python}", "-W", "ignore", "-m", "flux_helpers", "golden")
    assert task.language == "systemverilog", "a helper's entry says its language"
    assert _flux_program_tools(("{python}", "-W", "ignore", "-m", "flux_helpers", "prog", "count", "--build", "x")) == ["valgrind"]
    assert _flux_program_tools(("{python}", "-m", "flux_helpers.prog", "size", "--build", "x")) == ["size"]
