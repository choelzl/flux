"""Every check at once (D822): ruff, the unit suite, the heavy tests and the web e2e, side by side
-- they share nothing -- each into its own log, a line each when it ends, its time, and the whole
run's. From flux/, in the dev shell:

    python3 tests/check.py                       # all four
    python3 tests/check.py unit e2e              # some
    FLUX_E2E_STEPS=invitation python3 tests/check.py e2e

The unit suite runs on 24 workers that steal work from each other (`--dist worksteal`), heavy on 16:
more gain nothing while the e2e shards share the machine (D1011) -- a few long tests are the floor.
The e2e runs as seven shards side by side (D1003, D1011), each its own server, browser and home: three
of the steps that stand alone (the other web_ui_*.py files', `ALONE` and the first of `CHAINS`), four
of web_ui.py's other steps, each started from a setup made by API (bob, the sweep `sw` run once), the
few that need an earlier step's work kept together (`CHAINS`). `FLUX_E2E_STEPS` set runs those steps as one.
Exit 1 when one fails; its log's last lines are printed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
JOBS = {
    "ruff": ["ruff", "check", "."],
    "unit": [sys.executable, "-m", "pytest", "-q", "-n", "24", "--dist", "worksteal", "tests/unit"],
    "heavy": [sys.executable, "-m", "pytest", "-q", "-m", "heavy", "-n", "16", "--dist", "worksteal", "tests/unit"],
    "e2e": [sys.executable, "tests/e2e/web_ui.py"],
}


#: web_ui.py's steps that pass on a fresh server with nothing made before them (found by running
#: each step alone)
ALONE = ("phone", "sorted lists", "invitation", "documents migrated")
#: D1011: web_ui.py's steps that need what an earlier one made, kept together in their order; the
#: first runs on a fresh server (it checks the empty list and uploads), the others after the setup
CHAINS = (("login refused", "login", "upload", "every tab", "screens"),
          ("passes at once", "live tabs", "agent test", "files and questions"),     # `fromex`, made by the first
          ("error feedback", "insights"))                                          # the failed start Insights reads
#: the rest of web_ui.py runs on its own after the setup (`FLUX_E2E_SETUP=1`: bob, the sweep `sw`
#: uploaded and run once -- web_ui.Run.setup); seconds of the longer steps, to deal shards evenly
WEIGHT = {"start and stop": 56, "phone": 54, "agent test": 45, "live tabs": 21, "compact tables": 19, "author progress": 17,
          "a killed run": 16, "overview layout": 14, "admin sharing": 13, "passes at once": 12, "user groups": 12,
          "sorted lists": 11, "error feedback": 10, "keyboard journey": 10}
PLAIN_SHARDS, SETUP_SHARDS = 3, 4


def _steps(name: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'r\.step\("([^"]+)"', (HERE / "tests/e2e" / name).read_text())))


def _deal(groups: list[tuple[str, ...]], n: int, order: dict[str, int]) -> list[list[str]]:
    """The groups over `n` shards, the heaviest first onto the lightest; each shard in the steps' own order."""
    shards: list[list[str]] = [[] for _ in range(n)]
    load = [0] * n
    for g in sorted(groups, key=lambda g: -sum(WEIGHT.get(s, 5) for s in g)):
        i = load.index(min(load))
        shards[i] += g
        load[i] += sum(WEIGHT.get(s, 5) for s in g)
    return [sorted(sh, key=order.__getitem__) for sh in shards if sh]


def e2e_shards() -> list[tuple[list[str], bool]]:
    """(steps, with the setup) per shard: the first chain and every step that stands alone on plain
    shards; the other chains and the rest of web_ui.py on shards that start from the setup."""
    main = _steps("web_ui.py")
    others = [s for p in sorted((HERE / "tests/e2e").glob("web_ui_*.py")) for s in _steps(p.name)]   # each sets up its own
    order = {s: i for i, s in enumerate(main + others)}
    chained = {s for c in CHAINS for s in c}
    plain = [CHAINS[0], *((s,) for s in main if s in ALONE), *((s,) for s in others)]
    setup = [*CHAINS[1:], *((s,) for s in main if s not in ALONE and s not in chained)]
    return [(sh, False) for sh in _deal(plain, PLAIN_SHARDS, order)] + [(sh, True) for sh in _deal(setup, SETUP_SHARDS, order)]


def _e2e(logs: Path) -> list[tuple[str, list[str], dict[str, str]]]:
    """(job, argv, env) per e2e shard: its steps, the setup or not, its own home (the first keeps the default one)."""
    if os.environ.get("FLUX_E2E_STEPS"):
        return [("e2e", JOBS["e2e"], {})]
    homes = Path.home() / "snap" / "firefox" / "common"     # where a snap Firefox may read its profile
    return [(f"e2e{i}", JOBS["e2e"], {"FLUX_E2E_STEPS": ",".join(steps), **({"FLUX_E2E_SETUP": "1"} if setup else {}),
                                      **({"FLUX_E2E_HOME": str(homes / f"flux-e2e-{i}")} if i else {})})
            for i, (steps, setup) in enumerate(e2e_shards())]


def _said(job: str, text: str, ok: bool) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if job == "ruff":
        return lines[-1] if lines else ("clean" if ok else "failed")
    if job.startswith("e2e"):
        return next((ln.strip() for ln in reversed(lines) if "checks passed" in ln), lines[-1] if lines else "")
    return next((ln.strip() for ln in reversed(lines) if " passed" in ln or " failed" in ln or " error" in ln), lines[-1] if lines else "")


def main(argv: list[str]) -> int:
    wanted = [a for a in argv if a in JOBS] or list(JOBS)
    unknown = [a for a in argv if a not in JOBS]
    if unknown:
        print(f"unknown: {', '.join(unknown)}; the jobs: {', '.join(JOBS)}", file=sys.stderr)
        return 2
    logs = Path(tempfile.mkdtemp(prefix="flux-check-"))
    t0 = time.monotonic()
    running = {}
    plan = [x for job in wanted for x in (_e2e(logs) if job == "e2e" else [(job, JOBS[job], {})])]
    for job, argv, env in plan:
        fh = open(logs / f"{job}.log", "w")
        running[job] = (subprocess.Popen(argv, cwd=HERE, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         env={**os.environ, "PYTHONUNBUFFERED": "1", **env}), fh, time.monotonic())
    print(f"running {', '.join(wanted)} at once; logs in {logs}", flush=True)
    failed = []
    while running:
        for job, (proc, fh, started) in list(running.items()):
            if proc.poll() is None:
                continue
            fh.close()
            text = (logs / f"{job}.log").read_text(errors="replace")
            ok = proc.returncode == 0 and not (job.startswith("e2e") and "FAIL" in text)
            print(f"{'ok  ' if ok else 'FAIL'} {job:6} {time.monotonic() - started:5.0f}s  {_said(job, text, ok)}", flush=True)
            if not ok:
                failed.append(job)
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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
