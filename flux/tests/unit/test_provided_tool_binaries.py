"""The provided-binary lookups, in the suite that always runs (docs/decisions.md D146/D148).

Both adapters prefer a packaged tool and fall back to clone-and-build. That preference is
*structural* — env var precedence, and what counts as a usable installation — and D119's rule is
that structural guards belong here rather than only in live tests that need real backends.

The `dramsim3` case carries the real lesson: a binary without its `configs/` must be rejected
rather than accepted, because accepting a half-installation is exactly how the CACTI package
shipped something that built, installed, and then crashed on missing runtime data (D145).
"""

from __future__ import annotations

import pytest


def _cacti_lookup():
    from flux_evaluator_cacti.adapter import CactiEvaluator

    return CactiEvaluator._provided_cacti_binary


def _dramsim3_lookup():
    from flux_evaluator_dramsim3.adapter import DramSim3Evaluator

    return DramSim3Evaluator._provided_dramsim3


def test_cacti_finds_a_binary_via_the_env_var_directory(tmp_path, monkeypatch):
    """`CACTI_BIN` points at a *directory* in nixchip's convention (`PKGNAME_BIN`), so the lookup
    has to accept that as well as a direct path to the executable."""
    binary = tmp_path / "cacti"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("CACTI_BIN", str(tmp_path))

    assert _cacti_lookup()() == binary


def test_cacti_falls_through_when_nothing_is_provided(tmp_path, monkeypatch):
    """Falling through is what preserves the clone-and-build path for anyone without nixchip."""
    monkeypatch.delenv("CACTI_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))

    assert _cacti_lookup()() is None


def test_cacti_ignores_a_non_executable_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CACTI_BIN", str(tmp_path))
    (tmp_path / "cacti").write_text("not executable")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    assert _cacti_lookup()() is None


def test_dramsim3_requires_configs_not_just_a_binary(tmp_path, monkeypatch):
    """The D145 lesson as a test: a binary with no `configs/` is not a usable installation. This
    adapter selects a real `.ini` by name, so accepting one would fail later and further away."""
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    binary = prefix / "bin" / "dramsim3"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("DRAMSIM3_BIN", str(prefix / "bin"))
    monkeypatch.delenv("DRAMSIM3_CONFIGS", raising=False)

    assert _dramsim3_lookup()() is None, "a binary without configs must not be accepted"

    configs = prefix / "share" / "dramsim3" / "configs"
    configs.mkdir(parents=True)
    (configs / "DDR4_8Gb_x16_2400_2.ini").write_text("[dram_structure]\n")

    found = _dramsim3_lookup()()
    assert found is not None and found == (binary, configs)


def test_dramsim3_rejects_an_empty_configs_directory(tmp_path, monkeypatch):
    """An existing but empty `configs/` is the same failure wearing a directory: present, useless.
    Checked for real `.ini` files rather than for the directory existing."""
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    binary = prefix / "bin" / "dramsim3"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    (prefix / "share" / "dramsim3" / "configs").mkdir(parents=True)
    monkeypatch.setenv("DRAMSIM3_BIN", str(prefix / "bin"))
    monkeypatch.delenv("DRAMSIM3_CONFIGS", raising=False)

    assert _dramsim3_lookup()() is None
