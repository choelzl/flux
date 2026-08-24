"""Shared helpers for the integration suite (D246): one definition each of the Ollama probe
and the ONNX builders, so copies cannot drift.

A plain importable module (`import _helpers`), not a conftest, so guards read at module top level.
"""

from __future__ import annotations

import os

import pytest


def ollama_up() -> bool:
    """True when an Ollama server answers. Honors OLLAMA_HOST, falling back to the local default."""
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


