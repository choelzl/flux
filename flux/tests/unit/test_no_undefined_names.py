"""No name is used that is never bound (docs/decisions.md D334).

THE CLASS THIS CATCHES, three times in one session, each crashing the demo on its first real step
with the entire suite green:

  * `_anneal` and `_population` deleted by an index-slice edit while still called (D321)
  * `repairable` computed after the action registry that reads it (D328)
  * `ask` initialised after the `--problem` block that needs it (D333)

All three lived inside `main()`, which nothing imports and no test calls — it places silicon. A
static check was hand-rolled twice and was wrong both times: the first missed a name assigned
later, the second produced fifty false positives on correct code. Getting it right needs
control-flow analysis, which is what a linter is.

`pyflakes` reports exactly one thing here, F821, and reports it correctly: with the `ask` bug
re-introduced it says "undefined name 'ask'" at the right line, and says nothing about the file
once it is fixed. This gates on that one check and ignores the rest of its output — unused
imports and f-strings without placeholders are style, and a gate that fails on style is a gate
someone turns off.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FLUX_ROOT = Path(__file__).resolve().parents[2]


def _tracked_python_files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "flux/**/*.py"], cwd=FLUX_ROOT.parent,
        capture_output=True, text=True, check=False)
    return [line[len("flux/"):] for line in listed.stdout.splitlines() if line.endswith(".py")]


def test_pyflakes_finds_no_undefined_names():
    files = _tracked_python_files()
    assert files, "no tracked Python files found — is this a git checkout?"
    run = subprocess.run([sys.executable, "-m", "pyflakes", *files],
                         cwd=FLUX_ROOT, capture_output=True, text=True, check=False)
    undefined = [line for line in run.stdout.splitlines()
                 if "undefined name" in line or "before assignment" in line]
    assert not undefined, (
        "pyflakes found names that are used and never bound:\n  " + "\n  ".join(undefined))


def test_pyflakes_is_actually_available():
    """A gate that silently stops running is worse than no gate. If pyflakes leaves the dev
    shell, this says so instead of passing vacuously."""
    run = subprocess.run([sys.executable, "-m", "pyflakes", "--version"],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, f"pyflakes is not runnable: {run.stderr.strip()[:200]}"
