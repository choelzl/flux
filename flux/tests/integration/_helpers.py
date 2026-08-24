"""Shared helpers for the integration suite (docs/decisions.md D246) — the review cycle found
8 copies of `_ollama_up` (one already drifted to a different timeout, all blind to
`OLLAMA_HOST`), 3 copies of the wide-proj ONNX builder, and 2 byte-identical copies of the
chain builder that D239's "reproduces D237's pins" claim silently depended on staying
identical. One definition each; a drifted copy is now impossible rather than unlikely.

Not a conftest: plain importable module (`import _helpers`), so guards read at module top
level exactly like the local definitions they replace.
"""

from __future__ import annotations

import os

import pytest


def ollama_up() -> bool:
    """True when an Ollama server answers. Honors OLLAMA_HOST (falling back to the local
    default) — the 8 previous copies all hardcoded localhost, silently skipping for anyone
    running a remote server."""
    import urllib.request

    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if not host.startswith("http"):
        host = f"http://{host}"
    try:
        urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=3)
        return True
    except Exception:  # noqa: BLE001
        return False


# Module-level: `pytestmark = _helpers.requires_ollama`; per-test: `@_helpers.requires_ollama`.
requires_ollama = pytest.mark.skipif(
    not ollama_up(), reason="needs an Ollama server (OLLAMA_HOST or localhost:11434)"
)


