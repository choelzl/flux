"""Your pytest file run against the design (D971).

    fluxpreset pytest DESIGN.py --tests test_design.py
    # each failing test on a line, then "N failing of M"; exit 0, 1, or 3 (the design did not load)

The design is copied as `design.py` beside a copy of the tests in a folder of its own, so the
tests say `import design` (or `from design import f`); `FLUX_DESIGN` names the original file.
A test that errors counts as failing. Needs pytest in the interpreter running Flux.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def catalog() -> list[dict]:
    """This preset's catalog entry (D971)."""
    from ._entries import N_FAILING, param
    return [{"id": "python-pytest", "role": "check", "title": "Run your pytest file",
             "what": "Runs your pytest file with the design importable as `design`; counts the failing tests.",
             "run": "fluxpreset pytest {artifact} --tests {tests}", "params": {"tests": param("Tests", "{home}/test_design.py")},
             "needs": [], "pass": N_FAILING, "languages": ["python"], "kinds": ["python"], "short": "pytest"}]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset pytest", description=__doc__.split("\n\n")[0])
    ap.add_argument("artifact")
    ap.add_argument("--tests", required=True, help="the pytest file (or folder)")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--show", type=int, default=8)
    a = ap.parse_args(argv)
    tests = Path(a.tests)
    if not tests.exists():
        print(f"no tests at {tests}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="flux-pytest-") as d:
        work = Path(d)
        shutil.copy(a.artifact, work / "design.py")
        target = work / tests.name
        (shutil.copytree if tests.is_dir() else shutil.copy)(tests, target)
        env = {**os.environ, "FLUX_DESIGN": str(Path(a.artifact).resolve()),
               "PYTHONPATH": os.pathsep.join([d, os.environ.get("PYTHONPATH", "")]), "PYTHONDONTWRITEBYTECODE": "1"}
        loads = subprocess.run([sys.executable, "-c", "import design"], cwd=work, env=env, capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=a.timeout)
        if loads.returncode != 0:
            print("did not load: " + ((loads.stderr.strip().splitlines() or ["import design failed"])[-1]))
            print("1 failing")
            return 3
        report = work / "report.xml"
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}",
                            "--rootdir", d, str(target)], cwd=work, env=env, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=a.timeout)
        if not report.is_file():
            print(f"pytest did not run (exit {r.returncode}): {(r.stdout + r.stderr).strip()[-600:]}", file=sys.stderr)
            return 2
        cases = list(ET.parse(report).getroot().iter("testcase"))
    failing = []
    for case in cases:
        bad = case.find("failure") if case.find("failure") is not None else case.find("error")
        if bad is not None:
            why = (bad.get("message") or "").splitlines()[0] if bad.get("message") else bad.tag
            failing.append(f"FAIL {case.get('classname', '')}::{case.get('name', '')}: {why[:200]}")
    if not cases:
        print(f"no test ran: {(r.stdout + r.stderr).strip()[-400:]}", file=sys.stderr)
        print("1 failing")
        return 1
    print("\n".join(failing[:a.show] + [f"{len(failing)} failing of {len(cases)}"]))
    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
