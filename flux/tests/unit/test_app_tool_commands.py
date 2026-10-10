"""Application commands survive copying and no longer depend on global CLI entry points."""

from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

import pytest

from flux_cli.main import build_parser
from flux_loop import load_task
from flux_loop.golden_proto import golden_path

FLUX = Path(__file__).resolve().parents[2]


def _core_env():
    env = dict(os.environ)
    # conftest makes the applications importable for adapter tests. A copied app must
    # work without that help, just like an uploaded loop on another machine.
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in env.get("PYTHONPATH", "").split(os.pathsep)
        if not ("/applications/" in p and p.endswith("/tools")))
    return env


@pytest.mark.parametrize("command", ["rtl", "champsim", "example"])
def test_removed_domain_and_example_commands_are_not_cli_entry_points(command):
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args([command])
    assert exc.value.code == 2
    assert command not in parser._subparsers._group_actions[0].choices


@pytest.mark.parametrize("name", ["adder16", "mul8", "macarray", "nlu", "prefetcher"])
def test_copied_application_calls_its_own_tool_scripts(name, tmp_path):
    home = tmp_path / "copied-loop"
    shutil.copytree(FLUX / "applications" / name, home,
                    ignore=shutil.ignore_patterns("out", "workbench", "runs", "__pycache__"))
    for doc in home.glob("*.yaml"):
        if doc.name != "problem.yaml" and not doc.name.endswith(".problem.yaml"):
            continue
        task = load_task(doc)
        for _, argv in task.commands():
            assert "flux_cli.main" not in argv
            for word in argv:
                if not task.subtasks and word.startswith("{home}/") and word.endswith(".py"):
                    assert (home / word.removeprefix("{home}/")).is_file()
        for stage in task.stages:
            if stage.command and "{home}/rtl.py" in stage.command:
                assert stage.metrics == ("fmax_mhz", "area_um2", "power_w", "cell_count")
                assert stage.needs == ("yosys", "openroad")
        script = home / {"prefetcher": "champsim.py"}.get(name, "rtl.py")
        result = subprocess.run([sys.executable, str(script), "--help"], cwd=tmp_path,
                                env=_core_env(), capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    if name == "nlu":
        for child in home.glob("ops/*/problem.yaml"):
            task = load_task(child)
            assert task.language == "systemverilog"
            assert golden_path(task) == child.parent / "golden.py"
            assert (child.parent / "rtl.py").is_file()


def test_all_bundled_documents_avoid_removed_cli_commands():
    for doc in (FLUX / "applications").rglob("*.yaml"):
        assert "flux rtl" not in doc.read_text() and "flux champsim" not in doc.read_text(), doc


def test_core_imports_and_registry_do_not_depend_on_removed_tool_packages(tmp_path):
    code = """
import importlib.util, json
import flux_cli.main, flux_web.app, flux_loop.golden_proto
names = ['flux_evaluator_champsim', 'flux_evaluator_timeloop', 'flux_evaluator_zigzag', 'flux_codegen_rtl_harness',
         'flux_evaluator_openroad', 'flux_evaluator_rtl', 'flux_calibration', 'flux_redaction', 'flux_codegen_harness_spec']
print(json.dumps({'packages': {name: importlib.util.find_spec(name) is not None for name in names}}))
"""
    run = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=_core_env(),
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert not any(result["packages"].values()), result


def test_bundled_rtl_sources_stay_consistent():
    result = subprocess.run([sys.executable, str(FLUX / "scripts/sync-app-tools.py"), "--check"],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
