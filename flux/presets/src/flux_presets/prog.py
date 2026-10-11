"""`fluxpreset prog time|count|size` (D661, D968; `flux prog` before D969): a program's
speed, instruction and cache counts, peak memory and size, as stages a document names. Each prints `name=value` lines.

    measure:
      count: {command: 'fluxpreset prog count --build "c++ -O2 -o {out} {artifact}"',
              metrics: [instructions]}
      time:  {command: 'fluxpreset prog time --build "c++ -O2 -o {out} {artifact}" --runs 10',
              metrics: [time_ms]}

`--build` is one shell-quoted command run in a fresh directory; `{out}` in it is the program it
must write there (`<dir>/prog`). `--run` is the command measured, `{out}` again the built program;
empty, it is the built program alone. A Python artifact needs no build:
`--run "{python} {artifact}"`. A build that fails prints the compiler's first lines and exits 3
(did not build, as the golden test's); a run that fails exits 1.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

__all__ = ["add_parsers", "cmd_prog_count", "cmd_prog_memory", "cmd_prog_size", "cmd_prog_time", "main"]


def catalog() -> list[dict]:
    """This preset's catalog entries (D968): a compiled program's size, time and counts. Their
    `needs` are inferred by the loader when a stage does not say them (`infer_needs`)."""
    from ._entries import metrics, param
    build = {"build": param("Build command ({out}: the program)", "c++ -O2 -o {out} {artifact}"),
             "run": param("Run command (empty: the built program)", "")}
    langs, kinds = ["c", "cpp", "python"], ["program", "python"]
    prog = 'fluxpreset prog {sub} --build "{{build}}" --run "{{run}}"{more}'
    return [{"id": "prog-size", "role": "stage", "title": "Program size",
             "what": "Builds the program and reads its sections with `size`: text, data and bss bytes.",
             "run": 'fluxpreset prog size --build "{build}"', "params": {"build": build["build"]},
             "metrics": metrics("text_bytes", "data_bytes", "bss_bytes"), "needs": ["size"], "infer_needs": True,
             "languages": langs, "kinds": kinds, "short": "size"},
            {"id": "prog-time", "role": "stage", "title": "Program run time",
             "what": "Builds the program and times it over several runs (hyperfine when present).",
             "run": prog.format(sub="time", more=" --runs {runs} --warmup {warmup}"),
             "params": {**build, "runs": param("Runs", 10), "warmup": param("Warm-up runs", 1)},
             "metrics": metrics("time_ms", "time_ms_stddev", "time_ms_min"), "needs": [], "languages": langs, "kinds": kinds,
             "short": "time"},
            {"id": "prog-count", "role": "stage", "title": "Instruction count (Valgrind)",
             "what": "Builds the program and counts instructions and cache misses with Valgrind's cachegrind: deterministic.",
             "run": prog.format(sub="count", more=""), "params": build,
             "metrics": metrics("instructions", "d1_misses", "ll_misses", "branch_mispredicts"), "needs": ["valgrind"],
             "infer_needs": True, "languages": langs, "kinds": kinds, "short": "count"},
            {"id": "prog-memory", "role": "stage", "title": "Program peak memory",
             "what": "Builds the program and runs it: its peak resident memory as the kernel counts it, the most over the runs.",
             "run": prog.format(sub="memory", more=" --runs {runs}"), "params": {**build, "runs": param("Runs", 3)},
             "metrics": metrics("peak_rss_kb"), "needs": [], "languages": langs, "kinds": kinds, "short": "memory"}]

#: exit code for "did not build" (D594)
_DID_NOT_BUILD = 3


class _Failed(Exception):
    def __init__(self, text: str, code: int) -> None:
        super().__init__(text)
        self.code = code


def _argv(cmd: str, out: Path) -> list[str]:
    return shlex.split(cmd.replace("{out}", shlex.quote(str(out))))


def _prepare(args: argparse.Namespace, scratch: Path) -> list[str]:
    """Build (when `--build` says how) and return the argv to measure."""
    out = scratch / "prog"
    if (args.build or "").strip():
        r = subprocess.run(_argv(args.build, out), cwd=scratch, capture_output=True, text=True,
                           timeout=float(args.timeout))
        if r.returncode != 0 or not out.exists():
            text = (r.stderr or r.stdout or "").strip().splitlines()
            why = "\n".join(text[:20]) or f"the build wrote no {out.name}"
            raise _Failed(f"did not build: {why}\n1 failing", _DID_NOT_BUILD)
    elif getattr(args, "binary", None):
        return [args.binary]                # `size BINARY`: a program already built
    run = (args.run or "").strip() or "{out}"
    if "{out}" in run and not out.exists():
        raise _Failed("nothing to run: say --build (which writes {out}) or --run", 2)
    return _argv(run, out)


def _measured(fn):
    """Run `fn(args, argv)` on the prepared program; print its metrics, one `name=value` a line."""
    def cmd(args: argparse.Namespace) -> int:
        with tempfile.TemporaryDirectory(prefix="flux-prog-") as td:
            try:
                got = fn(args, _prepare(args, Path(td)), Path(td))
            except _Failed as exc:
                print(exc)
                return exc.code
        for k, v in got.items():
            print(f"{k}={v:.6g}" if isinstance(v, float) else f"{k}={v}")
        return 0
    return cmd


def _time(args: argparse.Namespace, argv: list[str], scratch: Path) -> dict[str, float | int]:
    runs, warmup = max(1, int(args.runs)), max(0, int(args.warmup))
    if shutil.which("hyperfine"):
        report = scratch / "hyperfine.json"
        r = subprocess.run(["hyperfine", "--shell=none", "--style=none", "--runs", str(runs), "--warmup", str(warmup),
                            "--export-json", str(report), shlex.join(argv)],
                           cwd=scratch, capture_output=True, text=True, timeout=float(args.timeout))
        if r.returncode != 0:
            raise _Failed(f"the program failed: {(r.stderr or r.stdout).strip()[-800:]}", 1)
        times = [t * 1e3 for t in json.loads(report.read_text())["results"][0]["times"]]
        how = "hyperfine"
    else:                                   # no hyperfine: the same measurement, a plain loop
        times = []
        for i in range(warmup + runs):
            t0 = time.perf_counter()
            r = subprocess.run(argv, cwd=scratch, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                               timeout=float(args.timeout))
            dt = (time.perf_counter() - t0) * 1e3
            if r.returncode != 0:
                raise _Failed(f"the program failed (exit {r.returncode}): {r.stderr.strip()[-800:]}", 1)
            if i >= warmup:
                times.append(dt)
        how = "python"
    return {"time_ms": statistics.fmean(times), "time_ms_stddev": statistics.stdev(times) if len(times) > 1 else 0.0,
            "time_ms_min": min(times), "runs": len(times), "timer": how}


#: cachegrind's events, summed into the metrics (D1 = first-level data cache, LL = last level)
_COUNTS = {"instructions": ("Ir",), "d1_misses": ("D1mr", "D1mw"), "ll_misses": ("ILmr", "DLmr", "DLmw"),
           "branch_mispredicts": ("Bcm", "Bim")}


def _count(args: argparse.Namespace, argv: list[str], scratch: Path) -> dict[str, int]:
    if not shutil.which("valgrind"):
        raise _Failed("valgrind is not on PATH (the Nix dev shell has it)", 2)
    report = scratch / "cachegrind.out"
    r = subprocess.run(["valgrind", "--tool=cachegrind", "--vgdb=no", "--cache-sim=yes", "--branch-sim=yes",
                        f"--cachegrind-out-file={report}", *argv],
                       cwd=scratch, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                       timeout=float(args.timeout))
    if r.returncode != 0 or not report.exists():
        raise _Failed(f"the program failed under cachegrind: {r.stderr.strip()[-800:]}", 1)
    events: list[str] = []
    totals: list[int] = []
    for line in report.read_text().splitlines():
        if line.startswith("events:"):
            events = line.split()[1:]
        elif line.startswith("summary:"):
            totals = [int(x) for x in line.split()[1:]]
    ev = dict(zip(events, totals))
    return {name: sum(ev[e] for e in keys) for name, keys in _COUNTS.items() if all(e in ev for e in keys)}


def _size(args: argparse.Namespace, argv: list[str], scratch: Path) -> dict[str, int]:
    if not shutil.which("size"):
        raise _Failed("`size` (binutils) is not on PATH", 2)
    binary = argv[0]
    r = subprocess.run(["size", "-B", "-d", binary], capture_output=True, text=True, timeout=60)
    rows = [ln.split() for ln in r.stdout.splitlines()[1:] if ln.strip()]
    if r.returncode != 0 or not rows:
        raise _Failed(f"size could not read {binary}: {r.stderr.strip()[-400:]}", 1)
    text, data, bss = (int(x) for x in rows[0][:3])
    return {"text_bytes": text, "data_bytes": data, "bss_bytes": bss}


def _memory(args: argparse.Namespace, argv: list[str], scratch: Path) -> dict[str, float | int]:
    """The program's peak resident memory (D971): its own maximum RSS as the kernel counts it
    (`wait4`), the most over `--runs` runs -- the build's compiler is not counted."""
    import os

    peaks = []
    for _ in range(max(1, int(args.runs))):
        proc = subprocess.Popen(argv, cwd=scratch, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)
        deadline = time.monotonic() + float(args.timeout)
        while True:
            pid, status, usage = os.wait4(proc.pid, os.WNOHANG)
            if pid:
                break
            if time.monotonic() > deadline:
                proc.kill()
                raise _Failed(f"the program ran over {args.timeout:g} s", 1)
            time.sleep(0.01)
        proc.returncode = os.waitstatus_to_exitcode(status)
        if proc.returncode != 0:
            err = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
            raise _Failed(f"the program exited {proc.returncode}: {err[-400:]}", 1)
        peaks.append(int(usage.ru_maxrss))          # kilobytes on Linux
    return {"peak_rss_kb": max(peaks), "peak_rss_kb_min": min(peaks), "runs": len(peaks)}


