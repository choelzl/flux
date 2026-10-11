"""A Python design's function against a reference (D971): the golden test for Python, no script to write.

    fluxhelper cases DESIGN.py --call count_primes --reference ref.py:count_primes --random 200
    fluxhelper cases DESIGN.py --call f --cases cases.json
    # each mismatch on a line, then "N failing of M"; exit 0, 1, or 3 (the design did not load)

The cases are, in order: `--cases` (a JSON list: each an argument list, or {"args": [...],
"kwargs": {...}, "expected": ...}), the reference module's `CASES` (a list of argument tuples),
and `--random N` calls of the reference module's `inputs(rng)` (it returns an argument tuple;
`rng` is a seeded `random.Random`). A case without `expected` is compared with the reference
function's result. `--tol R` accepts floats within a relative R. A call that raises is a failure.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import sys
from pathlib import Path
from typing import Any


def catalog() -> list[dict]:
    """This helper's catalog entry (D971)."""
    from ._entries import N_FAILING, param
    return [{"id": "python-cases", "role": "check", "title": "Compare with a reference function",
             "what": "Calls the design's function on cases (a JSON table, the reference's CASES, random inputs from its "
                     "inputs(rng)) and compares each result with the reference function's or the expected value.",
             "run": "fluxhelper cases {artifact} --call {call} --reference {reference} --random {random}",
             "params": {"call": param("Function", "main"), "reference": param("Reference (file:function)", "{home}/reference.py"),
                        "random": param("Random cases", 100)},
             "needs": [], "pass": N_FAILING, "languages": ["python"], "kinds": ["python"], "short": "cases"}]


def _load(path: str, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"{path} is not a Python file")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _same(got: Any, want: Any, tol: float) -> bool:
    if isinstance(got, float) or isinstance(want, float):
        try:
            g, w = float(got), float(want)
        except (TypeError, ValueError):
            return False
        if math.isnan(g) or math.isnan(w):
            return math.isnan(g) and math.isnan(w)
        return g == w or abs(g - w) <= tol * max(abs(g), abs(w))
    if isinstance(got, (list, tuple)) and isinstance(want, (list, tuple)):
        return len(got) == len(want) and all(_same(a, b, tol) for a, b in zip(got, want))
    if isinstance(got, dict) and isinstance(want, dict):
        return got.keys() == want.keys() and all(_same(got[k], want[k], tol) for k in got)
    return got == want


def _short(value: Any, n: int = 120) -> str:
    text = repr(value)
    return text if len(text) <= n else text[:n] + "..."


def add_case_arguments(ap: argparse.ArgumentParser) -> None:
    """The case options `cases`, `ccases` and `accuracy` share (D971, D972)."""
    ap.add_argument("--call", required=True, help="the design's function")
    ap.add_argument("--reference", default="", help="FILE.py or FILE.py:function (default: the --call name)")
    ap.add_argument("--cases", default="", help="a JSON file of cases")
    ap.add_argument("--random", type=int, default=0, help="random cases from the reference's inputs(rng)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--show", type=int, default=8, help="mismatches printed")


def collect(a: argparse.Namespace) -> tuple[Any, list[tuple[tuple, dict, Any, bool]]] | int:
    """(the reference function or None, the cases), or an exit code with the reason on stderr."""
    ref_fn, ref = None, None
    if a.reference:
        path, _, name = a.reference.partition(":")
        try:
            ref = _load(path, "reference")
            ref_fn = getattr(ref, name or a.call)
        except Exception as exc:  # noqa: BLE001
            print(f"the reference {a.reference} does not load: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
    cases: list[tuple[tuple, dict, Any, bool]] = []          # (args, kwargs, expected, has expected)
    if a.cases:
        for c in json.loads(Path(a.cases).read_text()):
            if isinstance(c, dict):
                cases.append((tuple(c.get("args", ())), dict(c.get("kwargs", {})), c.get("expected"), "expected" in c))
            else:
                cases.append((tuple(c) if isinstance(c, (list, tuple)) else (c,), {}, None, False))
    for c in getattr(ref, "CASES", ()) or ():
        cases.append((tuple(c) if isinstance(c, (list, tuple)) else (c,), {}, None, False))
    if a.random:
        make = getattr(ref, "inputs", None)
        if not callable(make):
            print("--random needs the reference module's inputs(rng)", file=sys.stderr)
            return 2
        rng = random.Random(a.seed)
        cases += [(tuple(make(rng)), {}, None, False) for _ in range(a.random)]
    if not cases:
        print("no cases: give --cases, a reference with CASES, or --random with inputs(rng)", file=sys.stderr)
        return 2
    if ref_fn is None and not all(has for _, _, _, has in cases):
        print("a case without `expected` needs --reference", file=sys.stderr)
        return 2
    return ref_fn, cases


def compare(fn: Any, a: argparse.Namespace) -> int:
    """`fn` on every case against the reference or the expected value; prints and returns the exit code."""
    got_cases = collect(a)
    if isinstance(got_cases, int):
        return got_cases
    ref_fn, cases = got_cases
    failing = []
    for args, kwargs, expected, has in cases:
        want = expected if has else ref_fn(*args, **kwargs)
        call = f"{a.call}({', '.join([*map(_short, args), *(f'{k}={_short(v)}' for k, v in kwargs.items())])})"
        try:
            got = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            failing.append(f"FAIL {call}: raised {type(exc).__name__}: {exc}")
            continue
        if not _same(got, want, a.tol):
            failing.append(f"FAIL {call}: got {_short(got)}, expected {_short(want)}")
    shown = list(dict.fromkeys(failing))[:a.show]         # a repeated case once on screen, every time in the count
    print("\n".join(shown + [f"{len(failing)} failing of {len(cases)}"]))
    return 1 if failing else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxhelper cases", description=__doc__.split("\n\n")[0])
    ap.add_argument("artifact")
    add_case_arguments(ap)
    ap.add_argument("--tol", type=float, default=0.0, help="relative tolerance for floats")
    a = ap.parse_args(argv)
    try:
        fn = getattr(_load(a.artifact, "design"), a.call)
    except Exception as exc:  # noqa: BLE001 -- whatever the design does on import is its failure
        print(f"did not load: {type(exc).__name__}: {exc}")
        print("1 failing")
        return 3
    return compare(fn, a)


if __name__ == "__main__":
    raise SystemExit(main())
