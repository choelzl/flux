"""A performance gate (D973): any measurement run again and compared with a recorded baseline.

    fluxhelper regress --baseline base.json --tol 5 -- fluxhelper bench design.py --call f --args "(10**6,)"
    fluxhelper regress --baseline base.json --record -- CMD ...     # writes the baseline from this run
    # each metric worse than the baseline by more than --tol percent a line, then "N failing"

The command after `--` prints `name=value` (any helper or script); the baseline is a JSON object
{name: value}. Lower is better unless the metric is named in `--higher`. Only the baseline's metrics
are judged (or those `--metrics` names); one the run does not print is a failure. The run's numbers
go to stderr.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from ._run import command, numbers, run


def catalog() -> list[dict]:
    """This helper's catalog entry (D973)."""
    from ._entries import N_FAILING, param
    return [{"id": "regress", "role": "check", "title": "No worse than a baseline",
             "what": "Runs any measurement again and fails when a metric is worse than the recorded baseline by more than "
                     "the tolerance.",
             "run": "fluxhelper regress --baseline {baseline} --tol {tol} -- {command}",
             "params": {"baseline": param("Baseline (JSON)", "{home}/baseline.json"), "tol": param("Tolerance", 5, "%"),
                        "command": param("Measurement command", "")},
             "needs": [], "pass": N_FAILING, "languages": [], "kinds": ["own"], "short": "regress"}]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxhelper regress", description=__doc__.split("\n\n")[0])
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--tol", type=float, default=5.0, help="percent worse that still passes")
    ap.add_argument("--higher", default="", help="metrics where higher is better, comma-separated")
    ap.add_argument("--metrics", default="", help="only these metrics, comma-separated (default: all the run prints)")
    ap.add_argument("--record", action="store_true", help="write the baseline from this run")
    ap.add_argument("--timeout", type=float, default=3600.0)
    ap.add_argument("rest", nargs=argparse.REMAINDER, help="-- the measurement command")
    a = ap.parse_args(argv)
    if not a.rest or a.rest == ["--"]:
        ap.error("give the measurement command after --")
    r = run(command(a.rest), a.timeout)
    got = numbers(r.stdout)
    keep = {x.strip() for x in a.metrics.split(",") if x.strip()}
    got = {k: v for k, v in got.items() if not keep or k in keep}
    print(" ".join(f"{k}={v:g}" for k, v in got.items()) or "(no numbers)", file=sys.stderr)
    if r.returncode != 0 or not got:
        print(f"FAIL the measurement did not run (exit {r.returncode}): {(r.stderr or r.stdout).strip()[-300:]}")
        print("1 failing")
        return 3 if r.returncode == 3 else 1
    path = Path(a.baseline)
    if a.record:
        path.write_text(json.dumps(got, indent=1) + "\n")
        print("0 failing")
        return 0
    if not path.is_file():
        print(f"no baseline at {path}: record one with --record", file=sys.stderr)
        return 2
    base, higher = json.loads(path.read_text()), {x.strip() for x in a.higher.split(",") if x.strip()}
    base = {k: v for k, v in base.items() if not keep or k in keep}
    failing = []
    for name, ref in base.items():
        if name not in got:
            failing.append(f"FAIL {name}: not printed by the run")
            continue
        now, ref = got[name], float(ref)
        worse = (ref - now) if name in higher else (now - ref)
        limit = abs(ref) * a.tol / 100
        if math.isnan(now) or worse > limit:
            pct = 100 * worse / abs(ref) if ref else math.inf
            failing.append(f"FAIL {name}: {now:g} against the baseline {ref:g}, {pct:.3g}% worse (tolerance {a.tol:g}%)")
    print("\n".join(failing + [f"{len(failing)} failing"]))
    return 1 if failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
