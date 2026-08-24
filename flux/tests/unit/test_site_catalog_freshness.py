"""The documentation site's generated catalog pages, kept true mechanically.

`website/docs/catalog/*.md` is generated from the live MCP surface by
`website/generate_catalog.py` and committed, so the GitHub Pages build never needs this repo's
toolchain. Committed generated output rots the moment someone changes a docstring, a signature,
or the registered surface and forgets to regenerate — the same failure mode
`test_mcp_surface_parity.py` exists for, one artifact further out. This test regenerates the
pages in memory and diffs them against what is on disk, so drift fails the unit suite instead
of publishing stale pages.
"""

from __future__ import annotations

import difflib
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_WEBSITE = _REPO / "website"


def test_generated_catalog_pages_match_the_live_surface():
    sys.path.insert(0, str(_WEBSITE))
    try:
        import generate_catalog
    finally:
        sys.path.remove(str(_WEBSITE))

    stale: list[str] = []
    for path, expected in generate_catalog.generate().items():
        on_disk = path.read_text() if path.is_file() else ""
        if on_disk != expected:
            diff = "\n".join(difflib.unified_diff(
                on_disk.splitlines(), expected.splitlines(),
                fromfile=str(path.relative_to(_REPO)), tofile="regenerated", lineterm="", n=2,
            ))
            stale.append(diff)
    assert not stale, (
        "generated catalog pages are stale — regenerate with\n"
        "  cd flux && nix develop --command python3 ../website/generate_catalog.py\n\n"
        + "\n\n".join(stale)
    )
