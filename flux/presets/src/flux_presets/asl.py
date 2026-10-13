"""A PTO-style spec's AVS tests, from the spec kept in the application's folder (D1019).

    fluxpreset asl tests TADD --spec {home}/spec          # the unit's tests: id, kind, summary
    fluxpreset asl run TADD --spec {home}/spec            # run them with ASLRef; then "N failing of M"
    fluxpreset asl run TADD --spec {home}/spec --out results.json   # each test's result, as JSON
    fluxpreset asl run TADD TSUB --spec {home}/spec --jobs 8        # several units' tests at once

A unit is named by its id or mnemonic (`PTO-TILE-TADD`, `TADD`). The spec is a checkout of the
spec's repository (its `asl/`, `tests/` and `scripts/` folders). `run` uses the spec's own runner,
`scripts/run-asl-test --root SPEC --id TEST`, which assembles the whole model and runs ASLRef on
it; ASLRef is the `aslref` on PATH, or `--aslref`. The runner writes its build in a temporary
folder that mirrors the spec, never in the spec or the application's folder.

D1024: a spec with the page runner (`scripts/asl_release_suite.py`) has its tests run the way its
own release suite runs them -- the model assembled once, the tests on `--jobs` threads (the
machine's cores): about half the time of a test is assembling the model, so a unit's ten tests
take ~ one test's time with ten cores. Without it, one `run-asl-test` per test, in turn.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ._pto import find, load


def catalog() -> list[dict]:
    """This preset's catalog entries (D1019)."""
    from ._entries import N_FAILING, param
    return [{"id": "asl-avs", "role": "check", "title": "The spec's AVS tests pass",
             "what": "Runs a PTO-style spec unit's AVS tests with ASLRef, through the spec's own runner, from the spec "
                     "kept in the application's folder. Each failing test a line, then the count.",
             "run": "fluxpreset asl run {unit} --spec {home}/spec",
             "params": {"unit": param("Unit (id or mnemonic)", "TADD")},
             "needs": ["aslref"], "pass": N_FAILING, "languages": [], "kinds": [], "short": "avs"}]


def _spec(a: argparse.Namespace):
    """(the units, the ones named) -- several for `run` (D1024), one for `tests`."""
    spec = Path(a.spec)
    units = load(spec)
    if not units:
        print(f"no spec units in {spec} (a spec checkout: its asl/ folder with PTO-INSTRUCTION headers)", file=sys.stderr)
        return None, None
    picked = []
    for name in a.unit:
        unit = find(units, name)
        if unit is None:
            print(f"no unit {name!r} in {spec}; the units: {', '.join(sorted(u.mnemonic for u in units.values())[:40])}",
                  file=sys.stderr)
            return units, None
        picked.append(unit)
    return units, picked


def _mirror(spec: Path, into: Path) -> Path:
    """A folder that reads as the spec (each top-level entry linked) with a build/ of its own."""
    root = into / "spec"
    root.mkdir()
    for entry in spec.resolve().iterdir():
        if entry.name not in ("build", ".cache"):
            (root / entry.name).symlink_to(entry)
    return root


def _why(output: str) -> str:
    """The line that says why a test failed: ASLRef's error, else the last line with words in it
    (the runner can end on a bare `{` of its JSON)."""
    lines = [ln.strip() for ln in output.splitlines() if re.search(r"\w", ln)]
    said = next((ln for ln in lines if re.search(r"Assertion failed|[Dd]ynamic error|[Ss]tatic error|[Ee]rror:", ln)), "")
    return said or (lines[-1] if lines else "")


#: run inside the spec's mirror (D1024): the spec's own page runner, the model assembled once
_PAGE = r"""
import json, sys
from pathlib import Path
root, jobs, timeout, ids = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4:]
sys.path.insert(0, str(root))
from scripts.asl_tests import load_focused_test_points, matrix
from scripts.asl_release_suite import execute_matrix
execute_matrix(root, matrix(load_focused_test_points(root, ids)), jobs=jobs, timeout_seconds=timeout, output=sys.stderr)
"""


