"""A PTO-style spec's AVS tests, from the spec kept in the application's folder (D1019).

    fluxpreset asl tests TADD --spec {home}/spec          # the unit's tests: id, kind, summary
    fluxpreset asl run TADD --spec {home}/spec            # run them with ASLRef; then "N failing of M"
    fluxpreset asl run TADD --spec {home}/spec --out results.json   # each test's result, as JSON

A unit is named by its id or mnemonic (`PTO-TILE-TADD`, `TADD`). The spec is a checkout of the
spec's repository (its `asl/`, `tests/` and `scripts/` folders). `run` uses the spec's own runner,
`scripts/run-asl-test --root SPEC --id TEST`, which assembles the whole model and runs ASLRef on
it; ASLRef is the `aslref` on PATH, or `--aslref`. The runner writes its build in a temporary
folder that mirrors the spec, never in the spec or the application's folder.
"""

from __future__ import annotations

import argparse
import json
import os
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
    spec = Path(a.spec)
    units = load(spec)
    if not units:
        print(f"no spec units in {spec} (a spec checkout: its asl/ folder with PTO-INSTRUCTION headers)", file=sys.stderr)
        return None, None
    unit = find(units, a.unit)
    if unit is None:
        print(f"no unit {a.unit!r} in {spec}; the units: {', '.join(sorted(u.mnemonic for u in units.values())[:40])}",
              file=sys.stderr)
        return units, None
    return units, unit


def _mirror(spec: Path, into: Path) -> Path:
    """A folder that reads as the spec (each top-level entry linked) with a build/ of its own."""
    root = into / "spec"
    root.mkdir()
    for entry in spec.resolve().iterdir():
        if entry.name not in ("build", ".cache"):
            (root / entry.name).symlink_to(entry)
    return root


def _run(a: argparse.Namespace) -> int:
    units, unit = _spec(a)
    if unit is None:
        return 2
    runner = Path(a.spec) / "scripts" / "run-asl-test"
    if not runner.is_file():
        print(f"{runner} is missing: `run` needs a whole checkout of the spec's repository", file=sys.stderr)
        return 2
    aslref = a.aslref or os.environ.get("ASLREF") or shutil.which("aslref")
    if not aslref:
        print("ASLRef is not available: put `aslref` on PATH or give --aslref", file=sys.stderr)
        return 2
    if not unit.tests:
        print(f"{unit.mnemonic} has no AVS tests in {a.spec}/tests", file=sys.stderr)
        print("0 failing of 0")
        return 0
    results, failed = [], 0
    with tempfile.TemporaryDirectory(prefix="flux-asl-", ignore_cleanup_errors=True) as d:
        root = _mirror(Path(a.spec), Path(d))
        for t in unit.tests:
            try:
                done = subprocess.run([sys.executable, str(root / "scripts" / "run-asl-test"), "--root", str(root), "--id", t.id],
                                      cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                      timeout=a.timeout, env={**os.environ, "ASLREF": aslref})
                ok, said = done.returncode == 0, (done.stderr or done.stdout).strip().splitlines()[-1:] or [""]
            except subprocess.TimeoutExpired:
                ok, said = False, [f"timed out after {a.timeout:g} s"]
            results.append({"id": t.id, "kind": t.kind, "requirements": t.requirements, "passed": ok,
                            **({} if ok else {"said": said[0][:300]})})
            if not ok:
                failed += 1
                print(f"{t.id}: {said[0][:300]}")
    if a.out:
        Path(a.out).write_text(json.dumps({"unit": unit.id, "tests": results}, indent=1))
    print(f"{failed} failing of {len(results)}")
    return 1 if failed else 0


def _tests(a: argparse.Namespace) -> int:
    _units, unit = _spec(a)
    if unit is None:
        return 2
    for t in unit.tests:
        print(f"{t.id}  [{t.kind}]  {t.summary}")
    print(f"tests={len(unit.tests)}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset asl", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helped in (("tests", _tests, "list the unit's AVS tests"), ("run", _run, "run them with ASLRef")):
        p = sub.add_parser(name, help=helped)
        p.add_argument("unit", help="the unit's id or mnemonic (PTO-TILE-TADD, TADD)")
        p.add_argument("--spec", default="spec", help="the spec checkout (default: ./spec)")
        p.set_defaults(fn=fn)
        if name == "run":
            p.add_argument("--aslref", default="", help="the ASLRef program (default: $ASLREF, else `aslref` on PATH)")
            p.add_argument("--out", default="", help="write each test's result here, as JSON")
            p.add_argument("--timeout", type=float, default=600.0, help="seconds per test")
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
