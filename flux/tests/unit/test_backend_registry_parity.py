"""The evaluator-registry half of the same convention `test_mcp_surface_parity.py` guards
(docs/decisions.md D119): an adapter package that exists but is not registered in
the evaluator registry (`flux_evaluator_abi.registry`, D426; `flux_cli.registry` is its facade) is unreachable from the CLI, from every CHIA node, and from every MCP tool —
while looking complete from the inside.

This is not hypothetical. [D26] records exactly this: Booksim2's adapter was real and correct, and
still unreachable from any CHIA node until the registration was added. That bug is invisible to
every test that exercises an adapter directly, because none of them go through the registry.

Filesystem-driven on purpose: the check has to notice a package nobody remembered to mention, so
it cannot take its list of packages from anything a person maintains by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_FLUX_ROOT = Path(__file__).resolve().parents[2]
_EVALUATORS_DIR = _FLUX_ROOT / "evaluator"


def _local_src_dirs() -> list[Path]:
    """Every `src` directory the dev shell puts on PYTHONPATH (`localSrcDirs` in flake.nix,
    the same list tests/unit/conftest.py reads): the one authoritative inventory of
    packages, wherever their directory sits."""
    import re

    block = re.search(r"localSrcDirs = \[(.*?)\];", (_FLUX_ROOT / "flake.nix").read_text(), re.S)
    assert block, "flake.nix has no localSrcDirs block"
    return [_FLUX_ROOT / d for d in re.findall(r'"([^"]+)"', block.group(1))]


def _implemented_adapter_dirs() -> dict[str, Path]:
    """Every `flux_evaluator_*` package the dev shell can import, keyed by backend name.

    Two homes, by the dependency rule of D428: `evaluator/<name>/` for a backend more than
    one application uses, and `applications/<app>/...` for an evaluator that reads one
    application's own code (`interconnect_struct`, `interconnect_phys`, `champsim_bingo`).
    Both are found through `localSrcDirs`, so a moved adapter cannot escape this check --
    a package the shell cannot import is not an adapter at all. Directories under
    `evaluator/` with no `src/` are deliberate placeholders (`cimloop`, `hammer`,
    `sparseloop`) -- a placeholder is not a missing registration.
    """
    found = {}
    for src in _local_src_dirs():
        for package in sorted(src.glob("flux_evaluator_*")):
            if package.name == "flux_evaluator_abi" or not package.is_dir():
                continue
            # Keyed by PACKAGE name, not directory name: `flux_evaluator_interconnect_phys` is the
            # backend `interconnect_phys` wherever its directory sits.
            found[package.name.removeprefix("flux_evaluator_")] = package
    return found


def test_the_evaluators_directory_is_findable():
    """Guards the guard: a moved directory would otherwise turn every assertion below into a
    vacuous pass over an empty set."""
    assert _EVALUATORS_DIR.is_dir(), f"expected an evaluator/ directory at {_EVALUATORS_DIR}"
    implemented = _implemented_adapter_dirs()
    assert len(implemented) >= 16
    assert {"interconnect_struct", "interconnect_phys", "champsim_bingo"} <= set(implemented)
    assert all("applications/" in str(implemented[n]) for n in
               ("interconnect_struct", "interconnect_phys", "champsim_bingo"))


def test_every_implemented_adapter_is_registered_in_the_cli_registry():
    from flux_cli.registry import available_backends

    registered = set(available_backends())
    implemented = set(_implemented_adapter_dirs())
    missing = implemented - registered
    assert not missing, (
        f"adapter packages with no registry entry: {sorted(missing)} — they are unreachable from "
        "`flux eval`, every CHIA node and every MCP tool until registered in "
        "interfaces/cli/src/flux_cli/registry.py"
    )


def test_every_registered_backend_has_a_real_adapter_package():
    """The other direction: a registration whose package was renamed or removed fails only when
    someone actually calls that backend, which may be much later."""
    from flux_cli.registry import available_backends

    orphaned = set(available_backends()) - set(_implemented_adapter_dirs())
    assert not orphaned, f"registered backends with no adapter package: {sorted(orphaned)}"


@pytest.mark.parametrize("name", sorted(_implemented_adapter_dirs()))
def test_each_backend_name_maps_back_from_a_stored_evaluator_string(name):
    """`flux replay` resolves a stored `provenance.evaluator` (e.g. `'zigzag@3.8.5'`) back to a
    backend by prefix match. A backend whose name is a prefix of another's would resolve to
    whichever the dict happens to yield first — checked per backend so the failure names the
    culprit rather than the pair."""
    from flux_cli.registry import backend_for_evaluator_string

    assert backend_for_evaluator_string(f"{name}@1.2.3") == name
