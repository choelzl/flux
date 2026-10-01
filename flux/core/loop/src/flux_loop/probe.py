"""`flux probe` (D678): a coding agent checks its file with the loop's own tools, mid-turn.

The loop writes a context file into the agent's work directory for each turn (the document,
the part, the budget, the log) and names it in `FLUX_PROBE`. `flux probe gate FILE` runs the
document's gate (the correctness checks) on FILE; `flux probe measure FILE --stage S [--stage T
...]` each named stage (a measurement) on its own, side by side, with its verdict against its
limits (D679; `--gate` puts the gate first). Both go through the same `PromptProblem` the loop
runs: the same commands, flags and metric parsing. A
prototype turn's `gate` is the prototype's own check. Each probe is a line in the turn's log,
read back by the loop and put on the record as the agent's own check, never as a measured
candidate. A budget per turn bounds them: the gate `probe.gate` times, each stage
`probe.<stage>` (else `probe.stages`) times.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

__all__ = ["PROBE_DEFAULT", "budget_of", "probe", "probe_context", "probe_line", "probes_done"]

#: The budget per turn unless the agent's `probe:` says otherwise.
PROBE_DEFAULT = {"gate": 20, "stages": 3}


def budget_of(budget: dict[str, int], key: str) -> int:
    return int(budget.get(key, budget.get("stages", PROBE_DEFAULT["stages"]) if key != "gate" else PROBE_DEFAULT["gate"]))


def probe_context(task: Any, workdir: Path, part: str, budget: dict[str, int] | None,
                  proto: list[str] | None = None) -> str:
    """The turn's context file (a new log per turn); "" when probes are off."""
    if budget is None:
        return ""
    root = workdir / ".flux-probes"
    root.mkdir(exist_ok=True)
    n = 1 + sum(1 for _ in root.glob("turn-*.json"))
    ctx = root / f"turn-{n:03d}.json"
    ctx.write_text(json.dumps({"doc": task.to_dict(), "home": task.home or ".", "part": part,
                               "budget": dict(budget), "log": str(root / f"turn-{n:03d}.jsonl"),
                               "stages": [s.name for s in task.stages], "proto": proto or None}))
    return str(ctx)


def probe_line(stages: list[str], budget: dict[str, int] | None, proto: bool = False,
               allowed: tuple[str, ...] = ()) -> str:
    """The brief's sentence on probes."""
    if budget is None:
        return ""
    n_gate = budget_of(budget, "gate")
    what = "the prototype's check" if proto else "the gate"
    line = (f"You may check a file with the loop's own tools: `flux probe gate FILE` runs {what} on it "
            f"(up to {n_gate} times this turn)")
    if stages and not proto:
        each = ", ".join(f"`{s}` {budget_of(budget, s)}" for s in stages)
        line += (f"; `flux probe measure FILE --stage S [--stage T ...]` runs those measurements, each on its own "
                 f"and side by side, no gate first unless `--gate`, and says whether each meets its limits "
                 f"(up to: {each} times)")
    from .agent import DENIED

    if set(allowed) >= set(DENIED):
        return line + ". Each prints what the loop would see, with its exact commands and flags, and is on the record."
    raw = (f" (except {', '.join(allowed)}, which you may run yourself)" if allowed else "")
    return line + f". Each prints what the loop would see. Use them, not the raw tools, which are denied{raw}."


def probes_done(ctx_path: str) -> list[dict[str, Any]]:
    """The turn's probes, from its log, for the record."""
    if not ctx_path:
        return []
    try:
        log = Path(json.loads(Path(ctx_path).read_text())["log"])
        return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]
    except (OSError, ValueError, KeyError):
        return []


def _log(ctx: dict[str, Any], row: dict[str, Any]) -> None:
    with open(ctx["log"], "a") as fh:
        fh.write(json.dumps(row) + "\n")


def _used(ctx: dict[str, Any], key: str) -> int:
    try:
        return sum(1 for ln in Path(ctx["log"]).read_text().splitlines() if ln.strip() and json.loads(ln).get("key") == key)
    except OSError:
        return 0


