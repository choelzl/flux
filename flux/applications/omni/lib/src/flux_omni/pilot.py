"""The omni loop: prompt in, plan out, tools run, conclusion grounded in what actually ran.

Round shape (docs/decisions.md D377): the model sees the task, the introspected catalog,
compact summaries of every executed step, and verbatim refusals from its last proposal;
it answers with JSON steps and/or `done` + a conclusion. Steps are validated before
anything runs (`plan.validate_step`), executed sequentially, and every outcome -- success,
tool exception, refusal -- is recorded and fed back. The model plans; it never touches
results: numbers come from the tools or not at all, the same boundary every other Flux
loop draws (D297's "measured, not remembered" applied to orchestration).

Model-free replay is not a degraded mode but the same executor: `run_plan()` is what
`run_omni()` calls internally, and the provenance file each run writes (`omni_run.json`)
is itself a loadable plan -- any model-authored run replays deterministically without the
model, which is the demo's without-a-model contract.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .catalog import ToolSpec, build_catalog, render_catalog
from .plan import Proposal, Refusal, Step, parse_proposal, resolve_refs, validate_step

_SUMMARY_CHARS = 1600  # per-step result summary budget in the model's context


class LLMProposer(Protocol):
    def propose(self, prompt: str) -> str: ...


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


def run_omni(
    prompt: str,
    proposer: LLMProposer,
    *,
    workdir: str | Path,
    tools: list[str] | None = None,
    max_rounds: int = 6,
    max_calls: int = 16,
    max_steps_per_round: int = 4,
    wall_clock_budget_s: float | None = None,
    feedback: Any | None = None,
    db_path: str | None = None,
) -> OmniReport:
    """The loop. Stops when the model says done, or on any budget (rounds, executed tool
    calls, wall clock) -- a budget stop reports `done=False` and whatever conclusion text
    the model last offered, never a fabricated one."""
    t0 = time.monotonic()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    catalog = build_catalog(tools)
    catalog_text = render_catalog(catalog)

    # The rim (D401): the campaign record is keyed by the PROMPT, so re-running the
    # same task resumes its campaign and round 1 reads back the last conclusion; the
    # operator's typed notes drain at round boundaries into the next prompt. Both are
    # optional and both fail into nothing rather than into a failed run.
    records = None
    if db_path:
        from flux_records import Records

        records = Records(db_path, objective={"study": "omni", "prompt": prompt,
                                              "tools": sorted(tools or [])}, log=print)
    record_ctx = _record_context(records)
    from flux_feedback import reload_notes

    human_notes: list[Any] = reload_notes(records, say=print)

    def _on_note(n: Any) -> None:
        if records is not None:
            records.note(n.text)
        log.append(f"[operator] {n.text}")

    outcomes: list[StepOutcome] = []
    all_refusals: list[Refusal] = []
    last_refusals: list[Refusal] = []
    bindings: dict[str, Any] = {}
    log: list[str] = []
    conclusion, done, llm_calls, rounds = "", False, 0, 0
    raw_replies: list[str] = []

    budget_spent = False
    for rounds in range(1, max_rounds + 1):
        if wall_clock_budget_s is not None and time.monotonic() - t0 > wall_clock_budget_s:
            budget_spent = True
            break
        from flux_feedback import drain_guidance

        human = drain_guidance(feedback, human_notes, on_note=_on_note)
        raw = proposer.propose(_round_prompt(
            prompt, catalog_text, outcomes, last_refusals, max_steps_per_round,
            rounds, max_rounds, human=human, record_ctx=record_ctx))
        llm_calls += 1
        raw_replies.append(raw)
        proposal: Proposal = parse_proposal(raw)
        last_refusals = []
        if proposal.parse_error is not None:
            last_refusals = [Refusal(-1, "(reply)", proposal.parse_error)]
            all_refusals.extend(last_refusals)
            continue
        bound = set(bindings)
        for i, step in enumerate(proposal.steps[:max_steps_per_round]):
            refusal = validate_step(i, step, catalog, bound, workdir)
            if refusal is None:
                # Dithering guard (D377, observed on the first live run: the model
                # described the same tool four times): an exact repeat of an executed
                # step re-measures nothing -- point back at the result it already has.
                for j, prior in enumerate(outcomes):
                    if prior.step.tool == step.tool and prior.step.args == step.args:
                        refusal = Refusal(
                            i, step.tool,
                            f"identical to executed step [{j}]; its result is shown "
                            "above -- read it instead of re-running")
                        break
            if refusal is not None:
                last_refusals.append(refusal)
                continue
            if len(outcomes) >= max_calls:
                break
            outcome = _execute(step, catalog, bindings, workdir, log)
            outcomes.append(outcome)
            if outcome.ok and step.bind:
                bound.add(step.bind)
        all_refusals.extend(last_refusals)
        if proposal.conclusion:
            conclusion = proposal.conclusion
        if proposal.done and not last_refusals:
            done = True
            break
        if len(outcomes) >= max_calls:
            break

    # A budget stop with evidence on the table still deserves a verdict: one final
    # conclude-only call (no steps will run) instead of ending on silence. The report
    # stays done=False -- the model concluded under duress, and the caller should know.
    if budget_spent and not done and outcomes and not conclusion:
        raw = proposer.propose(
            "The tool budget is exhausted; no more steps will run. Based ONLY on the "
            "executed results below, answer the task in 2-4 sentences citing actual "
            "numbers. Respond with ONLY the answer text.\n\n## Task\n" + prompt
            + "\n\n## Executed results\n" + "\n".join(
                f"[{i}] {o.step.tool}: " + (summarize(o.result) if o.ok else f"ERROR {o.error}")
                for i, o in enumerate(outcomes)))
        llm_calls += 1
        raw_replies.append(raw)
        conclusion = raw.strip()

    # Final drain (a note typed while the last round ran is still recorded), then the
    # run lands in the record: each executed step a trial, each refusal a refused
    # trial, the conclusion an INFERENCE event the next run of this prompt reads back.
    from flux_feedback import drain_guidance

    drain_guidance(feedback, human_notes, on_note=_on_note)
    if records is not None:
        for i, o in enumerate(outcomes):
            records.trial({"tool": o.step.tool, "args": summarize(o.step.args, 300)},
                          f"step{i}:{o.step.tool}", rung="executed", strategy="llm-plan",
                          metrics={"elapsed_s": o.elapsed_s} if o.ok else None,
                          error=o.error, wall_s=o.elapsed_s, analytic=False,
                          evaluator="omni@tool")
        for r in all_refusals:
            records.trial({"refused": r.render()}, f"refused:{r.step_index}:{r.tool}",
                          rung="gate", strategy="llm-plan", metrics=None,
                          error=r.reason)
        if conclusion:
            records.conclude({"conclusion": conclusion, "done": done,
                              "rounds": rounds, "llm_calls": llm_calls})

    provenance = workdir / "omni_run.json"
    provenance.write_text(json.dumps({
        "prompt": prompt,
        "tools_offered": sorted(catalog),
        "executed_plan": [o.step.to_dict() for o in outcomes],
        "outcomes": [o.to_dict() for o in outcomes],
        "refusals": [r.render() for r in all_refusals],
        "raw_replies": raw_replies,
        "notes": log,
        "conclusion": conclusion,
        "done": done,
    }, indent=2, default=str))

    return OmniReport(
        prompt=prompt, outcomes=tuple(outcomes), refusals=tuple(all_refusals),
        conclusion=conclusion, done=done, rounds=rounds, llm_calls=llm_calls,
        wall_clock_s=time.monotonic() - t0, provenance_path=str(provenance),
    )
