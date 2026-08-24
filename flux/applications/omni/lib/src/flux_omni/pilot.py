"""The omni loop: prompt in, plan out, tools run, conclusion grounded in what actually ran.

Round shape (docs/decisions.md D377): the model sees the task, the introspected catalog,
compact summaries of every executed step, and verbatim refusals from its last proposal;
it answers with JSON steps and/or `done` + a conclusion. Steps are validated before
anything runs (`plan.validate_step`), executed sequentially, and every outcome -- success,
tool exception, refusal -- is recorded and fed back. The model plans; it never touches
results: numbers come from the tools or not at all, the same boundary every other Flux
loop draws (D297's "measured, not remembered" applied to orchestration).

Model-free replay is not a degraded mode but the same executor: the provenance file each
run writes (`omni_run.json`) is itself a loadable plan -- `params.plan_file` runs it through
the same gate and executor with no model asked, and `run_plan()` runs one outside the loop.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalog import ToolSpec, build_catalog  # noqa: F401 -- the world looks `build_catalog` up here
from .plan import Refusal, Step, resolve_refs, validate_step

from flux_llm import Proposer

_SUMMARY_CHARS = 1600  # per-step result summary budget in the model's context


@dataclass(frozen=True, slots=True)
class StepOutcome:
    step: Step
    ok: bool
    result: Any = None          # JSON-safe (the MCP wrappers guarantee it) or None
    error: str | None = None
    elapsed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.step.to_dict(), "ok": self.ok, "result": self.result,
                "error": self.error, "elapsed_s": self.elapsed_s}


@dataclass(frozen=True, slots=True)
class OmniReport:
    prompt: str
    outcomes: tuple[StepOutcome, ...]
    refusals: tuple[Refusal, ...]
    conclusion: str
    done: bool                   # model said done (or replay finished); False = budget stop
    rounds: int
    llm_calls: int
    wall_clock_s: float
    provenance_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "refusals": [{"step_index": r.step_index, "tool": r.tool, "reason": r.reason}
                         for r in self.refusals],
            "conclusion": self.conclusion,
            "done": self.done,
            "rounds": self.rounds,
            "llm_calls": self.llm_calls,
            "wall_clock_s": self.wall_clock_s,
            "provenance_path": self.provenance_path,
        }


def summarize(value: Any, budget: int = _SUMMARY_CHARS) -> str:
    """A result as the model will see it: full structure, long leaves truncated, then the
    whole rendering capped. Lossy on purpose -- the full result lives in provenance."""

    def trim(node: Any, depth: int = 0) -> Any:
        if isinstance(node, str):
            return node if len(node) <= 200 else node[:200] + f"...({len(node)} chars)"
        if isinstance(node, dict):
            return {k: trim(v, depth + 1) for k, v in node.items()}
        if isinstance(node, list):
            if len(node) > 8:
                return [trim(v, depth + 1) for v in node[:8]] + [f"...({len(node)} items)"]
            return [trim(v, depth + 1) for v in node]
        return node

    text = json.dumps(trim(value), default=str)
    return text if len(text) <= budget else text[:budget] + f"...({len(text)} chars)"


def _execute(step: Step, catalog: dict[str, ToolSpec], bindings: dict[str, Any],
             workdir: Path, log: list[str]) -> StepOutcome:
    t0 = time.monotonic()
    try:
        args = resolve_refs(step.args, bindings)
        if step.tool == "write_file":
            target = workdir / args["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(args["text"])
            result: Any = {"written": str(target), "chars": len(args["text"])}
        elif step.tool == "load_ir":
            import flux_ir

            rel = Path(args["path"])
            flux_root = Path(__file__).resolve().parents[5]
            for root in (workdir, flux_root):
                if (root / rel).is_file():
                    result = flux_ir.load_document(root / rel)
                    break
            else:
                raise FileNotFoundError(
                    f"{rel} not found under the run workdir or {flux_root}")
        elif step.tool == "describe":
            name = args["tool"]
            result = {"tool": name,
                      "detail": catalog[name].render() if name in catalog
                      else f"{name} is a meta-tool; see the rules section"}
        elif step.tool == "note":
            log.append(str(args.get("text", "")))
            result = {"noted": True}
        else:
            from flux_profile import phase as _tphase

            compact = {k: v for k, v in args.items()
                       if isinstance(v, (int, float, bool, str)) and
                       (not isinstance(v, str) or len(v) <= 60)}
            with _tphase(f"omni: {step.tool}",
                         why=f"-> ${step.bind}" if step.bind else "", **compact):
                result = catalog[step.tool].fn(**args)
    except Exception as exc:  # noqa: BLE001 -- a tool crash is an outcome, not a loop crash
        return StepOutcome(step=step, ok=False, error=f"{type(exc).__name__}: {exc}",
                           elapsed_s=time.monotonic() - t0)
    outcome = StepOutcome(step=step, ok=True, result=result,
                          elapsed_s=time.monotonic() - t0)
    if step.bind:
        bindings[step.bind] = result
    return outcome


def run_plan(
    steps: tuple[Step, ...],
    catalog: dict[str, ToolSpec],
    workdir: str | Path,
) -> tuple[list[StepOutcome], list[Refusal]]:
    """The model-free executor: validate everything first (a canned plan with a typo
    should refuse before running half of itself), then run in order. A step failure does
    not stop the plan -- later steps not referencing its bind still run; ones that do
    reference it fail at resolution and say so."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    refusals: list[Refusal] = []
    bound: set[str] = set()
    for i, step in enumerate(steps):
        refusal = validate_step(i, step, catalog, bound, workdir)
        if refusal is not None:
            refusals.append(refusal)
        elif step.bind:
            bound.add(step.bind)
    if refusals:
        return [], refusals
    bindings: dict[str, Any] = {}
    log: list[str] = []
    return [_execute(s, catalog, bindings, workdir, log) for s in steps], []


