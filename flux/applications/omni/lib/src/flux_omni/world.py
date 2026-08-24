"""omni's WORLD (review 2 step R3, docs/decisions.md D539; the shape of D519 and D533): what
`applications/omni/omni.problem.yaml` names once as its `world:` -- the round shape of D377
on the one loop (D458).

A round is a BATCH of steps the model proposes (D446/D457), `plan.validate_step` is the
gate, running a tool is the measurement on the one stage, and the answer is a conclusion in
words rather than a decided design. What stays here is what makes omni omni: the prompt, the
catalog, the dithering guard, the conclude-only round when a budget stops the run, and the
provenance file that is itself a replayable plan. The loop owns the record and its trials,
the operator-feedback drain, the timing tree, the live panels, the step budget and the
refusal bookkeeping.

What the DOCUMENT says: `params.prompt` (the task), `params.tools` (the catalog offered),
the budgets (`max_calls`, `max_steps_per_round`, `wall_clock_budget_s`), `workdir`,
`plan_file` (a saved plan run through the same gate and executor with no model asked), the
one `run` stage, `cache: false`, `budget.steps` (the rounds) and the campaign -- keyed by
the prompt when the node builds the document for a caller (D401).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator

from flux_loop import Candidate, LoopState, Scored, Verdict

from . import pilot                      # looked up through the module, so a test can stub the catalog
from .catalog import ToolSpec, render_catalog
from .pilot import OmniReport, StepOutcome, _execute, _record_context, _round_prompt, summarize
from .plan import Refusal, Step, load_plan_file, parse_proposal, validate_step

STAGE = "run"


class World:
    """One omni run as the hooks of the document problem that runs it: built from the
    document's `params:`; `objective`, `stages` and `cache_suffix` are the document's."""

    name = "omni"
    PARAMS = ("prompt", "tools", "max_calls", "max_steps_per_round", "wall_clock_budget_s",
              "workdir", "plan_file")

    def __init__(self, problem: Any) -> None:
        self.problem = problem
        p = dict(problem.task.params or {})
        unknown = sorted(set(p) - set(self.PARAMS))
        if unknown:
            raise ValueError(f"params {unknown} are not omni's; known: {', '.join(self.PARAMS)}")
        self.prompt = str(p.get("prompt") or "").strip()
        if not self.prompt and not p.get("plan_file"):
            raise ValueError("params.prompt is the task; omni has nothing to plan without one")
        tools = p.get("tools")
        self.tools = sorted(str(t) for t in tools) if tools else []
        self.max_calls = int(p.get("max_calls") or 16)
        self.max_steps_per_round = int(p.get("max_steps_per_round") or 4)
        budget = p.get("wall_clock_budget_s")
        self.wall_clock_budget_s = float(budget) if budget is not None else None
        self.workdir_param = str(p["workdir"]) if p.get("workdir") else None
        self.plan_file = str(p["plan_file"]) if p.get("plan_file") else None
        self.catalog: dict[str, ToolSpec] = {}
        self.catalog_text = ""
        self.workdir = Path(self.workdir_param or ".")
        self.started = time.monotonic()
        #: what actually ran, in order -- the model sees these, and so does the report
        self.outcomes: list[StepOutcome] = []
        self.all_refusals: list[Refusal] = []
        self.round_refusals: list[Refusal] = []
        self.bindings: dict[str, Any] = {}
        self.notes: list[str] = []
        self.raw_replies: list[str] = []
        self.conclusion_text = ""
        self.done = False
        self.budget_spent = False
        self.rounds = 0
        self.llm_calls = 0
        self.provenance_path = ""
        self._records: Any = None
        self._bound: set[str] = set()
        self._admitted_now: list[Step] = []
        self._steps: dict[str, Step] = {}

    # ------------------------------------------------------------------ mentor
    def open_records(self, request: Any, say: Any) -> Any:
        """The campaign record the loop opened, kept so the run can write its conclusion to
        it: omni's conclusion is TEXT, not a decided design, and the loop writes one only
        when there is a pick."""
        from flux_loop import Problem

        self._records = Problem.open_records(self.problem, request, say)
        return self._records

    @property
    def records(self) -> Any:
        return self._records

    def prepare(self, state: LoopState) -> dict[str, Any]:
        self.started = time.monotonic()
        self.catalog = pilot.build_catalog(self.tools or None)
        self.catalog_text = render_catalog(self.catalog)
        self.workdir = Path(self.workdir_param or getattr(state, "workdir", None) or ".")
        self.workdir.mkdir(parents=True, exist_ok=True)
        out = {"tools offered": len(self.catalog), "workdir": str(self.workdir)}
        if self.plan_file:
            out["plan"] = self.plan_file
        return out

    def mentor_sections(self, state: LoopState) -> list[tuple[str, str]]:
        return [("task", self.prompt), ("tool catalog", self.catalog_text)]

    # ------------------------------------------------------------ orchestration
    def search(self, state: LoopState) -> Iterator[list[Candidate]]:
        """One round per step: the model plans, the loop gates and runs, the results come
        back as the value of the `yield` and become the next round's context. A saved plan
        (`params.plan_file`) is one batch through the same gate and executor, no model asked."""
        yield from self._rounds(state)
        self._finish(state)

    def _rounds(self, state: LoopState) -> Iterator[list[Candidate]]:
        say = state.say
        if self.plan_file:
            steps = load_plan_file(self.plan_file)
            say(f"  replaying {len(steps)} step(s) from {self.plan_file}; the model is not asked")
            yield [self._candidate(i, s) for i, s in enumerate(steps)]
            self.done = True
            return
        record_ctx = _record_context(self._records)
        max_rounds = max(1, state.request.steps)
        for round_no in range(1, max_rounds + 1):
            if self._out_of_time():
                self.budget_spent = True
                say("  the wall-clock budget is spent; no further rounds")
                return
            self.rounds = round_no
            human = state.drain()
            prompt = _round_prompt(self.prompt, self.catalog_text, self.outcomes,
                                  self.round_refusals, self.max_steps_per_round,
                                  round_no, max_rounds, human=human, record_ctx=record_ctx)
            raw = self._ask(state, prompt)
            self.raw_replies.append(raw)
            self.llm_calls += 1
            proposal = parse_proposal(raw)
            self.round_refusals = []
            if proposal.parse_error is not None:
                self._refuse(state, Refusal(-1, "(reply)", proposal.parse_error))
                yield []                          # nothing to run; the model is told why
                continue
            if proposal.conclusion:
                self.conclusion_text = proposal.conclusion
            room = max(0, self.max_calls - len(self.outcomes))
            steps = list(proposal.steps[:self.max_steps_per_round])[:room]
            if proposal.done and not steps:
                self.done = True
                return
            yield [self._candidate(i, s) for i, s in enumerate(steps)]
            if proposal.done and not self.round_refusals:
                self.done = True
                return
            if len(self.outcomes) >= self.max_calls:
                say(f"  the tool budget is spent ({self.max_calls} call(s))")
                self.budget_spent = True
                return
        if not self.done:
            self.budget_spent = True

    def _finish(self, state: LoopState) -> None:
        """The run's end: a budget stop with evidence on the table still deserves a verdict
        (one conclude-only call, never silence; the report stays done=False), the conclusion
        on the record for the next run of this prompt (D401), and the provenance file that
        is itself a replayable plan."""
        if (self.budget_spent and not self.done and self.outcomes and not self.conclusion_text
                and state.proposer is not None):
            raw = state.proposer.propose(
                "The tool budget is exhausted; no more steps will run. Based ONLY on the "
                "executed results below, answer the task in 2-4 sentences citing actual "
                "numbers. Respond with ONLY the answer text.\n\n## Task\n" + self.prompt
                + "\n\n## Executed results\n" + "\n".join(
                    f"[{i}] {o.step.tool}: " + (summarize(o.result) if o.ok else f"ERROR {o.error}")
                    for i, o in enumerate(self.outcomes))).text
            self.llm_calls += 1
            self.raw_replies.append(raw)
            self.conclusion_text = raw.strip()
        if self._records is not None and self.conclusion_text:
            try:
                self._records.conclude({"conclusion": self.conclusion_text, "done": self.done,
                                        "rounds": self.rounds, "llm_calls": self.llm_calls})
            except Exception:  # noqa: BLE001 -- a record that refuses must not fail the run
                pass
        provenance = self.workdir / "omni_run.json"
        provenance.write_text(json.dumps({
            "prompt": self.prompt,
            "tools_offered": sorted(self.catalog),
            "executed_plan": [o.step.to_dict() for o in self.outcomes],
            "outcomes": [o.to_dict() for o in self.outcomes],
            "refusals": [r.render() for r in self.all_refusals],
            "raw_replies": self.raw_replies,
            "notes": self.notes,
            "conclusion": self.conclusion_text,
            "done": self.done,
        }, indent=2, default=str))
        self.provenance_path = str(provenance)
        if self.conclusion_text:
            state.lessons.append(f"[{STAGE}] omni concludes: {self.conclusion_text[:300]}")

    def next_work(self, state: LoopState, waiting: list[str]) -> str:
        return "batch"                            # omni has no parts, only rounds of steps

    # ------------------------------------------------------------------- gate
    def build(self, cand: Candidate, subgoal: str | None, state: LoopState) -> Step:
        """Nothing to compile: a step IS the candidate. Argument references are resolved
        when it runs, against what the earlier steps actually bound."""
        return self._steps[cand.name]

    def judge(self, built: Step, cand: Candidate, subgoal: str | None,
              state: LoopState) -> Verdict:
        """The gate (`plan.validate_step`), plus the dithering guard: an exact repeat of an
        executed step re-measures nothing, so point the model back at the result it has
        (D377, observed on the first live run)."""
        index = int(cand.meta["index"])
        if index == 0:                      # a new round: the gate sees the batch in order
            self._bound = set(self.bindings)
            self._admitted_now = []
        refusal = validate_step(index, built, self.catalog, self._bound, self.workdir)
        if refusal is None:
            for j, prior in self._already():
                if prior.tool == built.tool and prior.args == built.args:
                    refusal = Refusal(index, built.tool,
                                      f"identical to executed step [{j}]; its result is "
                                      "shown above -- read it instead of re-running")
                    break
        if refusal is not None:
            self._refuse(state, refusal)
            return Verdict(False, 1.0, refusal.render())
        if built.bind:
            self._bound.add(built.bind)
        self._admitted_now.append(built)
        return Verdict(True, 0.0)

    def _already(self) -> list[tuple[int, Step]]:
        """Every step the model will see a result for, with the index it will see it under:
        what has run, then what this round's gate has already admitted -- the loop gates a
        whole round before running any of it, and a duplicate inside one round is still a
        duplicate."""
        ran = [(j, o.step) for j, o in enumerate(self.outcomes)]
        return ran + [(len(self.outcomes) + k, st) for k, st in enumerate(self._admitted_now)]

    def describe_failure(self, subgoal: str | None, verdict: Verdict) -> str:
        return verdict.why

    # -------------------------------------------------------------- the stage
    def measure(self, cand: Candidate, stage: str, state: LoopState) -> dict[str, Any]:
        """Running the tool IS the measurement. A tool that crashes is an outcome, not a
        loop crash: the error is what the model reads next round."""
        outcome = _execute(self._steps[cand.name], self.catalog, self.bindings,
                           self.workdir, self.notes)
        self.outcomes.append(outcome)
        if not outcome.ok:
            return {"error": outcome.error or "the tool raised"}
        return {"elapsed_s": outcome.elapsed_s, "result": summarize(outcome.result, 300)}

    def evaluator_name(self, stage: str) -> str:
        return "omni@tool"

    # ----------------------------------------------------------- the decision
    def frontier(self, scored: list[Scored], state: LoopState) -> list[Scored]:
        return []

    def frontier_axes(self) -> None:
        return None

    def decide(self, pool: list[Scored], state: LoopState) -> tuple[Scored | None, str]:
        """omni answers in words: the conclusion the model wrote from what ran. There is no
        design to decide between, so there is no decision -- saying otherwise would put a
        tool call in the standings as if it were an answer."""
        return None, "omni concludes in words, not in a decided design"

    # ------------------------------------------------------------------ inner
    def _ask(self, state: LoopState, prompt: str) -> str:
        from flux_loop.model import _ask

        return _ask(state, prompt).text

    def _candidate(self, index: int, step: Step) -> Candidate:
        """The step as a candidate. The `Step` itself stays in `self._steps` rather than in
        `meta`: `meta` is written to the record as JSON, and the record should carry the
        step's tool and arguments, not the repr of an object."""
        name = f"step{len(self.outcomes) + index}:{step.tool}"
        self._steps[name] = step
        return Candidate(
            name=name, artifact="",
            knobs={"tool": step.tool, "args": summarize(step.args, 300),
                   "bind": step.bind or ""},
            meta={"index": index, "strategy": "plan" if self.plan_file else "llm-plan"})

    def _refuse(self, state: LoopState, refusal: Refusal) -> None:
        self.round_refusals.append(refusal)
        self.all_refusals.append(refusal)
        state.refused.append((refusal.tool, refusal.reason[:300]))

    def _out_of_time(self) -> bool:
        return (self.wall_clock_budget_s is not None
                and time.monotonic() - self.started > self.wall_clock_budget_s)

    # ---- the run in omni's own terms: the node's return and the task report's lines
    def omni_report(self, out: Any) -> OmniReport:
        return OmniReport(
            prompt=self.prompt, outcomes=tuple(self.outcomes), refusals=tuple(self.all_refusals),
            conclusion=self.conclusion_text, done=self.done, rounds=self.rounds,
            llm_calls=self.llm_calls, wall_clock_s=time.monotonic() - self.started,
            provenance_path=self.provenance_path)

    def report(self, out: Any) -> list[str]:
        lines = [f"  CONCLUSION ({'done' if self.done else 'a budget stop'}, {self.rounds} round(s), "
                 f"{self.llm_calls} model call(s), {len(self.outcomes)} step(s) executed)"]
        lines.append(f"    {self.conclusion_text or '(none: the model never concluded)'}")
        for i, o in enumerate(self.outcomes):
            lines.append(f"    [{i}] {o.step.tool}" + (f" -> ${o.step.bind}" if o.step.bind else "")
                         + f" ({o.elapsed_s:.1f}s): " + (summarize(o.result, 120) if o.ok else f"ERROR {o.error}"))
        if self.all_refusals:
            lines.append(f"  REFUSED STEPS ({len(self.all_refusals)})")
            lines.extend(f"    {r.render()}" for r in self.all_refusals[:8])
        if self.provenance_path:
            lines.append(f"  provenance {self.provenance_path} (a replayable plan)")
        return lines


__all__ = ["STAGE", "World"]
