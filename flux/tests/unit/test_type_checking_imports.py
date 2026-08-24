"""`if TYPE_CHECKING:` imports must name things that exist (D334).

A TYPE_CHECKING block never executes and pyflakes accepts the bound name, so a wrong import there is
invisible. The blocks are executed here, where the failure is loud.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _files_with_type_checking():
    out = []
    for path in FLUX_ROOT.rglob("*.py"):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        if "TYPE_CHECKING" in text:
            out.append(path)
    return sorted(out)


_FILES = _files_with_type_checking()
assert _FILES, "no TYPE_CHECKING blocks found — has the convention changed?"


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_every_type_checking_import_resolves(path):
    tree = ast.parse(path.read_text())
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        name = getattr(test, "id", None) or getattr(test, "attr", None)
        if name != "TYPE_CHECKING":
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.ImportFrom) and inner.module and not inner.level:
                try:
                    module = importlib.import_module(inner.module)
                except ImportError as exc:
                    missing.append(f"{inner.module} ({exc})")
                    continue
                for alias in inner.names:
                    if not hasattr(module, alias.name):
                        missing.append(f"{inner.module}.{alias.name}")
            elif isinstance(inner, ast.Import):
                for alias in inner.names:
                    try:
                        importlib.import_module(alias.name.split(".")[0])
                    except ImportError as exc:
                        missing.append(f"{alias.name} ({exc})")
    assert not missing, f"{path.name} has TYPE_CHECKING imports that do not resolve: {missing}"