_PROPOSAL_SHAPE = """Respond with ONLY a JSON object, no prose:
{"steps": [{"tool": "<name>", "args": {...}, "bind": "<optional name>"}],
 "done": false, "conclusion": ""}

Rules:
- Use tools from the catalog exactly as specified; arguments must match the listed names.
- Bind a step's result with "bind" and reference it in later args as "$name.field.sub"
  or "$name.items[0]" (strings only, exact syntax).
- The catalog lists one signature line per tool. Before first use of a tool whose
  arguments you are not sure of, call the meta-tool describe(tool) -- it returns the
  full parameter documentation. Guessing argument shapes wastes a round on a refusal.
- Never repeat a step whose result is already shown above; repeated identical steps are
  refused. Read the shown results instead.
- Tools named agentic_* and generate_* run their own inner LLM loops (slow, minutes);
  prefer plain evaluate/search for direct questions unless the task asks for those.
- Meta-tools also exist: write_file(path, text) writes a file under the run directory
  (relative paths only); load_ir(path) loads a Flux IR YAML document (run directory or a
  bundled example like core/ir/workload/examples/mlp-gemm0.yaml) so you can pass it as a
  "$bind"; describe(tool) as above; note(text) records a remark.
- Propose at most {max_steps} steps per round. Prefer few, decisive steps: evaluate or
  search first, read the numbers, then decide.
- When the task is answered, set "done": true and write a conclusion that cites the
  executed results (their actual numbers), not expectations."""


def _record_context(records) -> str:
    """What earlier runs of this same prompt concluded -- the flywheel's read-back
    half (D401). Omni's record unit is the run, so what compounds is conclusions:
    a resumed prompt starts from its own last verdict instead of from zero."""
    if records is None or not getattr(records, "resumed", False):
        return ""
    lines = []
    for c in records.conclusions(limit=2):
        text = str(c.get("conclusion", "")).strip()
        if text:
            lines.append(text if len(text) <= 500 else text[:500] + "...")
    if not lines:
        return ""
    return ("## What an earlier run of this exact task concluded (its numbers were "
            "measured then; re-verify anything you rely on)\n" + "\n---\n".join(lines))