def _paged(root: Path, ids: list[str], a: argparse.Namespace, aslref: str) -> dict[str, tuple[bool, str]] | None:
    """Each test's (passed, why) through the spec's page runner, or None when the spec has none or it
    could not run (the caller runs them one by one)."""
    if not (root / "scripts" / "asl_release_suite.py").is_file():
        return None
    try:
        subprocess.run([sys.executable, "-c", _PAGE, str(root), str(a.jobs), str(int(a.timeout)), *ids], cwd=root,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=a.timeout * (len(ids) + 2),
                       env={**os.environ, "ASLREF": aslref})
    except subprocess.TimeoutExpired:
        return None
    out: dict[str, tuple[bool, str]] = {}
    for tid in ids:
        d = root / "build" / "asl-test-results" / tid
        try:
            doc = json.loads((d / "result.json").read_text())
        except (OSError, ValueError):
            return None
        log = (d / "aslref.log").read_text(errors="replace") if (d / "aslref.log").is_file() else ""
        ok = doc.get("status") == "passed"
        out[tid] = (ok, "" if ok else _why(log + "\n" + str(doc.get("error") or "")))
    return out


def _run(a: argparse.Namespace) -> int:
    units, picked = _spec(a)
    if picked is None:
        return 2
    unit = picked[0]
    runner = Path(a.spec) / "scripts" / "run-asl-test"
    if not runner.is_file():
        print(f"{runner} is missing: `run` needs a whole checkout of the spec's repository", file=sys.stderr)
        return 2
    aslref = a.aslref or os.environ.get("ASLREF") or shutil.which("aslref")
    if not aslref:
        print("ASLRef is not available: put `aslref` on PATH or give --aslref", file=sys.stderr)
        return 2
    tests = [t for u in picked for t in u.tests]
    if not tests:
        print(f"{', '.join(u.mnemonic for u in picked)}: no AVS tests in {a.spec}/tests", file=sys.stderr)
        print("0 failing of 0")
        return 0
    results, failed = [], 0
    with tempfile.TemporaryDirectory(prefix="flux-asl-", ignore_cleanup_errors=True) as d:
        root = _mirror(Path(a.spec), Path(d))
        paged = _paged(root, [t.id for t in tests], a, aslref) if a.jobs != 1 else None
        for t in tests:
            if paged is not None:
                ok, why = paged[t.id]
                said = [why]
            else:
                try:
                    done = subprocess.run([sys.executable, str(root / "scripts" / "run-asl-test"), "--root", str(root), "--id", t.id],
                                          cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                          timeout=a.timeout, env={**os.environ, "ASLREF": aslref})
                    ok, said = done.returncode == 0, [_why(done.stderr + "\n" + done.stdout)]
                except subprocess.TimeoutExpired:
                    ok, said = False, [f"timed out after {a.timeout:g} s"]
            results.append({"id": t.id, "kind": t.kind, "requirements": t.requirements, "passed": ok,
                            **({} if ok else {"said": said[0][:300]})})
            if not ok:
                failed += 1
                print(f"{t.id}: {said[0][:300]}")
    if a.out:
        Path(a.out).write_text(json.dumps({"unit": unit.id, "units": [u.id for u in picked], "tests": results}, indent=1))
    print(f"{failed} failing of {len(results)}")
    return 1 if failed else 0


def _tests(a: argparse.Namespace) -> int:
    _units, picked = _spec(a)
    if picked is None:
        return 2
    for t in (t for u in picked for t in u.tests):
        print(f"{t.id}  [{t.kind}]  {t.summary}")
    print(f"tests={sum(len(u.tests) for u in picked)}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset asl", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helped in (("tests", _tests, "list the unit's AVS tests"), ("run", _run, "run them with ASLRef")):
        p = sub.add_parser(name, help=helped)
        p.add_argument("unit", nargs="+", help="the unit's id or mnemonic (PTO-TILE-TADD, TADD); `run` takes several")
        p.add_argument("--spec", default="spec", help="the spec checkout (default: ./spec)")
        p.set_defaults(fn=fn)
        if name == "run":
            p.add_argument("--aslref", default="", help="the ASLRef program (default: $ASLREF, else `aslref` on PATH)")
            p.add_argument("--out", default="", help="write each test's result here, as JSON")
            p.add_argument("--timeout", type=float, default=600.0, help="seconds per test")
            p.add_argument("--jobs", type=int, default=os.cpu_count() or 1,
                           help="tests at once, the model assembled once (the spec's page runner); 1: one by one")
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
