"""`flux selftest`: does Flux work on this machine? One command runs what a newcomer would, in a
temporary directory, and prints PASS / FAIL / SKIP per check with the time it took.

    flux selftest              # a command-driven loop, the model, a model-written problem
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


def _smoke(cwd: Path) -> tuple[bool, str]:
    """Exercise generation, checks, measurements and selection through ordinary app scripts."""
    home = cwd / "smoke"
    home.mkdir()
    (home / "gen.py").write_text("from pathlib import Path\nimport sys\nPath(sys.argv[1]).write_text(sys.argv[2])\n")
    (home / "check.py").write_text("from pathlib import Path\nimport sys\nassert int(Path(sys.argv[1]).read_text()) in (1, 2, 3)\n")
    (home / "measure.py").write_text("from pathlib import Path\nimport sys\nprint('score=' + Path(sys.argv[1]).read_text())\n")
    (home / "problem.yaml").write_text("""statement: Select the largest measured score.
language: text
flow:
  orchestrate: {policy: sweep, space: {x: [1, 2, 3]}}
  generate: {command: '{python} {home}/gen.py {artifact} {x}'}
  test: '{python} {home}/check.py {artifact}'
  measure:
    score: {command: '{python} {home}/measure.py {artifact}', metrics: [score]}
  knowledge: off
objectives: [{metric: score, direction: maximize}]
budget: {steps: 1, prototype: false}
""")
    return _run_doc(home / "problem.yaml", cwd, 300, passes=3)


def _application(cwd: Path, name: str, timeout: float, *extra: str, passes: int = 1) -> tuple[bool | None, str]:
    """Test a bundled application in a disposable copy, including its local tool commands."""
    source = FLUX / "applications" / name
    if not source.is_dir():
        return None, "bundled application unavailable in this installation"
    home = cwd / name
    shutil.copytree(source, home, ignore=shutil.ignore_patterns("out", "workbench", "runs", "__pycache__"))
    return _run_doc(home / "problem.yaml", cwd, timeout, *extra, passes=passes)


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
        check("a command-driven loop, no model", lambda: _smoke(work))
        if args.full:
            check("the README's first run (adder16)", lambda: (None, "needs verilator, yosys and openroad") if rtl_tools
                  else _application(work, "adder16", 1800, "--screen-only", passes=6))
        if args.no_model:
            check("the model", lambda: (None, "--no-model"))
        else:
            from flux_llm import OpenAIChatProposer, describe_model

            prop = OpenAIChatProposer(args.model)
            down = prop.preflight()
            check("the model server", lambda: (not down, down or describe_model(prop)))
            check("a problem the model writes (primes)", lambda: (None, "the model server is not ready") if down
                  else _application(work, "primes", float(args.model_timeout), *(("--model", args.model) if args.model else ())))
        agent = next((a for a in ("opencode", "claude", "codex") if shutil.which(a)), None)
        check("a coding agent on PATH", lambda: (bool(agent) or None, agent or "none of opencode, claude, codex"))
    failed = [r for r in rows if r[0] == "FAIL"]
    print(f"{len(rows) - len(failed)} of {len(rows)} checks passed or skipped"
          + (f"; FAILED: {', '.join(r[1] for r in failed)}" if failed else ""))
    return 1 if failed else 0
