"""Every check at once (D822): ruff, the unit suite, the heavy tests and the web e2e, side by side
-- they share nothing -- each into its own log, a line each when it ends, its time, and the whole
run's. From flux/, in the dev shell:

    python3 tests/check.py                       # all four
    python3 tests/check.py unit e2e              # some
    FLUX_E2E_STEPS=invitation python3 tests/check.py e2e

The unit suite runs on 32 workers that steal work from each other (`--dist worksteal`): more
workers than that gain nothing, a few long tests and each worker's collection being the floor.
The e2e runs as three shards side by side (D1003), each its own server, browser and home: the steps
of web_ui.py that build on each other in order (the floor), and two of the steps that stand alone
(the other web_ui_*.py files', and `ALONE` below) -- `FLUX_E2E_STEPS` set runs those steps as one.
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
    "unit": [sys.executable, "-m", "pytest", "-q", "-n", "32", "--dist", "worksteal", "tests/unit"],
    "heavy": [sys.executable, "-m", "pytest", "-q", "-m", "heavy", "-n", "24", "--dist", "worksteal", "tests/unit"],
    "e2e": [sys.executable, "tests/e2e/web_ui.py"],
}


#: web_ui.py's steps that pass on their own (D1003: found by running each step alone); the rest
#: of that file is one chain -- a login, the loops the steps before made
ALONE = ("phone", "sorted lists", "invitation", "documents migrated")      # not "error feedback": Insights reads its start
E2E_SHARDS = 3


def _steps(name: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'r\.step\("([^"]+)"', (HERE / "tests/e2e" / name).read_text())))


def e2e_shards() -> list[list[str]]:
    """The chain whole, in its order; the steps that stand alone dealt over the other shards."""
    chain = [s for s in _steps("web_ui.py") if s not in ALONE]
    alone = [s for s in _steps("web_ui.py") if s in ALONE] + [s for p in sorted((HERE / "tests/e2e").glob("web_ui_*.py"))
                                                              for s in _steps(p.name)]     # each sets up its own
    rest = [alone[i::E2E_SHARDS - 1] for i in range(E2E_SHARDS - 1)]
    return [chain, *rest]


def _e2e(logs: Path) -> list[tuple[str, list[str], dict[str, str]]]:
    """(job, argv, env) per e2e shard: its steps, its own home (the first keeps the default one)."""
    if os.environ.get("FLUX_E2E_STEPS"):
        return [("e2e", JOBS["e2e"], {})]
    homes = Path.home() / "snap" / "firefox" / "common"     # where a snap Firefox may read its profile
    return [(f"e2e{i}", JOBS["e2e"], {"FLUX_E2E_STEPS": ",".join(steps),
                                      **({"FLUX_E2E_HOME": str(homes / f"flux-e2e-{i}")} if i else {})})
            for i, steps in enumerate(e2e_shards())]


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
    shards = sorted(logs.glob("e2e?.log"))
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
