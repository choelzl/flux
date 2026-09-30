"""`flux probe` (D678): a coding agent checks its file with the loop's own tools, mid-turn.

The loop writes a context file into the agent's work directory for each turn (the document,
the part, the budget, the log) and names it in `FLUX_PROBE`. `flux probe gate FILE` runs the
document's gate on FILE, `flux probe measure FILE --stage S` the gate and then stage S, through
the same `PromptProblem` the loop runs: the same commands, flags and metric parsing. A
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
        line += f"; `flux probe measure FILE --stage S` runs the gate, then stage S (up to: {each} times)"
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


def probe(kind: str, file: str, stage: str | None = None, ctx_path: str | None = None) -> tuple[int, str]:
    """(exit code, what to print): 0 passed / measured, 1 failed, 2 refused (no context, over
    budget, an unknown stage)."""
    ctx_path = ctx_path or os.environ.get("FLUX_PROBE", "")
    if not ctx_path or not Path(ctx_path).is_file():
        return 2, "flux probe runs inside a loop's agent turn (FLUX_PROBE names its context); there is none here"
    ctx = json.loads(Path(ctx_path).read_text())
    src = Path(file)
    if not src.is_file():
        return 2, f"no file {file}"
    text = src.read_text()
    stages = list(ctx.get("stages") or [])
    if kind == "measure":
        if ctx.get("proto"):
            return 2, "a prototype turn probes its check only: flux probe gate FILE"
        stage = stage or (stages[0] if stages else None)
        if stage not in stages:
            return 2, f"no stage {stage!r}; this problem's stages: {', '.join(stages) or 'none'}"
    key = "gate" if kind == "gate" else str(stage)
    cap = budget_of(ctx["budget"], key)
    used = _used(ctx, key)
    if used >= cap:
        return 2, (f"the {key} probe budget of this turn is spent ({cap}); write the file and end your turn: "
                   f"the loop runs {key} on it and comes back with the result")
    t0 = time.monotonic()
    row: dict[str, Any] = {"key": key, "file": src.name, "sha": hashlib.sha256(text.encode()).hexdigest()[:12],
                           "when": time.time()}
    if ctx.get("proto"):
        from flux_evaluator_abi.tools import run_tool

        cmd = [c.replace("{artifact}", str(src.resolve())) for c in ctx["proto"]]
        run = run_tool(cmd, cwd=str(src.resolve().parent), timeout_s=600, what="probe proto")
        out = ((run.stdout or "") + "\n" + (run.stderr or "")).strip()
        row.update(ok=run.ok, result=out[-300:], seconds=round(time.monotonic() - t0, 1))
        _log(ctx, row)
        return (0 if run.ok else 1), out[-6000:] + f"\n[probe {used + 1} of {cap}]"
    from .document import TaskSpec
    from .task import PromptProblem
    from .types import BuildError, Candidate, LoopRequest, LoopState

    task = TaskSpec.from_dict(ctx["doc"], base=ctx["home"])
    problem = PromptProblem(task)
    said: list[str] = []
    work = Path(ctx_path).parent / f"run-{key}-{used + 1:03d}"
    work.mkdir(exist_ok=True)
    state = LoopState(request=LoopRequest(db=""), say=said.append, proposer=None, feedback=None, workdir=str(work))
    part = ctx.get("part") or None
    cand = Candidate(f"probe-{src.stem}", text, knobs={"task": task.id, "part": part or ""}, subgoal=part)
    lines = []
    try:
        built = problem.build(cand, part, state)
        verdict = problem.judge(built, cand, part, state)
    except BuildError as exc:
        row.update(ok=False, result=f"did not build: {str(exc)[:300]}", seconds=round(time.monotonic() - t0, 1))
        _log(ctx, row)
        return 1, f"GATE: did not build\n{exc}\n[probe {used + 1} of {cap}]"
    if not verdict.ok:
        row.update(ok=False, result=f"gate: {verdict.score:g} failures", seconds=round(time.monotonic() - t0, 1))
        _log(ctx, row)
        return 1, f"GATE: {verdict.score:g} failures\n{verdict.why}\n[probe {used + 1} of {cap}]"
    lines.append("GATE: passed")
    code = 0
    if kind == "measure":
        spec = next(r for r in task.stages if r.name == stage)
        tail = ""
        if spec.command:                              # run here, so a miss shows the tool's own output
            from .task import _metrics_in

            run = problem._run(spec.command, problem._subs(cand, None, state), spec.timeout_s, f"probe {stage}")
            output = (run.stdout or "") + "\n" + (run.stderr or "")
            got = _metrics_in(spec, output) or None
            tail = output.strip()[-3000:]
        else:
            got = problem.measure(cand, str(stage), state)
        if not got or "error" in got:
            code = 1
            lines.append(f"{stage}: not measured" + (f" ({got['error']})" if got and "error" in got else "")
                         + ("\n" + "\n".join(said[-5:]) if said else "")
                         + (f"\nthe stage's output (its end):\n{tail}" if tail else ""))
        else:
            lines.append(f"{stage}: " + ", ".join(f"{k}={v:g}" for k, v in got.items()))
            try:
                lines.append("objectives: " + problem.objectives().describe())
            except Exception:  # noqa: BLE001 -- the numbers stand without it
                pass
            row["metrics"] = got
    row.update(ok=code == 0, result=lines[-1][:300], seconds=round(time.monotonic() - t0, 1))
    _log(ctx, row)
    return code, "\n".join(lines) + f"\n[probe {used + 1} of {cap}]"
