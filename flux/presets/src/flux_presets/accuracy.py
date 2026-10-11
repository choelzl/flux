"""How far an approximation is from its reference (D972): a measurement, not pass/fail.

    fluxpreset accuracy DESIGN.py --call exp_approx --reference ref.py:exp --random 1000
    # max_abs_error= mean_abs_error= max_rel_error= mean_rel_error= max_ulp= cases=

The design's function and the reference are called on the same cases as `fluxpreset cases` (a
JSON table, the reference's `CASES`, `--random N` of its `inputs(rng)`); each numeric result (a
number, or every number of a list or tuple) is compared. `max_ulp` counts float64 steps between
the two results. A call that raises, or a non-numeric result, exits 1; a design that does not
load exits 3.
"""

from __future__ import annotations

import argparse
import math
import statistics
import struct
import sys
from typing import Any

from .cases import _load, add_case_arguments, collect


def catalog() -> list[dict]:
    """This preset's catalog entry (D972)."""
    from ._entries import metrics, param
    return [{"id": "python-accuracy", "role": "stage", "title": "Accuracy against a reference",
             "what": "Calls the design's function and a reference on the same cases and reports how far apart they are: "
                     "absolute, relative and ULP error.",
             "run": "fluxpreset accuracy {artifact} --call {call} --reference {reference} --random {random}",
             "params": {"call": param("Function", "main"), "reference": param("Reference (file:function)", "{home}/reference.py"),
                        "random": param("Random cases", 1000)},
             "metrics": metrics("max_abs_error", "mean_abs_error", "max_rel_error", "mean_rel_error", "max_ulp"),
             "needs": [], "languages": ["python"], "kinds": ["python"], "short": "accuracy"}]


def _ordered(x: float) -> int:
    bits = struct.unpack("<q", struct.pack("<d", x))[0]
    return bits if bits >= 0 else -(bits & 0x7FFFFFFFFFFFFFFF)


def _numbers(v: Any) -> list[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float, list, tuple)):
        raise TypeError(f"a non-numeric result: {v!r}")
    return [float(v)] if isinstance(v, (int, float)) else [x for e in v for x in _numbers(e)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset accuracy", description=__doc__.split("\n\n")[0])
    ap.add_argument("artifact")
    add_case_arguments(ap)
    a = ap.parse_args(argv)
    try:
        fn = getattr(_load(a.artifact, "design"), a.call)
    except Exception as exc:  # noqa: BLE001
        print(f"did not load: {type(exc).__name__}: {exc}")
        return 3
    got_cases = collect(a)
    if isinstance(got_cases, int):
        return got_cases
    ref_fn, cases = got_cases
    abs_err, rel_err, ulps = [], [], []
    try:
        for args, kwargs, expected, has in cases:
            want = _numbers(expected if has else ref_fn(*args, **kwargs))
            got = _numbers(fn(*args, **kwargs))
            if len(got) != len(want):
                raise ValueError(f"{len(got)} numbers where the reference has {len(want)}")
            for g, w in zip(got, want):
                if math.isnan(g) or math.isnan(w):
                    if not (math.isnan(g) and math.isnan(w)):
                        abs_err.append(math.inf); rel_err.append(math.inf); ulps.append(math.inf)  # noqa: E702
                    continue
                abs_err.append(abs(g - w))
                rel_err.append(abs(g - w) / abs(w) if w else (0.0 if g == w else math.inf))
                ulps.append(abs(_ordered(g) - _ordered(w)) if math.isfinite(g) and math.isfinite(w) else (0 if g == w else math.inf))
    except Exception as exc:  # noqa: BLE001
        print(f"{a.call} could not be measured: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not abs_err:
        print("no numeric results to compare", file=sys.stderr)
        return 1
    print(f"max_abs_error={max(abs_err):.6g} mean_abs_error={statistics.fmean(abs_err):.6g} "
          f"max_rel_error={max(rel_err):.6g} mean_rel_error={statistics.fmean(rel_err):.6g} "
          f"max_ulp={max(ulps):.6g} cases={len(cases)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
