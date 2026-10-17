"""Every check at once (D822): ruff, the unit suite, the heavy tests and the web e2e, side by side
-- they share nothing -- each into its own log, a line each when it ends, its time, and the whole
run's. From flux/, in the dev shell:

    python3 tests/check.py                       # all four, everything
    python3 tests/check.py unit e2e              # some
    python3 tests/check.py --area live,files     # the areas' unit files, their heavy tests and e2e steps (D1171)
    python3 tests/check.py --changed             # the areas the uncommitted and untracked changes touch
    python3 tests/check.py --changed main~3      # ... the changes since main~3
    python3 tests/check.py --list                # the areas, with their counts
    FLUX_E2E_STEPS=invitation python3 tests/check.py e2e

An area (tests/areas.py) is its product files, its unit test files and its e2e steps. `--changed` maps each
changed file to the areas whose files it is (a changed test file to its own); a file in `areas.SHARED`, or one
in flux/ no area names, runs everything. `--area` and `--changed` combine with the jobs: `--area live unit`.

The unit suite runs on up to 24 workers that steal work from each other (`--dist worksteal`), heavy on up to 16
(fewer for a few files): more gain nothing while the e2e shards share the machine (D1011) -- a few long tests
are the floor. The e2e runs as shards side by side (D1003, D1011), each its own server, browser and home: the
steps that stand alone and the first of `CHAINS` on plain shards, the steps that start from a setup made by API
(bob, the sweep `sw` run once; `setup=True` on their `r.step`) and the other `CHAINS` on setup shards, dealt by
`WEIGHT`, the heaviest first -- 3 + 4 shards, more when the machine has idle cores (`FLUX_E2E_SHARDS=plain,setup`
to choose), fewer for a few steps. A chain's step selected brings the chain's steps before it.
`FLUX_E2E_STEPS` set runs those steps as one. Exit 1 when one fails; its log's last lines are printed.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "tests"))
import areas  # noqa: E402

PYTEST = [sys.executable, "-m", "pytest", "-q"]
JOBS = {
    "ruff": ["ruff", "check", "."],
    "unit": [*PYTEST, "-n", "24", "--dist", "worksteal", "tests/unit"],
    "heavy": [*PYTEST, "-m", "heavy", "-n", "16", "--dist", "worksteal", "tests/unit"],
    "e2e": [sys.executable, "tests/e2e/web_ui.py"],
}


#: D1011: steps that need what an earlier one made, kept together in their order; the first runs on a
#: fresh server (it checks the empty list and uploads), the others after the setup
CHAINS = (("login refused", "login", "upload", "every tab", "screens"),
          ("passes at once", "live tabs", "the decision and the best", "agent test", "files and questions"),     # `fromex`, made by the first
          ("error feedback", "insights"))                                          # the failed start Insights reads
#: steps never dealt to one shard: "graphs" leaves sw's results unranked (its measurement preferences), and the
#: decision step ranks sw's when it has more designs than `fromex` (found by D1171's deal; a whole run in one
#: process meets it too)
APART = (("graphs", "the decision and the best"),)
#: seconds of the steps, measured on a loaded machine (D1171), to deal shards evenly; a step not named: 3
WEIGHT = {"start and stop": 115, "phone": 103, "talk clear": 57, "keyboard journey": 53, "a killed run": 50, "author refused": 48,
          "passes at once": 46, "agent test": 41, "agent views": 38, "user groups": 22, "admin sharing": 20, "error feedback": 19,
          "overview layout": 15, "file manager": 13, "ask conversations": 12, "author progress": 12, "documents migrated": 11,
          "live page": 11, "compact tables": 11, "graphs": 11, "sorted lists": 10, "reload and browser history": 10, "live tabs": 10,
          "login refused": 9, "admin impersonation": 8, "admin permissions": 8, "phone wizard": 8, "main measurements": 7,
          "selected problem": 7, "loop ownership": 7, "dse setting": 7, "dictionary metrics": 6, "every tab": 6, "new loop by agent": 5,
          "loop controls": 5, "hidden from AI": 5, "admin": 5, "raw network setting": 5, "dark": 5, "upload zip": 5}
#: what a shard costs before its first step (a server, a browser; the setup), in WEIGHT's seconds
SHARD_COST = 40
PLAIN_SHARDS, SETUP_SHARDS = 3, 4


def _weight(group) -> int:
    return sum(WEIGHT.get(s, 3) for s in group)


def _deal(groups: list[tuple[str, ...]], n: int, order: dict[str, int]) -> list[list[str]]:
    """The groups over `n` shards, the heaviest first onto the lightest; each shard in the steps' own order."""
    shards: list[list[str]] = [[] for _ in range(n)]
    load = [0] * n
    for g in sorted(groups, key=lambda g: -_weight(g)):
        clash = {i for i, sh in enumerate(shards) for a, b in APART if (a in g and b in sh) or (b in g and a in sh)}
        i = min((i for i in range(n) if i not in clash), key=load.__getitem__, default=load.index(min(load)))
        shards[i] += g
        load[i] += _weight(g)
    return [sorted(sh, key=order.__getitem__) for sh in shards if sh]


