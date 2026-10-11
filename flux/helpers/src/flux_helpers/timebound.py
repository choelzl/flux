"""A time budget as a check (D973): a design slower than `--max-ms` on the given input fails, before
the costly benchmark runs.

    fluxhelper timebound DESIGN.py --call f --args "(1000000,)" --max-ms 500
    # "0 failing", or "FAIL f(...) took 812 ms, over 500 ms" and "1 failing"; exit 0, 1, or 3 (did not load)

The call runs in a child process that is stopped at `--max-ms` times `--grace` (default 2), so an
endless design fails rather than hangs. The fastest of `--runs` timed calls (after `--warmup`) is
judged: one slow run on a busy machine does not fail a fast design.
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys

_CHILD = r"""
import importlib.util, json, sys, time
path, call, args, runs, warmup = sys.argv[1], sys.argv[2], json.loads(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
try:
    spec = importlib.util.spec_from_file_location("design", path); mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod); fn = getattr(mod, call)
except Exception as exc:
    print(json.dumps({"load": f"{type(exc).__name__}: {exc}"})); sys.exit(0)
pos, kw = (args, {}) if isinstance(args, list) else ([], args)
best = None
for i in range(warmup + runs):
    t = time.perf_counter(); fn(*pos, **kw); ms = (time.perf_counter() - t) * 1000
    if i >= warmup:
        best = ms if best is None else min(best, ms)
        print(json.dumps({"ms": ms}), flush=True)
print(json.dumps({"best": best}))
"""


def catalog() -> list[dict]:
    """This helper's catalog entry (D973)."""
    from ._entries import N_FAILING, param
    return [{"id": "python-timebound", "role": "check", "title": "Within a time budget",
             "what": "Times one call of the design's function on a large input and fails a design slower than the budget, "
                     "before the costly benchmark.",
             "run": "fluxhelper timebound {artifact} --call {call} --args {args} --max-ms {max_ms}",
             "params": {"call": param("Function", "main"), "args": param("Arguments (a Python literal)", "()"),
                        "max_ms": param("Budget", 1000, "ms")},
             "needs": [], "pass": N_FAILING, "languages": ["python"], "kinds": ["python"], "short": "timebound"}]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxhelper timebound", description=__doc__.split("\n\n")[0])
    ap.add_argument("artifact")
    ap.add_argument("--call", required=True)
    ap.add_argument("--args", default="()", help='a Python literal: "(1000,)" or "{\'n\': 1000}"')
    ap.add_argument("--max-ms", type=float, required=True)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--warmup", type=int, default=0)
    ap.add_argument("--grace", type=float, default=2.0, help="the child is stopped at max-ms times this")
    a = ap.parse_args(argv)
    given = ast.literal_eval(a.args)
    args = given if isinstance(given, dict) else list(given) if isinstance(given, (tuple, list)) else [given]
    limit = a.max_ms * a.grace / 1000 * (a.runs + a.warmup) + 5
    call = f"{a.call}{tuple(args) if isinstance(args, list) else args}"
    try:
        r = subprocess.run([sys.executable, "-c", _CHILD, a.artifact, a.call, json.dumps(args), str(max(1, a.runs)),
                            str(max(0, a.warmup))], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=limit)
    except subprocess.TimeoutExpired:
        print(f"FAIL {call} did not finish within {limit:.0f} s, over {a.max_ms:g} ms")
        print("1 failing")
        return 1
    said = [json.loads(ln) for ln in r.stdout.splitlines() if ln.startswith("{")]
    if said and "load" in said[0]:
        print(f"did not load: {said[0]['load']}")
        print("1 failing")
        return 3
    best = next((x["best"] for x in said if "best" in x), None)
    if r.returncode != 0 or best is None:
        print(f"FAIL {call} raised: {(r.stderr.strip().splitlines() or ['?'])[-1]}")
        print("1 failing")
        return 1
    if best > a.max_ms:
        print(f"FAIL {call} took {best:.4g} ms, over {a.max_ms:g} ms")
        print("1 failing")
        return 1
    print(f"{a.call} took {best:.4g} ms of {a.max_ms:g} ms")
    print("0 failing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
