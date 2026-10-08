"""nixchip bin-directory exports and explicit executable overrides remain runnable."""

from __future__ import annotations

import subprocess
import sys

import pytest

from flux_evaluator_openroad.adapter import OpenRoadEvaluator


@pytest.mark.parametrize("tool", ["yosys", "openroad"])
@pytest.mark.parametrize("form", ["default", "directory", "executable", "command"])
def test_tool_override_runs_the_selected_executable(tmp_path, monkeypatch, tool, form):
    monkeypatch.delenv("YOSYS_BIN", raising=False)
    monkeypatch.delenv("OPENROAD_BIN", raising=False)
    folder = tmp_path / "tool bin with spaces"
    folder.mkdir()
    name = tool if form in ("default", "directory") else "custom-" + tool
    binary = folder / name
    binary.write_text(f"#!{sys.executable}\nprint('selected {tool}')\n")
    binary.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder))
    monkeypatch.chdir(tmp_path)
    (tmp_path / tool).mkdir()  # a project folder must not shadow the default PATH command
    if form != "default":
        value = {"directory": str(folder), "executable": str(binary), "command": name}[form]
        monkeypatch.setenv(tool.upper() + "_BIN", value)
    evaluator = OpenRoadEvaluator()
    command = getattr(evaluator, tool + "_bin")
    result = subprocess.run([command], text=True, capture_output=True, check=True)
    assert result.stdout.strip() == f"selected {tool}"


@pytest.mark.parametrize("tool", ["yosys", "openroad"])
def test_missing_explicit_override_does_not_fall_back_to_path(tmp_path, monkeypatch, tool):
    folder = tmp_path / "bin"
    folder.mkdir()
    fallback = folder / tool
    fallback.write_text(f"#!{sys.executable}\nraise AssertionError('wrong tool executed')\n")
    fallback.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder))
    monkeypatch.setenv(tool.upper() + "_BIN", str(tmp_path / "missing-tool"))
    command = getattr(OpenRoadEvaluator(), tool + "_bin")
    with pytest.raises(FileNotFoundError):
        subprocess.run([command], check=True)
