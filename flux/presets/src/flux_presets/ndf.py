"""A PTO-style spec's normative clauses, read with the NDF tool (`ndf`, from nixchip) from the spec
kept in the application's folder (D1020).

    fluxpreset ndf list --spec {home}/spec                 # each unit: its clauses by modality, its tests
    fluxpreset ndf clauses TADD --spec {home}/spec         # the unit's clauses, whole: what a design must do
    fluxpreset ndf check TADD --spec {home}/spec --results avs.json   # each MUST clause failed or uncovered,
                                                                       # then "N failing of M"

`ndf build` compiles the spec (its ndf.yaml, ndf.lock and the dependencies they pin, e.g. the
`tools/ndf` submodule) into an index in a temporary folder, each time; nothing is written in the
spec. A clause's modality is the strongest of MUST / SHOULD / MAY its text uses; a clause is
enforced when it says MUST and its status is active (or accepted). `check` reads the results
`fluxpreset asl run --out` writes: a clause is covered by the tests naming it, or else by the tests
naming its unit (`PTO-INST-...`); it passes when it is covered and every covering test passed.
`--uncovered warn` lists an uncovered clause without counting it.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ._pto import Unit, find, load

MODALITIES = ("must", "should", "may")
ENFORCED_STATUS = ("active", "accepted")


def catalog() -> list[dict]:
    """This preset's catalog entries (D1020)."""
    from ._entries import N_FAILING, param
    return [{"id": "ndf-contract", "role": "check", "title": "Every MUST clause covered and passing",
             "what": "Reads a PTO-style spec unit's NDF clauses (the NDF tool, from the spec in the application's folder) "
                     "and the AVS results `fluxpreset asl run --out` wrote: each MUST clause that failed or has no test "
                     "is a line, then the count.",
             "run": "fluxpreset ndf check {unit} --spec {home}/spec --results {results}",
             "params": {"unit": param("Unit (id or mnemonic)", "TADD"), "results": param("AVS results (JSON)", "avs.json")},
             "needs": ["ndf"], "pass": N_FAILING, "languages": [], "kinds": [], "short": "contract"}]


def modality(text: str) -> str:
    words = set(re.findall(r"\b(MUST|SHOULD|MAY)\b", text))
    return next((m for m in MODALITIES if m.upper() in words), "")


def _ndf(a: argparse.Namespace) -> str | None:
    found = a.ndf or shutil.which("ndf")
    if not found:
        print("the NDF tool is not available: `ndf` on PATH (nixchip's ndf, in Flux's dev shell) or --ndf", file=sys.stderr)
    return found


