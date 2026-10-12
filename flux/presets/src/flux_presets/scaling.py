"""How a design's time grows with the problem size (D975): any command, timed at growing sizes.

    fluxpreset scaling -- ./prog {n}
    fluxpreset scaling --build "cc -O2 -o {out} {artifact}" --input "python3 gen.py {n}" -- {out}
    fluxpreset scaling --sizes 1000,2000,4000,8000 -- python3 design.py {n}
    # scaling_exponent= scaling_r2= time_at_max_ms= max_n= points=
    fluxpreset scaling --max-exponent 1.5 -- ./prog {n}      # a check: "N failing"

`{n}` in the command (and in `--input`) is the size. Without `--sizes`, n starts at `--start` and
doubles until a run takes `--max-s`, or `--points` sizes ran. `--input` is a command whose output
is the run's stdin, made before the run and not timed. Each size's time is the fastest of `--runs`
(wall clock). The run at n=1 is the start-up cost: it is taken off every time, and a size whose
remaining time is under `--floor-ms` or under the start-up itself is left out of the fit. The exponent k is the least-squares
slope of log(time) against log(n), time ~ c*n^k: about 1 linear, 1.1 n log n, 2 quadratic -- n log n
and n are not told apart reliably. `scaling_r2` says how well the line fits; under 0.9 the
timings are noise. The sizes and times go to stderr.
"""

from __future__ import annotations

import argparse
import math
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ._run import command


def catalog() -> list[dict]:
    """This preset's catalog entries (D975)."""
    from ._entries import N_FAILING, metrics, param
    common = {"start": param("First size", 1000), "command": param("Run command ({n}: the size)", "{artifact} {n}")}
    return [{"id": "scaling", "role": "stage", "title": "How time grows with size",
             "what": "Times the program at doubling problem sizes and fits time ~ n^k: the exponent says how it scales "
                     "(1 linear, 2 quadratic), the constant factor aside.",
             "run": "fluxpreset scaling --start {start} -- {command}",
             "params": common,
             "metrics": metrics("scaling_exponent", "scaling_r2", "time_at_max_ms", "max_n"),
             "needs": [], "languages": [], "kinds": ["own"], "short": "scaling"},
            {"id": "scaling-limit", "role": "check", "title": "Scales no worse than n^k",
             "what": "Times the program at doubling sizes and fails when time grows faster than n to the given power.",
             "run": "fluxpreset scaling --start {start} --max-exponent {max_exponent} -- {command}",
             "params": {**common, "max_exponent": param("Highest exponent", 1.5)},
             "needs": [], "pass": N_FAILING, "languages": [], "kinds": ["own"], "short": "scaling"}]


class _Failed(Exception):
    pass


def _fill(words: list[str], n: int, out: str) -> list[str]:
    return [w.replace("{n}", str(n)).replace("{out}", out) for w in words]


def _timed(cmd: list[str], n: int, out: str, make: list[str], runs: int, timeout: float, cwd: str) -> float:
    """The fastest of `runs` wall-clock times of the command at size n, in seconds."""
    stdin = None
    if make:
        g = subprocess.run(_fill(make, n, out), capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, cwd=cwd)
        if g.returncode != 0:
            raise _Failed(f"--input at n={n} exited {g.returncode}: {g.stderr.decode(errors='replace').strip()[-300:]}")
        stdin = g.stdout
    best = math.inf
    for _ in range(max(1, runs)):
        t = time.perf_counter()
        try:
            r = subprocess.run(_fill(cmd, n, out), input=stdin if stdin is not None else b"", stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, timeout=timeout, cwd=cwd)
        except subprocess.TimeoutExpired:
            raise _Failed(f"n={n} ran over {timeout:g} s") from None
        took = time.perf_counter() - t
        if r.returncode != 0:
            raise _Failed(f"n={n} exited {r.returncode}: {r.stderr.decode(errors='replace').strip()[-300:]}")
        best = min(best, took)
    return best