def shard_counts() -> tuple[int, int]:
    """(plain, setup) shards: 3 + 4, two more of each on a machine with idle cores to spare (FLUX_E2E_SHARDS=p,s)."""
    if os.environ.get("FLUX_E2E_SHARDS"):
        p, s = os.environ["FLUX_E2E_SHARDS"].split(",")
        return int(p), int(s)
    idle = (os.cpu_count() or 1) - os.getloadavg()[0]
    return (PLAIN_SHARDS + 2, SETUP_SHARDS + 2) if idle >= 48 else (PLAIN_SHARDS, SETUP_SHARDS)


def e2e_shards(wanted: set[str] | None = None) -> list[tuple[list[str], bool]]:
    """(steps, with the setup) per shard, for every step or the `wanted` ones: the first chain and every step that
    stands alone on plain shards; the other chains and the steps that start from the setup on setup shards."""
    sys.path.insert(0, str(HERE / "tests/e2e"))
    from steps import ORDER

    steps = areas.e2e_steps()
    if wanted is not None:                          # a chain's step brings the chain's steps before it
        wanted = set(wanted)
        for c in CHAINS:
            picked = [i for i, s in enumerate(c) if s in wanted]
            if picked:
                wanted |= set(c[:picked[-1] + 1])
    keep = (lambda s: True) if wanted is None else (lambda s: s in wanted)
    order = {s: i for i, s in enumerate(ORDER)}
    chained = {s for c in CHAINS for s in c}
    chains = [tuple(s for s in c if keep(s)) for c in CHAINS]
    plain = [g for g in [chains[0], *((s,) for s in ORDER if keep(s) and s not in chained and not steps[s][1])] if g]
    setup = [g for g in [*chains[1:], *((s,) for s in ORDER if keep(s) and s not in chained and steps[s][1])] if g]
    n_plain, n_setup = shard_counts()
    # a shard costs its start: a few steps go on fewer shards
    n_plain = min(n_plain, max(1, math.ceil(sum(map(_weight, plain)) / SHARD_COST)))
    n_setup = min(n_setup, max(1, math.ceil(sum(map(_weight, setup)) / SHARD_COST)))
    return ([(sh, False) for sh in _deal(plain, n_plain, order)] if plain else []) + \
           ([(sh, True) for sh in _deal(setup, n_setup, order)] if setup else [])


def _e2e(steps: set[str] | None, homes: Path) -> list[tuple[str, list[str], dict[str, str]]]:
    """(job, argv, env) per e2e shard: its steps, the setup or not, its own home in `homes` -- this run's: a run
    starting deletes its home's files and browser profile, and two checks at once (two agents) on the same homes
    broke each other's steps (a stand-in agent's program gone, a profile pulled from under its browser)."""
    if os.environ.get("FLUX_E2E_STEPS"):
        return [("e2e", JOBS["e2e"], {} if os.environ.get("FLUX_E2E_HOME") else {"FLUX_E2E_HOME": str(homes / "e2e")})]
    return [(f"e2e{i}", JOBS["e2e"], {"FLUX_E2E_STEPS": ",".join(sh), **({"FLUX_E2E_SETUP": "1"} if setup else {}),
                                      "FLUX_E2E_HOME": str(homes / f"e2e{i}")})
            for i, (sh, setup) in enumerate(e2e_shards(steps))]


