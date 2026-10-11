"""Any measurement run several times, its noise taken out (D975): each metric's median and spread.

    fluxpreset repeat --n 7 -- fluxpreset prog time --build "cc -O2 -o {out} {artifact}"
    # each metric's median as name=value, and name_spread_pct= (max - min) / median, in percent

The command after `--` prints `name=value`. It runs `--warmup` times unjudged, then `--n` times;
a run that exits non-zero, or a metric not printed every time, fails the measurement (exit 1).
Each run's numbers go to stderr.
"""

from __future__ import annotations

import argparse
import statistics
import sys

from ._run import command, numbers, run


def catalog() -> list[dict]:
    """This preset's catalog entry (D975)."""
    from ._entries import param
    return [{"id": "repeat", "role": "stage", "title": "Median of several runs",
             "what": "Runs any measurement several times and prints each metric's median and its spread, so one noisy "
                     "run does not decide.",
             "run": "fluxpreset repeat --n {n} -- {command}",
             "params": {"n": param("Runs", 5), "command": param("Measurement command", "")},
             "metrics": {}, "needs": [], "languages": [], "kinds": ["own"], "short": "repeat"}]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset repeat", description=__doc__.split("\n\n")[0])
    ap.add_argument("--n", type=int, default=5, help="judged runs")
    ap.add_argument("--warmup", type=int, default=0, help="runs before, not judged")
    ap.add_argument("--timeout", type=float, default=3600.0, help="the most one run may take")
    ap.add_argument("rest", nargs=argparse.REMAINDER, help="-- the measurement command")
    a = ap.parse_args(argv)
    if not a.rest or a.rest == ["--"]:
        ap.error("give the measurement command after --")
    cmd = command(a.rest)
    seen: list[dict[str, float]] = []
    for i in range(max(0, a.warmup) + max(1, a.n)):
        r = run(cmd, a.timeout)
        if r.returncode != 0:
            print(f"run {i + 1} exited {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}", file=sys.stderr)
            return 3 if r.returncode == 3 else 1
        got = numbers(r.stdout)
        print(f"run {i + 1}: " + (" ".join(f"{k}={v:g}" for k, v in got.items()) or "(no numbers)"), file=sys.stderr)
        if i >= a.warmup:
            seen.append(got)
    names = [k for k in seen[0] if all(k in s for s in seen)]
    missing = sorted({k for s in seen for k in s} - set(names))
    if missing or not names:
        print(f"not printed by every run: {', '.join(missing)}" if missing else "the command printed no numbers",
              file=sys.stderr)
        return 1
    out = []
    for k in names:
        vals = [s[k] for s in seen]
        mid = statistics.median(vals)
        spread = (max(vals) - min(vals)) / abs(mid) * 100 if mid else 0.0
        out += [f"{k}={mid:g}", f"{k}_spread_pct={spread:.3g}"]
    print(" ".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
