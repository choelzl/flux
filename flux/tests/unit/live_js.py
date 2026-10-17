"""Live's modules under node (D1234): a copy of the page's static folder as ES modules, a script run in it."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

STATIC = Path(__file__).parents[2] / "interfaces/web/src/flux_web/static"
_COPY: Path | None = None


def folder() -> Path:
    """The static folder copied once per session, read as ES modules (its relative imports resolve)."""
    global _COPY
    if _COPY is None:
        _COPY = Path(tempfile.mkdtemp(prefix="live-js-"))
        shutil.copytree(STATIC, _COPY / "static", ignore=shutil.ignore_patterns("vendor"))
        shutil.copytree(STATIC / "vendor", _COPY / "static" / "vendor")
        (_COPY / "static" / "package.json").write_text('{"type": "module"}')
    return _COPY / "static"


def run(script: str, stdin: str = "") -> object:
    """`script` (an ES module, imports relative to the static folder: "./live/events.js") writes JSON to stdout."""
    f = folder() / f"run-{abs(hash(script))}.js"
    f.write_text(script)
    r = subprocess.run(["node", str(f)], input=stdin, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def module(path: str, expr: str) -> object:
    """`expr` (a JS function of the module at `path`, e.g. "live/views.js") applied; its answer."""
    return run(f"import * as m from {json.dumps('./' + path)};\nprocess.stdout.write(JSON.stringify(({expr})(m)));")
