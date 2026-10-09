"""Reusable build, check and measurement commands behind an application's champsim.py.

    flow:
      test: '{python} {home}/champsim.py check {artifact} --traces {home}/traces'
      measure:
        screen:
          command: '{python} {home}/champsim.py run {artifact} --traces {home}/traces --warmup 10000000 --sim 15000000'
          metrics: [geomean_speedup]
          needs: [pythia]

ARTIFACT is an `.ini` of knobs (its `l2c_prefetcher_types = a,b` line picks the L2 prefetchers)
or a `.h` holding one `class X : public Prefetcher`, built in and run as `x`. The commands wrap
`champsim_tools`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

__all__ = ["cmd_champsim_build", "cmd_champsim_check", "cmd_champsim_run", "main"]

#: The smoke run of `check`: long enough for a prefetcher to issue, short enough to be a gate.
SMOKE = (200_000, 1_000_000)


def _types(given: str | None) -> list[str]:
    return [t.strip() for t in (given or "").split(",") if t.strip()]


def cmd_champsim_run(args: argparse.Namespace) -> int:
    from champsim_tools import BuildFailedError, measure

    try:
        got = measure(args.artifact, args.traces, int(args.warmup), int(args.sim),
                      with_types=_types(args.with_), jobs=args.jobs, config=args.config)
    except BuildFailedError as exc:
        print(exc.first_error)
        print("1 failing")
        return 3
    first = ["geomean_speedup"]
    for k in [*first, *sorted(k for k in got if k not in first)]:
        v = got[k]
        print(f"{k}={v:.6g}" if isinstance(v, float) and not v.is_integer() else f"{k}={int(v)}")
    return 0


def _build(header: Path) -> tuple[int, object]:
    from champsim_tools import BuildError, build_header

    try:
        got = build_header(header.read_text())
    except BuildError as exc:
        print(exc)
        print("1 failing")
        return 3, None
    if not got.ok:
        print(got.first_error or "the build failed")
        print("1 failing")
        return 3, None     # exit 3: did not build
    return 0, got


def cmd_champsim_build(args: argparse.Namespace) -> int:
    code, _ = _build(Path(args.header))
    if code == 0:
        print("0 failing")
    return code


def cmd_champsim_check(args: argparse.Namespace) -> int:
    from champsim_tools import SimulationFailedError, resolve_traces, simulate

    code, got = _build(Path(args.header))
    if code:
        return code
    paths = resolve_traces(args.traces)
    trace = min(paths.values(), key=lambda p: p.stat().st_size)      # the quickest to stream
    try:
        ran = simulate(got.binary, "", [got.name], trace, *SMOKE)
    except SimulationFailedError as exc:
        print(exc)
        print("1 failing: the smoke run failed")
        return 1
    print(f"trace={trace.name} ipc={ran['ipc']:.6g} l2_pf_issued={int(ran['l2_pf_issued'])} l2_pf_useful={int(ran['l2_pf_useful'])}")
    if ran["l2_pf_issued"] == 0:
        print("1 failing: issued no prefetches")
        return 1
    print("0 failing")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Commands exposed by the prefetcher application's champsim.py."""
    parser = argparse.ArgumentParser(description="Application ChampSim checks and measurements")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Measure an INI file or prefetcher header on traces")
    run.add_argument("artifact")
    run.add_argument("--traces", required=True)
    run.add_argument("--warmup", type=int, required=True)
    run.add_argument("--sim", type=int, required=True)
    run.add_argument("--with", dest="with_", default=None)
    run.add_argument("--jobs", type=int, default=None)
    run.add_argument("--config", default=None)
    run.set_defaults(func=cmd_champsim_run)
    build = sub.add_parser("build", help="Build a prefetcher header")
    build.add_argument("header")
    build.set_defaults(func=cmd_champsim_build)
    check = sub.add_parser("check", help="Build and smoke-run a prefetcher header")
    check.add_argument("header")
    check.add_argument("--traces", required=True)
    check.set_defaults(func=cmd_champsim_check)
    args = parser.parse_args(argv)
    return args.func(args)