def clauses(ndf: str, spec: Path) -> list[dict] | str:
    """The spec's requirement clauses as `ndf query` gives them, or why they could not be read."""
    with tempfile.TemporaryDirectory(prefix="flux-ndf-", ignore_cleanup_errors=True) as d:
        index = Path(d) / "graph.sqlite"
        built = subprocess.run([ndf, "build", "--root", str(spec), "--output", str(index)], cwd=spec,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if not index.exists():
            try:
                said = "; ".join(x.get("message", "") for x in json.loads(built.stdout).get("diagnostics", []))
            except ValueError:
                said = (built.stderr or built.stdout).strip()
            if "NDF-DEP" in said:
                said += " (the spec's pinned dependencies: `git submodule update --init` in the spec)"
            return f"ndf build failed: {said[:600]}"
        try:
            for diag in json.loads(built.stdout).get("diagnostics", []):     # an index with warnings still reads
                print(f"ndf: {diag.get('code', '')} {diag.get('message', '')}"[:300], file=sys.stderr)
        except ValueError:
            pass
        got = subprocess.run([ndf, "query", "--index", str(index), "--expression", "kind=requirement"],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
    try:
        doc = json.loads(got.stdout)
    except ValueError:
        return f"ndf query failed: {(got.stderr or got.stdout).strip()[:600]}"
    if not doc.get("ok"):
        return "ndf query failed: " + "; ".join(x.get("message", "") for x in doc.get("diagnostics", []))[:600]
    return list(doc.get("data") or [])


def of_unit(found: list[dict], unit: Unit) -> list[dict]:
    return [c for c in found if (c.get("source") or {}).get("path") == unit.source]


def _short(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]


def _enforced(c: dict) -> bool:
    return modality(c.get("body") or "") == "must" and str(c.get("status") or "") in ENFORCED_STATUS


def _load(a: argparse.Namespace):
    spec = Path(a.spec)
    units = load(spec)
    if not units:
        print(f"no spec units in {spec} (a spec checkout: its asl/ folder with PTO-INSTRUCTION headers)", file=sys.stderr)
        return None
    ndf = _ndf(a)
    if not ndf:
        return None
    found = clauses(ndf, spec)
    if isinstance(found, str):
        print(found, file=sys.stderr)
        return None
    return units, found


def _list(a: argparse.Namespace) -> int:
    got = _load(a)
    if got is None:
        return 2
    units, found = got
    for u in sorted(units.values(), key=lambda u: u.source):
        if a.classification and a.classification not in u.classification:
            continue
        mine = of_unit(found, u)
        counts = {m: sum(1 for c in mine if modality(c.get("body") or "") == m) for m in MODALITIES}
        print(f"{u.mnemonic:<10} {u.id:<24} must={counts['must']} should={counts['should']} may={counts['may']} "
              f"tests={len(u.tests)}  {u.summary}")
    return 0


def _clauses(a: argparse.Namespace) -> int:
    got = _load(a)
    if got is None:
        return 2
    units, found = got
    unit = find(units, a.unit)
    if unit is None:
        print(f"no unit {a.unit!r} in {a.spec}", file=sys.stderr)
        return 2
    print(f"# {unit.mnemonic} ({unit.id}): {unit.summary}\n")
    for c in of_unit(found, unit):
        how = modality(c.get("body") or "") or "none"
        print(f"## {_short(c['id'])}  [{how}, {c.get('status')}{'' if _enforced(c) else ', not enforced'}]\n")
        print((c.get("body") or "").strip() + "\n")
    return 0


def _check(a: argparse.Namespace) -> int:
    got = _load(a)
    if got is None:
        return 2
    units, found = got
    unit = find(units, a.unit)
    if unit is None:
        print(f"no unit {a.unit!r} in {a.spec}", file=sys.stderr)
        return 2
    try:
        results = json.loads(Path(a.results).read_text())
    except (OSError, ValueError) as exc:
        print(f"no results to check: {a.results} ({exc}); `fluxpreset asl run {unit.mnemonic} --out FILE` writes them",
              file=sys.stderr)
        return 2
    tests = list(results.get("tests") or [])
    own = "PTO-INST-" + (unit.id[4:] if unit.id.startswith("PTO-") else unit.id)
    enforced = [c for c in of_unit(found, unit) if _enforced(c)]
    failing = 0
    for c in enforced:
        cid = _short(c["id"])
        covering = [t for t in tests if cid in (t.get("requirements") or [])] \
            or [t for t in tests if own in (t.get("requirements") or [])]
        if not covering:
            print(f"{cid}: uncovered (no test names it or {own})")
            failing += a.uncovered == "block"
        elif not all(t.get("passed") for t in covering):
            print(f"{cid}: failed ({', '.join(t['id'] for t in covering if not t.get('passed'))})")
            failing += 1
    print(f"{failing} failing of {len(enforced)}")
    return 1 if failing else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset ndf", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, helped in (("list", _list, "each unit, its clauses by modality and its tests"),
                             ("clauses", _clauses, "a unit's clauses, whole"),
                             ("check", _check, "each MUST clause failed or uncovered, then the count")):
        p = sub.add_parser(name, help=helped)
        if name != "list":
            p.add_argument("unit", help="the unit's id or mnemonic (PTO-TILE-TADD, TADD)")
        else:
            p.add_argument("--classification", default="", help="only units of this classification (elementwise-tile-tile)")
        p.add_argument("--spec", default="spec", help="the spec checkout (default: ./spec)")
        p.add_argument("--ndf", default="", help="the NDF tool (default: `ndf` on PATH)")
        if name == "check":
            p.add_argument("--results", required=True, help="the AVS results `fluxpreset asl run --out` wrote")
            p.add_argument("--uncovered", choices=("block", "warn"), default="block",
                           help="an enforced clause no test covers: counted (block) or only listed (warn)")
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
