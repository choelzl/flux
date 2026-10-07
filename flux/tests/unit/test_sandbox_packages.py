"""Per-loop Nix packages: validated selections, pinned profiles and isolated launch environments."""

from __future__ import annotations

import json
import os
import types
from pathlib import Path

import pytest

from flux_cli import sandbox, sandbox_packages as packages


@pytest.mark.parametrize("raw", ['[]', 'null', '{"other": []}', '{"nixpkgs": "jq"}',
                                 '{"nixchip": [1]}', '{"nixpkgs": ["../flake"]}',
                                 '{"nixpkgs": ["jq; touch x"]}', '{"nixpkgs": ["github:owner/repo#pkg"]}'])
def test_package_selections_accept_attributes_only(raw):
    with pytest.raises(ValueError):
        packages.package_spec(raw)


def test_package_sources_and_nested_attributes_are_kept():
    assert packages.package_spec('{"nixpkgs": ["7zip", "python3Packages.numpy", "jq", "jq"], "nixchip": ["verilator"]}') == {
        "nixpkgs": ["7zip", "python3Packages.numpy", "jq"], "nixchip": ["verilator"],
    }
    with pytest.raises(ValueError):
        packages.package_spec(json.dumps({"nixpkgs": ["jq"] * 65}))


def test_profiles_are_cached_rooted_and_refreshed_when_the_lock_changes(tmp_path, monkeypatch):
    flake = tmp_path / "server-flake"
    flake.mkdir()
    (flake / "flake.nix").write_text("{}")
    (flake / "flake.lock").write_text("first lock")
    monkeypatch.setenv("FLUX_SANDBOX_NIX_FLAKE", str(flake))
    monkeypatch.setenv("FLUX_ROOT", str(tmp_path / "untrusted-loop-flake"))
    monkeypatch.setattr(packages.shutil, "which", lambda name: "/bin/nix")
    store = tmp_path / "package-output"
    store.mkdir()
    (store / "flux-package-env.json").write_text("{}")
    builds = []

    def build(cmd, **kwargs):
        builds.append(cmd)
        profile = Path(cmd[cmd.index("--out-link") + 1])
        profile.symlink_to(store, target_is_directory=True)

    monkeypatch.setattr(packages.subprocess, "run", build)
    spec = {"nixpkgs": ["jq"], "nixchip": ["verilator"]}
    assert packages.package_root(spec, tmp_path / "cache") == store
    assert packages.package_root(spec, tmp_path / "cache") == store
    assert len(builds) == 1
    assert builds[0][builds[0].index("flakePath") + 1] == str(flake)
    assert json.loads(builds[0][builds[0].index("packageSpec") + 1]) == spec
    (flake / "flake.lock").write_text("updated lock")
    packages.package_root(spec, tmp_path / "cache")
    assert len(builds) == 2
    assert builds[0][builds[0].index("--out-link") + 1] != builds[1][builds[1].index("--out-link") + 1]


def _profile(tmp_path, monkeypatch):
    root = tmp_path / "extra-packages"
    for folder in ("bin", "include", "lib/pkgconfig"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "flux-package-env.json").write_text(json.dumps({"VERILATOR_HOME": str(root), "VERILATOR_BIN": None}))
    monkeypatch.setenv(packages.PACKAGE_ENV, '{"nixpkgs":["jq"],"nixchip":["verilator"]}')
    monkeypatch.setattr(packages, "package_root", lambda spec, cache: root)
    return root


def test_package_environment_is_restored_even_if_a_launch_fails(tmp_path, monkeypatch):
    root = _profile(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/original/native/libs")
    monkeypatch.setenv("CPATH", "/original/headers")
    monkeypatch.setenv("VERILATOR_BIN", "/old/bin/directory")
    monkeypatch.delenv("VERILATOR_HOME", raising=False)
    before = dict(os.environ)
    with pytest.raises(RuntimeError, match="a launch failed"):
        with packages.package_environment(tmp_path):
            assert os.environ["PATH"] == f"{root}/bin:/usr/bin"
            assert os.environ["CPATH"] == f"{root}/include:/original/headers"
            assert os.environ["PKG_CONFIG_PATH"].split(os.pathsep)[0] == f"{root}/lib/pkgconfig"
            assert os.environ["VERILATOR_HOME"] == str(root)
            assert "VERILATOR_BIN" not in os.environ
            assert os.environ["LD_LIBRARY_PATH"] == "/original/native/libs"
            raise RuntimeError("a launch failed")
    assert dict(os.environ) == before


def test_extra_tools_and_nixchip_paths_reach_the_container_only_for_this_launch(tmp_path, monkeypatch):
    root = _profile(tmp_path, monkeypatch)
    monkeypatch.setattr(sandbox, "_local", lambda: tmp_path / "sandbox-cache")
    monkeypatch.setattr(sandbox, "_engine_ok", lambda eng: "")
    monkeypatch.setenv("FLUX_SANDBOX_NET", "open")
    monkeypatch.setenv("FLUX_SANDBOX_ALLOW", "")
    doc = tmp_path / "problem.yaml"
    doc.write_text("statement: s\n")
    args = types.SimpleNamespace(file=str(doc), db=None, out=None, json=None)
    seen = []
    monkeypatch.setattr(sandbox.subprocess, "call", lambda cmd, **kwargs: seen.append(sandbox.container_env(cmd)) or 0)
    before = os.environ["PATH"]
    assert sandbox.launch(["task", "run", str(doc)], args, "task run") == 0
    assert seen[0]["PATH"].split(os.pathsep)[0] == str(root / "bin")
    assert seen[0]["VERILATOR_HOME"] == str(root)
    assert "VERILATOR_BIN" not in seen[0]
    assert packages.PACKAGE_ENV not in seen[0]
    assert "FLUX_SANDBOX_NIX_FLAKE" not in seen[0]
    assert os.environ["PATH"] == before
    monkeypatch.delenv(packages.PACKAGE_ENV)
    assert sandbox.launch(["task", "run", str(doc)], args, "task run") == 0
    assert str(root / "bin") not in seen[1]["PATH"].split(os.pathsep)


def test_a_package_error_is_reported_before_starting_a_container(tmp_path, monkeypatch, capsys):
    _profile(tmp_path, monkeypatch)
    def missing(spec, cache):
        raise RuntimeError("no package called missing-tool")
    monkeypatch.setattr(packages, "package_root", missing)
    monkeypatch.setattr(sandbox.subprocess, "call", lambda *a, **kw: pytest.fail("container must not start"))
    assert sandbox.launch([], types.SimpleNamespace(), "task run") == 2
    assert "cannot prepare sandbox packages: no package called missing-tool" in capsys.readouterr().err
