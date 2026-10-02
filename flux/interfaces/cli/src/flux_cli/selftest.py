"""`flux selftest`: does Flux work on this machine? One command runs what a newcomer would, in a
temporary directory, and prints PASS / FAIL / SKIP per check with the time it took.

    flux selftest              # tools, a sweep, an RTL sweep, the model, a model-written problem
    flux selftest --full       # also the README's first run (adder16, about three minutes)
    flux selftest --no-model   # only what needs no model
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

__all__ = ["cmd_selftest"]

FLUX = Path(__file__).resolve().parents[4]          # flux/: the applications live here


def _flux(*args: str, cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-W", "ignore", "-m", "flux_cli.main", *args], cwd=cwd,
                          capture_output=True, text=True, timeout=timeout)


def _decided(cwd: Path, kind: str, timeout: float, *extra: str, passes: int = 1) -> tuple[bool, str]:
    """`flux new` of this kind, run for `passes` passes: (a decision was made, what it was or why not)."""
    name = f"st_{kind.replace('-', '_')}"
    made = _flux("new", name, "--kind", kind, cwd=cwd, timeout=120)
    if made.returncode != 0:
        return False, (made.stdout + made.stderr).strip().splitlines()[-1:][0] if (made.stdout + made.stderr).strip() else "flux new failed"
    return _run_doc(cwd / name / "problem.yaml", cwd, timeout, *extra, passes=passes)


def _run_doc(doc: Path, cwd: Path, timeout: float, *extra: str, passes: int = 1) -> tuple[bool, str]:
    answer = cwd / f"{doc.stem}.answer.json"
    try:
        run = _flux("task", "run", str(doc), "--passes", str(passes), "--json", str(answer), *extra, cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"no decision within {timeout:.0f}s"
    try:
        dec = json.loads(answer.read_text()).get("decision") or {}
    except (OSError, ValueError):
        dec = {}
    if dec:
        metrics = ", ".join(f"{k}={v:.4g}" for k, v in (dec.get("metrics") or {}).items() if isinstance(v, (int, float)))
        return True, f"{dec.get('name', 'decided')}: {metrics}"[:110]
    tail = [ln for ln in (run.stdout + run.stderr).splitlines() if ln.strip()]
    return False, (tail[-1] if tail else f"exit {run.returncode}")[:160]


def cmd_selftest(args: argparse.Namespace) -> int:
    rows: list[tuple[str, str, float, str]] = []

    def check(name: str, fn: Callable[[], tuple[bool | None, str]]) -> None:
        t0 = time.monotonic()
        try:
            ok, why = fn()
        except Exception as exc:  # noqa: BLE001 -- a check that crashes is a failed check
            ok, why = False, f"{type(exc).__name__}: {exc}"[:160]
        state = "SKIP" if ok is None else "PASS" if ok else "FAIL"
        rows.append((state, name, time.monotonic() - t0, why))
        print(f"  {state:4s}  {name:44s} {time.monotonic() - t0:6.1f}s  {why}", flush=True)

    rtl_tools = [t for t in ("verilator", "yosys", "openroad") if shutil.which(t) is None]   # openroad times the synthesis
    print("flux selftest: each check runs in a temporary directory", flush=True)
    with tempfile.TemporaryDirectory(prefix="flux-selftest-") as d:
        work = Path(d)
        check("tools on PATH", lambda: (                 # the loop alone (pip) is a setup too: missing is a skip
            True if not rtl_tools else None,
            "verilator, yosys, openroad" if not rtl_tools
            else f"missing {', '.join(rtl_tools)}: the RTL checks skip (use `nix develop`)"))
        check("a sweep, no model (flux new --kind sweep)", lambda: _decided(work, "sweep", 300, passes=6))         # D738: a pass a point
        check("an RTL sweep (flux new --kind rtl-sweep)", lambda: (None, "needs verilator, yosys and openroad") if rtl_tools
              else _decided(work, "rtl-sweep", 900, "--screen-only", passes=6))
        if args.full:
            check("the README's first run (adder16)", lambda: (None, "needs verilator, yosys and openroad") if rtl_tools
                  else _run_doc(FLUX / "applications/adder16/problem.yaml", work, 1800, "--screen-only",
                                "--db", str(work / "adder16.db"), "--out", str(work / "adder16.v"), passes=12))
        if args.no_model:
            check("the model", lambda: (None, "--no-model"))
        else:
            from flux_llm import OpenAIChatProposer, describe_model

            prop = OpenAIChatProposer(args.model)
            down = prop.preflight()
            check("the model server", lambda: (not down, down or describe_model(prop)))
            check("a problem the model writes (--kind python)", lambda: (None, "the model server is not ready") if down
                  else _decided(work, "python", float(args.model_timeout), *(("--model", args.model) if args.model else ())))
        agent = next((a for a in ("opencode", "claude", "codex") if shutil.which(a)), None)
        check("a coding agent on PATH", lambda: (bool(agent) or None, agent or "none of opencode, claude, codex"))
    failed = [r for r in rows if r[0] == "FAIL"]
    print(f"{len(rows) - len(failed)} of {len(rows)} checks passed or skipped"
          + (f"; FAILED: {', '.join(r[1] for r in failed)}" if failed else ""))
    return 1 if failed else 0