def _pytest(job: str, files: list[str] | None) -> list[str]:
    """The unit or heavy job, on every file or on `files` -- its workers scaled to how many."""
    if files is None:
        return JOBS[job]
    cap = 24 if job == "unit" else 16
    n = min(cap, len(files))
    return [*PYTEST, *(["-m", "heavy"] if job == "heavy" else []), *(["-n", str(n), "--dist", "worksteal"] if n > 1 else []),
            *(f"tests/unit/{f}" for f in files)]


# ---- which areas
def changed_files(ref: str) -> list[str]:
    """The files changed since `ref` (committed after it, staged or not) and the untracked ones, relative to flux/."""
    root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=HERE, capture_output=True, text=True, check=True).stdout.strip())
    got = subprocess.run(["git", "diff", "--name-only", ref, "--"], cwd=root, capture_output=True, text=True, check=True).stdout.split()
    got += subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=root, capture_output=True, text=True, check=True).stdout.split()
    flux = HERE.relative_to(root).as_posix() + "/"
    return sorted({p[len(flux):] if p.startswith(flux) else "../" + p for p in got})


def from_changes(ref: str) -> tuple[list[str] | None, str]:
    """The areas `ref`'s changes touch (None: everything), and why, said in a line."""
    files = changed_files(ref)
    if not files:
        return [], f"no change since {ref}"
    everything = [f for f in files if areas.areas_of(f) is None]
    if everything:
        shared = [f for f in everything if areas.matches(f, areas.SHARED)]
        why = (f"{len(shared)} shared ({', '.join(shared[:3])}{', ...' if len(shared) > 3 else ''})" if shared else "") + \
              (", " if shared and len(shared) < len(everything) else "") + \
              (f"{len(everything) - len(shared)} in no area ({', '.join([f for f in everything if f not in shared][:3])})"
               if len(shared) < len(everything) else "")
        return None, f"{len(files)} files changed since {ref}; everything: {why}"
    counts: dict[str, int] = {}
    for f in files:
        for a in areas.areas_of(f):
            counts[a] = counts.get(a, 0) + 1
    none = sum(1 for f in files if not areas.areas_of(f))
    return sorted(counts), (f"{len(files)} files changed since {ref}: " + ", ".join(f"{a} ({n})" for a, n in sorted(counts.items()))
                            + (f"; {none} that no test here reads (outside flux/, scripts/)" if none else ""))


def selection(names: list[str]) -> tuple[list[str], set[str]]:
    """The unit files and the e2e steps of the areas `names`."""
    steps = areas.e2e_steps()
    return sorted({f for a in names for f in areas.unit_files(a)}), {s for a in names for s in areas.e2e_of(a, steps)}


def list_areas() -> None:
    steps = areas.e2e_steps()
    print(f"{'area':13} {'unit':>4} {'e2e':>4}  sources")
    for a, spec in areas.AREAS.items():
        print(f"{a:13} {len(areas.unit_files(a)):4} {len(areas.e2e_of(a, steps)):4}  {len(spec['sources'])} globs")
    print(f"shared: {len(areas.SHARED)} globs (a change there runs everything); "
          f"{len(list((HERE / 'tests/unit').glob('test_*.py')))} unit files, {len(steps)} e2e steps in all")