def _setup(ctx: dict[str, Any], ctx_path: str, run: str):
    """A fresh problem and state for one probe run, in its own directory (runs go side by side)."""
    from .document import TaskSpec
    from .task import PromptProblem
    from .types import LoopRequest, LoopState

    task = TaskSpec.from_dict(ctx["doc"], base=ctx["home"])
    work = Path(ctx_path).parent / run
    work.mkdir(exist_ok=True)
    said: list[str] = []
    state = LoopState(request=LoopRequest(db=""), say=said.append, proposer=None, feedback=None, workdir=str(work))
    return task, PromptProblem(task), state, said


def _candidate(task: Any, text: str, name: str, part: str | None):
    from .types import Candidate

    return Candidate(f"probe-{name}", text, knobs={"task": task.id, "part": part or ""}, subgoal=part)


def _gate(ctx: dict[str, Any], ctx_path: str, src: Path, text: str, n: int) -> tuple[bool, str, str]:
    """(passed, report, one line for the log): the document's gate, through the loop's own build/judge."""
    from .types import BuildError

    task, problem, state, _said = _setup(ctx, ctx_path, f"run-gate-{n:03d}")
    part = ctx.get("part") or None
    cand = _candidate(task, text, src.stem, part)
    try:
        verdict = problem.judge(problem.build(cand, part, state), cand, part, state)
    except BuildError as exc:
        return False, f"GATE: did not build\n{exc}", f"did not build: {str(exc)[:300]}"
    if not verdict.ok:
        return False, f"GATE: {verdict.score:g} failures\n{verdict.why}", f"gate: {verdict.score:g} failures"
    return True, "GATE: passed", "GATE: passed"


def _stage(ctx: dict[str, Any], ctx_path: str, src: Path, text: str, stage: str, n: int
           ) -> tuple[bool, str, dict[str, float] | None]:
    """(measured and within its limits, report, metrics): one stage on its own, and its verdict
    against its cutoffs and the objectives' limits at it."""
    from .task import _metrics_in

    task, problem, state, said = _setup(ctx, ctx_path, f"run-{stage}-{n:03d}")
    spec = next(r for r in task.stages if r.name == stage)
    cand = _candidate(task, text, src.stem, ctx.get("part") or None)
    tail = ""
    if spec.command:                                  # run here, so a miss shows the tool's own output
        run = problem._run(spec.command, problem._subs(cand, None, state), spec.timeout_s, f"probe {stage}")
        output = (run.stdout or "") + "\n" + (run.stderr or "")
        got = _metrics_in(spec, output) or None
        tail = output.strip()[-3000:]
    else:
        got = problem.measure(cand, stage, state)
    if not got or "error" in got:
        return False, (f"{stage}: not measured" + (f" ({got['error']})" if got and "error" in got else "")
                       + ("\n" + "\n".join(said[-5:]) if said else "")
                       + (f"\nthe stage's output (its end):\n{tail}" if tail else "")), None
    lines = [f"{stage}: " + ", ".join(f"{k}={v:g}" for k, v in got.items())]
    from .estimate import failing

    rules = problem._estimate_rules(spec, state, [])
    miss = failing(got, rules, 0.0).replace("estimated ", "").replace(" by more than 0%", "") if rules else ""
    if rules:
        lines.append(f"  limits at {stage}: " + "; ".join(f"{m} {op} {x:g}" for m, op, x, _w in rules)
                     + (f" -- FAILS: {miss}" if miss else " -- met"))
    return not miss, "\n".join(lines), got


