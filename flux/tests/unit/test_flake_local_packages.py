"""Every local `flux-*` package is on the dev shell's PYTHONPATH (D195).

`flake.nix`'s `localSrcDirs` makes each package importable without a pip install; a package
missing from it can still work locally and fail in CI. The package list is read from the
filesystem, so a package nobody listed is still noticed.
"""

from __future__ import annotations

import re
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]
FLAKE = FLUX_ROOT / "flake.nix"


def _listed_src_dirs() -> set[str]:
    block = re.search(r"localSrcDirs = \[(.*?)\];", FLAKE.read_text(), re.S)
    assert block is not None, "localSrcDirs block not found in flake.nix"
    return set(re.findall(r'"([^"]+/src)"', block.group(1)))


def _package_src_dirs() -> set[str]:
    """Every directory holding a `flux_*` Python package, at any nesting depth the repo uses
    (e.g. `core/ir/src/flux_ir`, `applications/interconnect/lib/src/flux_interconnect`)."""
    found = set()
    for pattern in ("*/src/flux_*", "*/*/src/flux_*", "*/*/*/src/flux_*"):
        for package in FLUX_ROOT.glob(pattern):
            if package.is_dir():
                found.add(str(package.parent.relative_to(FLUX_ROOT)))
    return found


def test_the_flake_and_the_filesystem_are_findable():
    """A moved flake or a failed glob would make every check below vacuous."""
    assert FLAKE.is_file()
    assert len(_listed_src_dirs()) >= 13
    assert len(_package_src_dirs()) >= 13


def test_every_local_package_is_on_the_dev_shell_pythonpath():
    missing = _package_src_dirs() - _listed_src_dirs()
    assert not missing, (
        f"packages absent from flake.nix's localSrcDirs: {sorted(missing)} — they are not "
        "importable in `nix develop`, so their tests pass locally and fail in CI"
    )


def test_every_listed_src_dir_still_holds_a_package():
    """No PYTHONPATH entry points at a removed or renamed package."""
    orphaned = _listed_src_dirs() - _package_src_dirs()
    assert not orphaned, f"localSrcDirs entries with no flux_* package: {sorted(orphaned)}"