def _said(job: str, text: str, ok: bool) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if job == "ruff":
        return lines[-1] if lines else ("clean" if ok else "failed")
    if job.startswith("e2e"):
        return next((ln.strip() for ln in reversed(lines) if "checks passed" in ln), lines[-1] if lines else "")
    return next((ln.strip() for ln in reversed(lines) if " passed" in ln or " failed" in ln or " error" in ln
                 or "no tests ran" in ln or " deselected" in ln), lines[-1] if lines else "")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="check.py", description="ruff, unit, heavy and e2e side by side (D822, D1171)")
    ap.add_argument("jobs", nargs="*", help=f"some of: {', '.join(JOBS)} (all when none)")
    ap.add_argument("--area", help="comma-separated areas (tests/areas.py; --list)")
    ap.add_argument("--changed", nargs="?", const="HEAD", metavar="REF", help="the areas changed since REF (HEAD: uncommitted and untracked)")
    ap.add_argument("--list", action="store_true", help="the areas and their counts")
    args = ap.parse_args(argv)
    if args.list:
        list_areas()
        return 0
    unknown = [a for a in args.jobs if a not in JOBS]
    if unknown:
        print(f"unknown: {', '.join(unknown)}; the jobs: {', '.join(JOBS)}", file=sys.stderr)
        return 2
    wanted = args.jobs or list(JOBS)
    picked: list[str] | None = None                 # None: everything
    if args.area:
        picked = [a.strip() for a in args.area.split(",") if a.strip()]
        bad = [a for a in picked if a not in areas.AREAS]
        if bad:
            print(f"unknown area: {', '.join(bad)}; the areas: {', '.join(areas.AREAS)}", file=sys.stderr)
            return 2
        print(f"areas {', '.join(picked)}", flush=True)
    if args.changed:
        got, why = from_changes(args.changed)
        print(why, flush=True)
        picked = None if got is None else sorted(set(picked or []) | set(got))
        if picked == []:
            print("nothing to check")
            return 0
    units, steps = (None, None) if picked is None else selection(picked)
    if picked is None and (args.area or args.changed):
        print("running everything", flush=True)
    if picked is not None:
        print(f"running the areas {', '.join(picked)}: {len(units)} unit files, {len(steps)} e2e steps", flush=True)
    logs = Path(tempfile.mkdtemp(prefix="flux-check-"))
    homes = None                                  # where a snap Firefox may read its profile; this run's alone
    if "e2e" in wanted:
        (Path.home() / "snap" / "firefox" / "common").mkdir(parents=True, exist_ok=True)
        homes = Path(tempfile.mkdtemp(prefix="flux-check-", dir=Path.home() / "snap" / "firefox" / "common"))
    t0 = time.monotonic()
    plan = []
    for job in wanted:
        if job == "e2e":
            plan += [] if steps is not None and not steps else _e2e(steps, homes)
        elif job in ("unit", "heavy"):
            plan += [] if units is not None and not units else [(job, _pytest(job, units), {})]
        else:
            plan.append((job, JOBS[job], {}))
    running, env_of = {}, {job: env for job, _cmd, env in plan}
    for job, cmd, env in plan:
        fh = open(logs / f"{job}.log", "w")
        running[job] = (subprocess.Popen(cmd, cwd=HERE, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         env={**os.environ, "PYTHONUNBUFFERED": "1", **env}), fh, time.monotonic())
    n_e2e = sum(j.startswith("e2e") for j, _, _ in plan)
    print(f"running {', '.join(dict.fromkeys(j.rstrip('0123456789') for j, _, _ in plan))} at once"
          + (f" ({n_e2e} e2e shards)" if n_e2e > 1 else "") + f"; logs in {logs}", flush=True)
    failed = []
    while running:
        for job, (proc, fh, started) in list(running.items()):
            if proc.poll() is None:
                continue
            fh.close()
            text = (logs / f"{job}.log").read_text(errors="replace")
            # pytest's 5: nothing collected -- the files picked hold no test of that kind (all heavy, or none)
            ok = (proc.returncode == 0 or (job in ("unit", "heavy") and proc.returncode == 5)) and not (job.startswith("e2e") and "FAIL" in text)
            print(f"{'ok  ' if ok else 'FAIL'} {job:6} {time.monotonic() - started:5.0f}s  {_said(job, text, ok)}", flush=True)
            if not ok:
                failed.append(job)
            elif "FLUX_E2E_HOME" in env_of[job]:
                shutil.rmtree(env_of[job]["FLUX_E2E_HOME"], ignore_errors=True)   # a failed shard's kept: its shots
            del running[job]
        time.sleep(0.5)
    shards = sorted(logs.glob("e2e[0-9]*.log"))
    if shards:                                    # D1003: the shards' checks as one count
        got = [re.search(r"(\d+) of (\d+) checks passed", p.read_text(errors="replace")) for p in shards]
        passed, total = (sum(int(m.group(k)) for m in got if m) for k in (1, 2))
        print(f"e2e: {passed} of {total} checks passed in {len(shards)} shards" + ("" if all(got) else " (a shard said no count)"))
    print(f"all in {time.monotonic() - t0:.0f}s" + (f"; failed: {', '.join(failed)}" if failed else ""))
    for job in failed:
        print(f"\n--- {job}: the end of {logs / (job + '.log')}")
        print("\n".join((logs / f"{job}.log").read_text(errors="replace").splitlines()[-30:]))
    if homes is not None and not any(j.startswith("e2e") for j in failed):
        shutil.rmtree(homes, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
