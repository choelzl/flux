"""An adapter package not registered in the evaluator registry (`flux_evaluator_abi.registry`,
D426) is unreachable from the CLI and task documents while looking complete from inside (D26).

Filesystem-driven on purpose: the check must notice a package nobody remembered to list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_FLUX_ROOT = Path(__file__).resolve().parents[2]
_EVALUATORS_DIR = _FLUX_ROOT / "evaluator"


@pytest.fixture(autouse=True)
def _application_evaluators():
    """Check the core registry without the test suite's optional application adapters."""


def _local_src_dirs() -> list[Path]:
    """Every `src` directory on the dev shell's PYTHONPATH (`localSrcDirs` in flake.nix, as tests/unit/conftest.py reads it)."""
    import re

    block = re.search(r"localSrcDirs = \[(.*?)\];", (_FLUX_ROOT / "flake.nix").read_text(), re.S)
    assert block, "flake.nix has no localSrcDirs block"
    return [_FLUX_ROOT / d for d in re.findall(r'"([^"]+)"', block.group(1))]


def _implemented_adapter_dirs() -> dict[str, Path]:
    """Every importable `flux_evaluator_*` package, keyed by backend name.

    Found through `localSrcDirs`, in `evaluator/<name>/` or under an application (D428), so a
    moved adapter cannot escape this check.
    """
    found = {}
    for src in _local_src_dirs():
        for package in sorted(src.glob("flux_evaluator_*")):
            if package.name == "flux_evaluator_abi" or not package.is_dir():
                continue
            # keyed by package name, not directory name
            found[package.name.removeprefix("flux_evaluator_")] = package
    return found


def test_the_evaluators_directory_is_findable():
    """Guards the guard: a moved directory would make every assertion below vacuous."""
    assert _EVALUATORS_DIR.is_dir(), f"expected an evaluator/ directory at {_EVALUATORS_DIR}"
    implemented = _implemented_adapter_dirs()
    assert len(implemented) >= 2
    assert {"openroad", "rtl"} <= set(implemented)


def test_every_implemented_adapter_is_registered_in_the_cli_registry():
    from flux_evaluator_abi import available_evaluators

    registered = set(available_evaluators())
    implemented = set(_implemented_adapter_dirs())
    missing = implemented - registered
    assert not missing, (
        f"adapter packages with no registry entry: {sorted(missing)} — they are unreachable from "
        "`flux eval` and every task document until registered in "
        "evaluator/abi/src/flux_evaluator_abi/registry.py"
    )


def test_every_registered_backend_has_a_real_adapter_package():
    """A registration whose package was renamed or removed fails here, not when first called."""
    from flux_evaluator_abi import available_evaluators

    orphaned = set(available_evaluators()) - set(_implemented_adapter_dirs())
    assert not orphaned, f"registered backends with no adapter package: {sorted(orphaned)}"


@pytest.mark.parametrize("name", sorted(_implemented_adapter_dirs()))
def test_each_backend_name_maps_back_from_a_stored_evaluator_string(name):
    """No backend name is a prefix of another's, since `flux replay` resolves provenance by prefix; checked per backend."""
    from flux_evaluator_abi import evaluator_name_for

    assert evaluator_name_for(f"{name}@1.2.3") == name