cmd_prog_time = _measured(_time)
cmd_prog_count = _measured(_count)
cmd_prog_size = _measured(_size)
cmd_prog_memory = _measured(_memory)


def add_parsers(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("prog", help="Measure a program: time (hyperfine), count (Valgrind cachegrind), size; "
                                           "prints name=value lines.")
    sub = p.add_subparsers(dest="prog_command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--build", default="", help='One shell-quoted build command; {out} is the program it writes, '
                                                    'e.g. "c++ -O2 -o {out} ARTIFACT". Empty: nothing is built.')
        sp.add_argument("--run", default="", help="The command measured; {out} is the built program. "
                                                  "Empty: the built program alone.")
        sp.add_argument("--timeout", type=float, default=600.0, help="Seconds for the build and for each run.")

    t = sub.add_parser("time", help="Wall time over --runs runs after --warmup (hyperfine, else a Python loop): "
                                    "time_ms (mean), time_ms_stddev, time_ms_min, runs.")
    common(t)
    t.add_argument("--runs", type=int, default=10); t.add_argument("--warmup", type=int, default=1)
    t.set_defaults(func=cmd_prog_time)
    c = sub.add_parser("count", help="Valgrind cachegrind, deterministic: instructions, d1_misses, ll_misses, "
                                     "branch_mispredicts.")
    common(c)
    c.set_defaults(func=cmd_prog_count)
    m = sub.add_parser("memory", help="Peak resident memory over --runs runs (the kernel's own count): peak_rss_kb, "
                                      "peak_rss_kb_min, runs.")
    common(m)
    m.add_argument("--runs", type=int, default=3)
    m.set_defaults(func=cmd_prog_memory)
    s = sub.add_parser("size", help="The built program's (or BINARY's) sections: text_bytes, data_bytes, bss_bytes.")
    common(s)
    s.add_argument("binary", nargs="?", default=None, help="A program already built (instead of --build).")
    s.set_defaults(func=cmd_prog_size)


def main(argv: list[str] | None = None) -> int:
    """`fluxpreset prog time|count|size ...`."""
    parser = argparse.ArgumentParser(prog="fluxpreset prog")
    add_parsers(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["prog", *(argv if argv is not None else __import__("sys").argv[1:])])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
