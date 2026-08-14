"""The interconnect demo, run as a PROGRAM, end to end, with real tools.

The unit contract next door covers flags and the empty-store path without touching a tool. This
one exists for the failures that only a real run produces: an import that resolves in the test
process but not from the script's own directory, a root derived by counting parents that breaks
when the file moves, a store schema created in the wrong order, a table that prints no rows.

Deliberately the SMALLEST real run: one scope, no model, ~1 minute. It is not measuring the
quality of anything — it is asserting the demo still works.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]
DEMO = FLUX_ROOT / "applications/interconnect/demo.py"

pytestmark = pytest.mark.skipif(
    any(shutil.which(t) is None for t in ("openroad", "yosys", "verilator")),
    reason="needs the physical shell: nix develop .#physical",
)


@pytest.fixture(scope="module")
def completed(tmp_path_factory):
    """One run, shared by every assertion below: it costs a minute of real Yosys and OpenROAD."""
    db = tmp_path_factory.mktemp("demo") / "smoke.db"
    proc = subprocess.run(
        [sys.executable, str(DEMO), "--db", str(db), "--rounds", "1", "--llm-round", "0"],
        capture_output=True, text=True, timeout=1800, cwd=str(FLUX_ROOT))
    assert proc.returncode == 0, f"demo exited {proc.returncode}\n{proc.stdout[-3000:]}"
    return proc.stdout


def test_it_reaches_a_result_table_with_rows_in_it(completed):
    """A demo that runs to completion and measures nothing looks identical to one that worked,
    which is exactly the failure D288 records. So the assertion is on ROWS, not on exit code."""
    assert "MEASURED by interconnect_phys" in completed
    assert "SMALLEST that meets timing:" in completed
    measured = [ln for ln in completed.splitlines() if "mm2" in ln and "0.0" in ln]
    assert measured, f"no measured fabric in the output:\n{completed[-2000:]}"


def test_rounds_one_takes_exactly_one_step(completed):
    """`--rounds 1` regressed to a no-op once and nothing noticed; four scopes ran where one was
    asked for."""
    assert "over 1 step(s)" in completed
    assert completed.count("--- orchestrator:") == 1


def test_it_says_what_it_did_not_explore(completed):
    """A study that looked at less has to say so. One scope of six means five unexplored, named."""
    assert "COVERAGE: 1 of 6 scopes enumerated" in completed
    assert "NOT explored:" in completed


def test_it_reports_what_it_cost(completed):
    """Timing is part of reading a result: a frontier from cached lookups and one from an hour of
    place-and-route are not the same claim (D295)."""
    assert "WHERE THE TIME WENT" in completed
    assert "ELAPSED (wall clock)" in completed
    assert "tool:" in completed, "no tool time recorded, so nothing real ran"


def test_a_fresh_store_is_populated_by_the_run(completed):
    """The first run on a clean machine: every fabric it counts must be one it tried itself."""
    line = next(ln for ln in completed.splitlines() if ln.startswith("FABRICS ATTEMPTED:"))
    total = int(line.split()[2])
    assert total > 10
    assert f"({total} first tried by this run" in line
