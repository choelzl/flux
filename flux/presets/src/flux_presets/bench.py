"""Time a Python function of the design (D968): the measurement a Python loop usually writes itself.

    fluxpreset bench DESIGN.py --call count_primes --args "(1000000,)" --runs 5 --warmup 1
    # time_ms=<median> time_min_ms=<fastest> time_spread_ms=<slowest - fastest> runs=5

DESIGN.py is loaded as a module and `--call` is called with `--args` (a Python literal: a tuple of
positional arguments, or a dict of keyword arguments), `--warmup` times untimed, then `--runs`
times timed. A design that does not load exits 3 (did not build); one that raises exits 1. Run
it with `budget.workers: 1`: timings taken at once disturb each other.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import statistics
import sys
import time
import traceback


def catalog() -> list[dict]:
    """This preset's catalog entry (D968)."""
    from ._entries import metrics, param
    return [{"id": "python-bench", "role": "stage", "title": "Time a Python function",
             "what": "Loads the design, calls one function with your arguments, times it over several runs.",
             "run": "fluxpreset bench {artifact} --call {call} --args {args} --runs {runs}",
             "params": {"call": param("Function", "main"), "args": param("Arguments (a Python literal)", "()"),
                        "runs": param("Runs", 5)},
             "metrics": metrics("time_ms"), "needs": [], "languages": ["python"], "kinds": ["python"], "short": "bench"}]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset bench", description=__doc__.split("\n\n")[0])
    ap.add_argument("artifact")
    ap.add_argument("--call", required=True, help="the function to time")
    ap.add_argument("--args", default="()", help='its arguments, a Python literal: "(1000,)" or "{\'n\': 1000}"')
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    a = ap.parse_args(argv)
    try:
        spec = importlib.util.spec_from_file_location("design", a.artifact)
        design = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(design)
        fn = getattr(design, a.call)
    except Exception as exc:  # noqa: BLE001
        print(f"did not load: {type(exc).__name__}: {exc}")
        return 3
    given = ast.literal_eval(a.args)
    args, kwargs = ((), given) if isinstance(given, dict) else (tuple(given) if isinstance(given, (tuple, list)) else (given,), {})
    times = []
    try:
        for i in range(max(0, a.warmup) + max(1, a.runs)):
            t = time.perf_counter()
            fn(*args, **kwargs)
            if i >= a.warmup:
                times.append((time.perf_counter() - t) * 1000)
    except Exception:  # noqa: BLE001
        print(traceback.format_exc(limit=3).rstrip(), file=sys.stderr)
        print(f"{a.call} raised")
        return 1
    print(f"time_ms={statistics.median(times):.4g} time_min_ms={min(times):.4g} "
          f"time_spread_ms={max(times) - min(times):.4g} runs={len(times)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