def fit(points: list[tuple[int, float]]) -> tuple[float, float]:
    """(k, r2) of log(t) = k*log(n) + c by least squares."""
    xs, ys = [math.log(n) for n, _ in points], [math.log(t) for _, t in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    k = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    ss = sum((y - my) ** 2 for y in ys)
    res = sum((y - (my + k * (x - mx))) ** 2 for x, y in zip(xs, ys))
    return k, (1 - res / ss) if ss else 1.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fluxpreset scaling", description=__doc__.split("\n\n")[0])
    ap.add_argument("--sizes", default="", help="the sizes, comma-separated (default: doubling from --start)")
    ap.add_argument("--start", type=int, default=1000)
    ap.add_argument("--points", type=int, default=8, help="the most sizes when doubling")
    ap.add_argument("--max-s", type=float, default=2.0, help="stop doubling once a run takes this long")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--floor-ms", type=float, default=5.0, help="sizes faster than this (or than start-up) after start-up are not fitted")
    ap.add_argument("--input", default="", help="a command ({n}) whose output is the run's stdin")
    ap.add_argument("--build", default="", help="build first; {out} is the program")
    ap.add_argument("--max-exponent", type=float, default=0.0, help="a check: fail when the exponent is over this")
    ap.add_argument("--timeout", type=float, default=600.0, help="the most one run may take")
    ap.add_argument("rest", nargs=argparse.REMAINDER, help="-- the run command, with {n}")
    a = ap.parse_args(argv)
    if not a.rest or a.rest == ["--"]:
        ap.error("give the run command after --, with {n} for the size")
    cmd = command(a.rest)
    if not any("{n}" in w for w in cmd) and not (a.input and "{n}" in a.input):
        ap.error("the command (or --input) needs {n}, the size")
    sizes = [int(x) for x in a.sizes.split(",") if x.strip()]
    checking = a.max_exponent > 0

    def failed(why: str) -> int:
        if checking:
            print(f"FAIL {why}\n1 failing")
        else:
            print(why, file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="flux-scaling-", ignore_cleanup_errors=True) as d:
        out = str(Path(d) / "prog")
        if a.build:
            b = subprocess.run(shlex.split(a.build.replace("{out}", shlex.quote(out))),
                               capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=a.timeout)
            if b.returncode != 0:
                print("did not build: " + "\n".join((b.stderr or b.stdout).strip().splitlines()[:20]))
                print("1 failing")
                return 3
        make = shlex.split(a.input) if a.input else []
        try:
            try:
                startup = _timed(cmd, 1, out, make, a.runs, a.timeout, d)
            except _Failed:
                startup = 0.0                       # n=1 is no valid size for this program: nothing taken off
            measured: list[tuple[int, float]] = []
            todo = list(sizes) or [a.start]
            while todo:
                n = todo.pop(0)
                t = _timed(cmd, n, out, make, a.runs, a.timeout, d)
                measured.append((n, t))
                print(f"n={n} ms={t * 1000:.3f}", file=sys.stderr)
                if not sizes and t < a.max_s and len(measured) < a.points:
                    todo.append(n * 2)
        except (_Failed, subprocess.TimeoutExpired) as exc:
            return failed(str(exc))
    floor = max(a.floor_ms / 1000, startup)               # start-up's own noise is about start-up's size
    points = [(n, t - startup) for n, t in measured if t - startup >= floor]
    print(f"start-up {startup * 1000:.3f} ms taken off; {len(points)} of {len(measured)} sizes fitted", file=sys.stderr)
    if len(points) < 3:
        return failed(f"only {len(points)} sizes ran over {floor * 1000:.0f} ms after start-up "
                      f"(the larger of --floor-ms and the start-up itself); give larger sizes "
                      "(--start, --sizes) or a lower --floor-ms")
    k, r2 = fit(points)
    if checking:
        bad = k > a.max_exponent
        print((f"FAIL time grows as n^{k:.2f} (r2 {r2:.3f}), over n^{a.max_exponent:g}\n" if bad else "") +
              f"{int(bad)} failing")
        return 1 if bad else 0
    print(f"scaling_exponent={k:.3f} scaling_r2={r2:.4f} time_at_max_ms={measured[-1][1] * 1000:.3f} "
          f"max_n={measured[-1][0]} points={len(points)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