def _round_prompt(prompt: str, catalog_text: str, outcomes: list[StepOutcome],
                  refusals: list[Refusal], max_steps: int,
                  round_no: int, max_rounds: int, human: str | None = None,
                  record_ctx: str = "") -> str:
    header = f"Round {round_no} of {max_rounds}."
    if round_no == max_rounds:
        header += (" This is the LAST round: no further steps will run, so set"
                   ' "done": true and conclude from the results above.')
    parts = [
        "You are the pilot of Flux, an accelerator design-space-exploration toolkit.",
        "You plan tool calls; the harness executes them and shows you real results.",
        header,
        _PROPOSAL_SHAPE.replace("{max_steps}", str(max_steps)),
        "## Tool catalog\n" + catalog_text,
        "## Task\n" + prompt,
    ]
    if record_ctx:
        parts.append(record_ctx)
    if human:
        parts.append(human)
    if outcomes:
        lines = []
        for i, o in enumerate(outcomes):
            head = f"[{i}] {o.step.tool}" + (f" -> ${o.step.bind}" if o.step.bind else "")
            body = summarize(o.result) if o.ok else f"ERROR: {o.error}"
            lines.append(f"{head} ({o.elapsed_s:.1f}s)\n{body}")
        parts.append("## Executed so far\n" + "\n".join(lines))
    else:
        parts.append("## Executed so far\n(nothing yet)")
    if refusals:
        parts.append("## Your last proposal was partly refused -- repair these\n"
                     + "\n".join(r.render() for r in refusals))
    return "\n\n".join(parts)


def campaign_for(prompt: str, tools: list[str] | None = None) -> dict[str, Any]:
    """The `campaign:` block a run of `prompt` opens its record under (D401): named by the
    prompt's digest, so the same task resumes its own campaign and reads back what it last
    concluded, and a different prompt starts blind. `Records(db, objective=identity, name=
    block["name"])` reopens it; the identity is this block minus `name`, with `study: omni`."""
    import hashlib

    key = hashlib.sha256((prompt + "|" + ",".join(sorted(tools or []))).encode()).hexdigest()[:12]
    return {"name": f"omni-{key}", "prompt": prompt, "tools": sorted(tools or [])}


def run_omni(
    prompt: str,
    proposer: Proposer | None,
    *,
    workdir: str | Path,
    tools: list[str] | None = None,
    max_rounds: int = 6,
    max_calls: int = 16,
    max_steps_per_round: int = 4,
    wall_clock_budget_s: float | None = None,
    feedback: Any | None = None,
    db_path: str | None = None,
    plan_file: str | None = None,
) -> OmniReport:
    """One omni run, ON THE LOOP from its DOCUMENT (D458, review 2 step R3): the document
    `applications/omni/omni.problem.yaml` with this call's ask as its `params:`, its campaign
    keyed by the prompt (D401), run by `flux_loop.run_loop`; `world.World.search` is the round
    loop, the gate is `validate_step`, running a tool is the measurement.

    Stops when the model says done, or on any budget (rounds, executed tool calls, wall
    clock) -- a budget stop reports `done=False` and whatever conclusion text the model
    last offered, never a fabricated one."""
    import yaml
    from flux_loop import PromptProblem, TaskSpec, request_for, run_loop

    doc_path = Path(__file__).resolve().parents[3] / "omni.problem.yaml"   # applications/omni/
    doc = yaml.safe_load(doc_path.read_text())
    doc["params"] = {**doc["params"], "prompt": prompt, "tools": list(tools) if tools else None,
                     "max_calls": int(max_calls), "max_steps_per_round": int(max_steps_per_round),
                     "wall_clock_budget_s": wall_clock_budget_s, "workdir": str(workdir),
                     "plan_file": plan_file}
    doc["campaign"] = campaign_for(prompt, tools)
    prob = PromptProblem(TaskSpec.from_dict(doc, base=doc_path.parent))
    request = request_for(prob.task, db=db_path or "", steps=int(max_rounds))
    out = run_loop(prob, request, proposer=proposer, feedback=feedback)
    return prob.world.omni_report(out)
