"""omni as a `flux_loop.Problem` (D458): the round shape of D377, on the one loop.

Omni was the last application with a loop of its own -- its own round budget, its own
validate-then-execute pass, its own record rim and its own feedback drain. Nothing about it
is a different SHAPE, though: a round is a BATCH of work the orchestrator proposes (D446/D457),
`plan.validate_step` is a gate, and running a tool is a measurement on one stage. So the round
loop is the loop's, and what stays here is what makes omni omni: the prompt, the catalog, the
dithering guard, and the fact that its answer is a conclusion in words rather than a design.

What the loop now owns for omni: the campaign record and its trials, the operator-feedback
drain, the timing tree, the live panels, the step budget, and the refusal bookkeeping.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator

from flux_loop import Candidate, LoopRequest, LoopState, Problem, Scored, Verdict

from .catalog import ToolSpec, render_catalog
from .pilot import StepOutcome, _execute, _record_context, _round_prompt, summarize
from .plan import Refusal, Step, parse_proposal, validate_step

__all__ = ["OmniProblem"]


class OmniProblem(Problem):
    """One omni run. `search` is the round loop: ask the model, yield the steps it proposed
    as a batch, read what they returned, ask again."""

    name = "omni"

    def __init__(self, prompt: str, catalog: dict[str, ToolSpec], workdir: str | Path, *,
                 tools: list[str] | None = None, max_calls: int = 16,
                 max_steps_per_round: int = 4, wall_clock_budget_s: float | None = None,
                 started: float = 0.0) -> None:
        self.prompt = prompt
        self.catalog = catalog
        self.catalog_text = render_catalog(catalog)
        self.workdir = Path(workdir)
        self.tools = sorted(tools or [])
        self.max_calls = max_calls
        self.max_steps_per_round = max_steps_per_round
        self.wall_clock_budget_s = wall_clock_budget_s
        self.started = started or time.monotonic()
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
        self._records: Any = None
        self._bound: set[str] = set()
        self._admitted_now: list[Step] = []
        self._steps: dict[str, Step] = {}

    # ------------------------------------------------------------------ mentor
    def objective(self, request: LoopRequest) -> dict[str, Any]:
        """The campaign is keyed by the PROMPT (D401): re-running the same task resumes it
        and round 1 reads back what it concluded last time."""
        return {"study": "omni", "prompt": self.prompt, "tools": self.tools}

    def open_records(self, request: LoopRequest, say: Any) -> Any:
        self._records = super().open_records(request, say)
        return self._records

    @property
    def records(self) -> Any:
        """The campaign record the loop opened, so the run can write its conclusion to it:
        omni's conclusion is TEXT, not a decided design, and the loop only writes one when
        there is a pick."""
        return self._records

    def mentor_sections(self, state: LoopState) -> list[tuple[str, str]]:
        return [("task", self.prompt), ("tool catalog", self.catalog_text)]

    # ------------------------------------------------------------ orchestration
    def search(self, state: LoopState) -> Iterator[list[Candidate]]:
        """One round per step: the model plans, the loop gates and runs, the results come
        back as the value of the `yield` and become the next round's context."""
        say = state.say
        record_ctx = _record_context(self._records)
        max_rounds = max(1, state.request.steps)
        got: list[Scored] = []
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
                got = yield []                    # nothing to run; the model is told why
                continue
            if proposal.conclusion:
                self.conclusion_text = proposal.conclusion
            room = max(0, self.max_calls - len(self.outcomes))
            steps = list(proposal.steps[:self.max_steps_per_round])[:room]
            if proposal.done and not steps:
                self.done = True
                return
            got = yield [self._candidate(i, s) for i, s in enumerate(steps)]
            if proposal.done and not self.round_refusals:
                self.done = True
                return
            if len(self.outcomes) >= self.max_calls:
                say(f"  the tool budget is spent ({self.max_calls} call(s))")
                return
        del got                                   # the last round's results are in `outcomes`

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
    def stages(self) -> list[str]:
        return ["executed"]

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

    def cache_suffix(self) -> str | None:
        """No cache: a tool call has effects and a run's whole point is that it RAN. A
        cached answer from an earlier run would be exactly the remembered number the loop
        refuses everywhere else (D297)."""
        return None

    # ----------------------------------------------------------- the decision
    def frontier(self, scored: list[Scored], state: LoopState) -> list[Scored]:
        return []

    def frontier_axes(self) -> None:
        return None

    def decide(self, pool: list[Scored], state: LoopState) -> tuple[Scored | None, str]:
        """omni answers in words: the conclusion the model wrote from what ran. There is no
        design to decide between, so there is no decision -- saying otherwise would put a
        tool call in the standings as if it were an answer."""
        if self.conclusion_text:
            state.lessons.append(f"[executed] omni concludes: {self.conclusion_text[:300]}")
        return None, "omni concludes in words, not in a decided design"

    # ------------------------------------------------------------------ inner
    def _ask(self, state: LoopState, prompt: str) -> str:
        from flux_loop.model import _ask

        return _ask(state, prompt)

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
            meta={"index": index, "strategy": "llm-plan"})

    def _refuse(self, state: LoopState, refusal: Refusal) -> None:
        self.round_refusals.append(refusal)
        self.all_refusals.append(refusal)
        state.refused.append((refusal.tool, refusal.reason[:300]))

    def _out_of_time(self) -> bool:
        return (self.wall_clock_budget_s is not None
                and time.monotonic() - self.started > self.wall_clock_budget_s)
