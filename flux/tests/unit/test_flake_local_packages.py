"""Every local `flux-*` package is on the dev shell's PYTHONPATH (docs/decisions.md D195).

`flake.nix`'s `localSrcDirs` is what makes each package importable without a pip install. A package
added to the tree but not to that list is invisible in the dev shell — importable in a developer's
own environment if they happen to have it on PYTHONPATH already, and missing in CI, which is the
worst version of the failure.

Filesystem-driven for the reason `test_backend_registry_parity.py` is: the check has to notice a
package nobody remembered to mention, so it cannot take its list from anything hand-maintained.

This file also exists because `flake.nix`'s own prose about the list was wrong twice — a stale
package count, and a claim that seven adapters were deliberately excluded when all seven were
listed. Prose drifts; this does not.
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
    """Every directory holding a `flux_*` Python package, at any nesting depth this repo uses:
    `core/ir/src/flux_ir` (two), `mentor/knowledge/mining/src/...` (three), and
    `applications/interconnect/lib/src/flux_interconnect` (three, the shape every future
    application takes)."""
    found = set()
    for pattern in ("*/src/flux_*", "*/*/src/flux_*", "*/*/*/src/flux_*"):
        for package in FLUX_ROOT.glob(pattern):
            if package.is_dir():
                found.add(str(package.parent.relative_to(FLUX_ROOT)))
    return found


def test_the_flake_and_the_filesystem_are_findable():
    """Guards the guard: a moved flake or a failed glob would make every assertion below vacuous."""
    assert FLAKE.is_file()
    assert len(_listed_src_dirs()) >= 30
    assert len(_package_src_dirs()) >= 30


def test_every_local_package_is_on_the_dev_shell_pythonpath():
    missing = _package_src_dirs() - _listed_src_dirs()
    assert not missing, (
        f"packages absent from flake.nix's localSrcDirs: {sorted(missing)} — they are not "
        "importable in `nix develop`, so their tests pass locally and fail in CI"
    )


def test_every_listed_src_dir_still_holds_a_package():
    """The other direction: a renamed or removed package leaves a dead PYTHONPATH entry, which is
    harmless at runtime and misleading to read."""
    orphaned = _listed_src_dirs() - _package_src_dirs()
    assert not orphaned, f"localSrcDirs entries with no flux_* package: {sorted(orphaned)}"