def probe(kind: str, file: str, stages: str | list[str] | None = None, ctx_path: str | None = None,
          gate_first: bool = False) -> tuple[int, str]:
    """(exit code, what to print): 0 passed / measured within its limits, 1 failed, 2 refused (no
    context, over budget, an unknown stage). `measure` runs each named stage on its own, side by
    side (no gate unless `gate_first`)."""
    ctx_path = ctx_path or os.environ.get("FLUX_PROBE", "")
    if not ctx_path or not Path(ctx_path).is_file():
        return 2, "flux probe runs inside a loop's agent turn (FLUX_PROBE names its context); there is none here"
    ctx = json.loads(Path(ctx_path).read_text())
    src = Path(file)
    if not src.is_file():
        return 2, f"no file {file}"
    text = src.read_text()
    sha = hashlib.sha256(text.encode()).hexdigest()[:12]
    known = list(ctx.get("stages") or [])

    def spent(key: str) -> str:
        return (f"the {key} probe budget of this turn is spent ({budget_of(ctx['budget'], key)}); write the file and "
                f"end your turn: the loop runs {key} on it and comes back with the result")

    def row(key: str, ok: bool, result: str, t0: float, **more: Any) -> dict[str, Any]:
        return {"key": key, "file": src.name, "sha": sha, "when": time.time(), "ok": ok, "result": result[:300],
                "seconds": round(time.monotonic() - t0, 1), **more}

    if kind == "measure" and ctx.get("proto"):
        return 2, "a prototype turn probes its check only: flux probe gate FILE"
    wanted: list[str] = []
    if kind == "measure":
        wanted = [stages] if isinstance(stages, str) else list(stages or [])
        wanted = list(dict.fromkeys(wanted or known[:1]))
        bad = [w for w in wanted if w not in known]
        if bad or not wanted:
            return 2, f"no stage {', '.join(map(repr, bad)) or 'to measure'}; this problem's stages: {', '.join(known) or 'none'}"
    out: list[str] = []
    code = 0
    if kind == "gate" or gate_first:
        used, cap = _used(ctx, "gate"), budget_of(ctx["budget"], "gate")
        if used >= cap:
            return 2, spent("gate")
        t0 = time.monotonic()
        if ctx.get("proto"):
            from flux_evaluator_abi.tools import run_tool

            cmd = [c.replace("{artifact}", str(src.resolve())) for c in ctx["proto"]]
            run = run_tool(cmd, cwd=str(src.resolve().parent), timeout_s=600, what="probe proto")
            text_out = ((run.stdout or "") + "\n" + (run.stderr or "")).strip()
            _log(ctx, row("gate", run.ok, text_out[-300:], t0))
            return (0 if run.ok else 1), text_out[-6000:] + f"\n[gate probe {used + 1} of {cap}]"
        ok, report, line = _gate(ctx, ctx_path, src, text, used + 1)
        _log(ctx, row("gate", ok, line, t0))
        out.append(report + f"\n[gate probe {used + 1} of {cap}]")
        if not ok:
            return 1, "\n".join(out)
    # each stage on its own budget, all side by side
    runs: list[tuple[str, int, int]] = []
    for w in wanted:
        used, cap = _used(ctx, w), budget_of(ctx["budget"], w)
        if used >= cap:
            out.append(spent(w))
            code = max(code, 2)
        else:
            runs.append((w, used + 1, cap))
    if runs:
        from concurrent.futures import ThreadPoolExecutor

        t0 = time.monotonic()
        from .pool import capped

        with ThreadPoolExecutor(max_workers=capped(len(runs))) as pool:          # D740
            done = list(pool.map(lambda r: _stage(ctx, ctx_path, src, text, r[0], r[1]), runs))
        for (w, n, cap), (ok, report, got) in zip(runs, done):
            _log(ctx, row(w, ok, report.splitlines()[-1] if ok else report.splitlines()[0], t0,
                          **({"metrics": got} if got else {})))
            out.append(report + f"\n[{w} probe {n} of {cap}]")
            code = max(code, 0 if ok else 1)
        try:
            from .document import TaskSpec
            from .task import PromptProblem

            out.append("objectives: " + PromptProblem(TaskSpec.from_dict(ctx["doc"], base=ctx["home"])).objectives().describe())
        except Exception:  # noqa: BLE001 -- the numbers stand without it
            pass
    return code, "\n".join(out)
