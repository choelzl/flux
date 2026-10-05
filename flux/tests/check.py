"""Every check at once (D822): ruff, the unit suite, the heavy tests and the web e2e, side by side
-- they share nothing -- each into its own log, a line each when it ends, its time, and the whole
run's. From flux/, in the dev shell:

    python3 tests/check.py                       # all four
    python3 tests/check.py unit e2e              # some
    FLUX_E2E_STEPS=invitation python3 tests/check.py e2e

The unit suite runs on 32 workers that steal work from each other (`--dist worksteal`): more
workers than that gain nothing, a few long tests and each worker's collection being the floor.
Exit 1 when one fails; its log's last lines are printed.
"""

from __future__ import annotations

import os
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


def _said(job: str, text: str, ok: bool) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if job == "ruff":
        return lines[-1] if lines else ("clean" if ok else "failed")
    if job == "e2e":
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
    for job in wanted:
        fh = open(logs / f"{job}.log", "w")
        running[job] = (subprocess.Popen(JOBS[job], cwd=HERE, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                         env={**os.environ, "PYTHONUNBUFFERED": "1"}), fh, time.monotonic())
    print(f"running {', '.join(wanted)} at once; logs in {logs}", flush=True)
    failed = []
    while running:
        for job, (proc, fh, started) in list(running.items()):
            if proc.poll() is None:
                continue
            fh.close()
            text = (logs / f"{job}.log").read_text(errors="replace")
            ok = proc.returncode == 0 and not (job == "e2e" and "FAIL" in text)
            print(f"{'ok  ' if ok else 'FAIL'} {job:6} {time.monotonic() - started:5.0f}s  {_said(job, text, ok)}", flush=True)
            if not ok:
                failed.append(job)
            del running[job]
        time.sleep(0.5)
    print(f"all in {time.monotonic() - t0:.0f}s" + (f"; failed: {', '.join(failed)}" if failed else ""))
    for job in failed:
        print(f"\n--- {job}: the end of {logs / (job + '.log')}")
        print("\n".join((logs / f"{job}.log").read_text(errors="replace").splitlines()[-30:]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
