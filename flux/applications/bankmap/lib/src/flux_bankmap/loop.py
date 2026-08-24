"""The bank-mapping study on the loop (D446): the chain of D356 as one search generator.

THE CHAIN, cheapest and most certain first, each stage a batch the loop gates with the same
exhaustive checker and measures (hardware cost) when it passes:

  1. BASELINE   the plain modulo. Checked, not assumed: the study exists because it fails, and
                the report says by how much.
  2. z3         the XOR-fold family, searched EXACTLY. Either the cheapest conflict-free fold
                the checker accepts for every start address, or a proof that none exists.
  3. FEASIBLE   when none exists, what IS achievable: the largest concurrency the request's
                strides admit, and the largest stride subset the requested concurrency admits.
                A study that can only say "no" has not finished.
  4. MODEL      non-linear families a model proposes -- consulted after the linear answer is
                known, told what failed and why, and checked exhaustively like everything else.

The DECISION is the cheapest conflict-free mapping the loop measured; if none, `run_study`
(flow.py) reports the best partial answer, labelled as partial. Every mapping is scored by the
same exhaustive checker, so a model's idea and the solver's result are compared on one footing.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable, Iterator

from flux_loop import Candidate, LoopState, Problem, StageNames, Scored, Verdict

from .check import Verdict as CheckVerdict, check
from .impossible import find_impossibility, max_feasible_concurrency
from .mapping import Mapping, Modulo, XorFold
from .problem import MappingRequest

STAGE = "exhaustive"       # the one stage: the checker is exhaustive, the cost is analytic


def rule_wired(req: MappingRequest) -> MappingRequest:
    """Unsolved free stages wired by rule (the interleave) for CHECKING only (D372).

    An unsolved free stage constrains no pair, so a mapping checked against it is judged
    against hardware that does not exist -- the first version declared plain modulo
    conflict-free that way. Until the solver chooses a wiring, verdicts use the least
    constrained concrete one, and say so.
    """
    if not any(st.lane_key == "free" and st.partition is None for st in req.stages):
        return req
    fixed = []
    for st in req.stages:
        if st.lane_key == "free" and st.partition is None:
            blocks = min(st.blocks, req.concurrent)
            fixed.append(replace(st, partition=tuple(
                tuple(range(b, req.concurrent, blocks)) for b in range(blocks))))
        else:
            fixed.append(st)
    return replace(req, stages=tuple(fixed))


class BankmapProblem(Problem):
    """The study's hooks. `run_study` (flow.py) runs it and reads a `MappingResult` back."""

    name = "bankmap"

    def __init__(self, request: MappingRequest, *,
                 propose: Callable[..., list[tuple[Mapping, str]]] | None = None) -> None:
        self.request = request                 # replaced when the solver chooses a wiring
        self.propose_fn = propose              # the older proposal callable, when a caller has one
        self.started = time.monotonic()
        self.mappings: dict[str, Mapping] = {}
        self.candidates: list[tuple[Mapping, CheckVerdict, str]] = []
        self.progress: list[dict[str, Any]] = []
        self.partial: list[tuple[str, XorFold]] = []
        self.counter_examples: list[str] = []
        self.base_summary = ""
        self.z3_summary = ""
        self.trace: Any = None
        self.impossible = False
        self.tried: list[tuple[str, str]] = []
        self._mentor: Any = None               # the declared sources (D449)

    # ---- mentor
    def objective(self, request: Any) -> dict[str, Any]:
        r = self.request
        return {"study": "bankmap", "strides": list(r.strides), "concurrent": r.concurrent,
                "banks": r.banks, "address_bits": r.address_bits, "topology": r.topology,
                "stages": [st.describe() for st in r.stages]}

    def cache_suffix(self) -> str | None:
        return None                            # microseconds per check: nothing to cache

    def knowledge(self) -> Any:
        """What earlier runs of this campaign found, as a declared source (D449): the cheapest
        conflict-free family, and what was tried and refused. The model round is told the same
        refusals as its ALREADY TRIED list; this is the reader's view of them."""
        if self._mentor is None:
            from flux_knowledge import Mentor, RecordReadback

            self._mentor = Mentor([RecordReadback(
                stage=STAGE, metric="hardware_cost", knobs=("family",), higher_is_better=False,
                metric_label="XOR-equivalent gates (fewer is better)",
                title="record: mapping families, cheapest first",
                conclusion=lambda c: (f"an earlier run decided: {c['decision']}"
                                      f"{'' if c.get('conflict_free') else ' (partial)'}"
                                      if c.get("decision") else None),
                extra=lambda r: [f"tried and refused: {c.get('name') or c.get('describe', '?')}"
                                 f" -- {why[:90]}"
                                 for c, why in r.refusals(stage=StageNames.GATE, limit=6)])])
        return self._mentor

    def stages(self) -> list[str]:
        return [STAGE]

    def analytic_stages(self) -> frozenset[str]:
        return frozenset({STAGE})

    def evaluator_name(self, stage: str) -> str:
        return "bankmap@exhaustive-check"

    # ---- orchestrator: the chain
    def _cand(self, mapping: Mapping, who: str, why: str = "") -> Candidate:
        name = mapping.describe()
        self.mappings[name] = mapping
        return Candidate(name=name, knobs={"family": type(mapping).__name__, "describe": name},
                         meta={"strategy": who, **({"idea": why} if why else {})})

    def search(self, state: LoopState) -> Iterator[list[Candidate]]:
        from . import flow as _flow          # the solver and the feasibility helpers live there

        say = state.say
        r = self.request
        n = r.concurrent
        say(f"problem: {r.describe()}")
        for note in r.notes:
            say(f"  interconnect: {note}")
            state.lessons.append(f"interconnect: {note}")

        # 1. baseline -- against a CONCRETE wiring: rule-fixed interleave until the solver chooses
        base = Modulo(0)
        scored = yield [self._cand(base, "baseline")]
        if scored:
            state.lessons.append("the plain modulo mapping is already conflict-free for these "
                                 "strides; no hashing is needed")
            return

        # 1b. is ANY mapping possible? A pigeonhole witness costs microseconds and, when it
        #     exists, makes every solver round and every model proposal a waste (D356).
        witness = find_impossibility(r)
        if witness is not None:
            bound = max_feasible_concurrency(r)
            say(f"impossible: {witness.explain()}")
            state.lessons.append(witness.explain())
            state.lessons.append(f"any mapping can serve at most {bound} of these accesses "
                                 f"concurrently without a conflict; the request asks for {n}")
            state.not_established.append(
                f"no conflict-free mapping exists for {n} concurrent accesses across "
                f"{list(r.strides)} -- proved, not searched. Ask for at most {bound}, or "
                "drop a stride")
            self.impossible = True
            self._feasible(state, start=bound)
            self._conclude_partial(state)
            return

        # 2. z3, exact over the linear family
        say(f"z3: searching XOR-folds over {r.bank_bits}x{r.address_bits} taps "
            f"(budget {r.z3_seconds}s)")
        fold, trace = _flow.solve(r, log=say)
        self.trace = trace
        if trace.partition is not None:
            # The solver CHOSE the lane wiring (D372): from here on it is the concrete
            # hardware every stage checks against, and the report says what to build.
            self.request = r = replace(r, stages=tuple(
                replace(st, partition=trace.partition)
                if st.lane_key == "free" and st.partition is None else st
                for st in r.stages))
            say(f"  z3 chose the lane wiring: {[list(b) for b in trace.partition]}")
            state.lessons.append(f"the solver chose the lane-to-crossbar wiring jointly with "
                                 f"the mapping: {[list(b) for b in trace.partition]}")
        elif any(st.lane_key == "free" and st.partition is None for st in r.stages):
            # Joint-unsat says NO wiring rescues the linear family (D372) -- but the model's
            # non-linear round still needs a concrete wiring to be judged against. Fix the
            # least constrained one: the interleave spreads the window as evenly as possible,
            # so it minimises co-located pairs, and it is the best wiring measured (D364).
            # Chosen by rule, stated in the report, never presented as the solver's finding.
            self.request = r = rule_wired(r)
            say("  the wiring cannot rescue the linear family; fixing the interleave (fewest "
                "co-located pairs) so the model round has a concrete target")
            state.lessons.append(
                "no wiring admits a linear fold (proved over every assignment); the model "
                "round ran on the interleaved wiring, fixed by rule for having the fewest "
                "co-located pairs -- not chosen by the solver")
        self.z3_summary = trace.outcome
        if fold is not None:
            for cost, clean, desc in trace.probes[:-1]:
                self.progress.append({"quality": clean, "cost": cost, "label": desc,
                                      "phase": "z3", "solved": False})
            scored = yield [self._cand(fold, "z3")]
            if scored:
                state.lessons.append(
                    f"z3 found {fold.describe()} -- conflict-free for every start address, "
                    f"{fold.hardware_cost()} XOR gate(s), in {trace.rounds} round(s)")
        else:
            for stride, start in trace.counter_examples[:6]:
                self.counter_examples.append(f"stride {stride}, start 0x{start:x}")
            if "unsat" in self.z3_summary:
                state.lessons.append(
                    f"NO XOR-fold is conflict-free for {n} concurrent accesses across strides "
                    f"{list(r.strides)}: the solver proved the linear family cannot do it, "
                    f"so any answer must be non-linear")
            # 3. what IS feasible, when the request is not
            n_ok = self._feasible(state)
            if n_ok:
                state.lessons.append(f"the strides admit at most {n_ok} conflict-free "
                                     f"concurrent accesses in the linear family (asked for {n})")

        # 4. the model, told what the solver could not do. The record, read back (D402): a
        #    resumed campaign's past refusals seed the ALREADY TRIED list, so the model is
        #    told what failed before, not just in this run.
        if state.records is not None and getattr(state.records, "resumed", False):
            past = [(c.get("name") or c.get("describe", "?"), why[:90])
                    for c, why in state.records.refusals(stage=StageNames.GATE, limit=8)
                    + state.records.refusals(stage=STAGE, limit=8)]
            if past:
                self.tried.extend(past)
                say(f"  the record seeds ALREADY TRIED with {len(past)} past refusal(s)")
        assignment_open = any(st.lane_key == "free" and st.partition is None
                              for st in r.stages)
        has_model = self.propose_fn is not None or state.proposer is not None
        if has_model and r.llm_round > 0 and not assignment_open:
            for round_ in range(1, 3):
                human = state.drain()
                try:
                    proposals = self._propose(state, human)
                except Exception as exc:  # noqa: BLE001
                    state.not_established.append(
                        f"the proposer did not run ({type(exc).__name__}: {exc!s:.100})")
                    break
                say(f"model round {round_}: {len(proposals)} proposal(s)")
                if not proposals:
                    break
                scored = yield [self._cand(m, "llm", why) for m, why in proposals]
                if scored:
                    break
        if not any(v.conflict_free for _m, v, _who in self.candidates):
            state.not_established.append(
                f"no conflict-free mapping was found for {n} concurrent accesses across all of "
                f"{list(r.strides)}; the linear family was proved insufficient and the model's "
                f"proposals were all refused")
            self._conclude_partial(state)

    def _propose(self, state: LoopState, human: str | None) -> list[tuple[Mapping, str]]:
        r = self.request
        kw = dict(baseline_summary=self.base_summary, z3_summary=self.z3_summary,
                  counter_examples=self.counter_examples, count=r.llm_round, tried=self.tried,
                  problem=r.problem, guidance=human)
        if self.propose_fn is not None:
            return list(self.propose_fn(r, **kw))
        from flux_llm import propose as _propose

        from .propose import build_prompt, parse_proposals

        reply = _propose(state.proposer, build_prompt(r, **kw))
        return parse_proposals(reply, r.bank_bits)

    def _feasible(self, state: LoopState, *, start: int | None = None) -> int:
        """The partial answers, when the request is not achievable (stage 3)."""
        from . import flow as _flow

        r = self.request
        n = r.concurrent
        n_ok, m_n = _flow._feasible_concurrency(r, state.say, start=start)
        if m_n is not None:
            self.partial.append((f"{n_ok} concurrent (all strides)", m_n))
            self.progress.append({"quality": 1.0, "cost": m_n.hardware_cost(),
                                  "label": f"{m_n.describe()} (at N={n_ok})",
                                  "phase": "feasible", "solved": False})
        strides_ok, m_s = _flow._feasible_strides(r, state.say)
        if m_s is not None and strides_ok:
            self.partial.append((f"strides {list(strides_ok)} at {n} concurrent", m_s))
        if self.impossible and len(strides_ok) <= 1:
            state.lessons.extend(_flow._stride_compatibility(r, state.say))
        return n_ok

    def _conclude_partial(self, state: LoopState) -> None:
        """A partial answer is a conclusion too (INFERENCE), even though nothing was measured."""
        best = self.partial[0][1] if self.partial else None
        if state.records is not None and best is not None:
            state.records.conclude({"decision": best.describe(), "conflict_free": False,
                                    "hardware_cost": best.hardware_cost()})

    # ---- evaluator: the gate is the exhaustive checker
    def build(self, cand: Candidate, subgoal: str | None, state: LoopState) -> Any:
        return self.mappings[cand.name]

    def judge(self, built: Any, cand: Candidate, subgoal: str | None,
              state: LoopState) -> Verdict:
        mapping: Mapping = built
        who = str(cand.meta.get("strategy", "llm"))
        req = rule_wired(self.request) if who == "baseline" else self.request
        n = req.concurrent
        v = check(mapping, req)
        self.candidates.append((mapping, v, who))
        self.progress.append({"quality": v.clean_fraction, "cost": mapping.hardware_cost(),
                              "label": mapping.describe(), "phase": who,
                              "solved": v.conflict_free})
        summary = v.summary(n)
        if who == "baseline":
            self.base_summary = summary
            state.say(f"baseline {mapping.describe()}: {summary}")
        elif who == "llm":
            if v.conflict_free:
                state.say(f"  {mapping.describe()}: CONFLICT-FREE, cost {mapping.hardware_cost()}")
                self.tried.append((mapping.describe(), "conflict-free"))
            else:
                self.tried.append((mapping.describe(), summary[:90]))
                if v.worst is not None:
                    self.counter_examples.append(
                        f"{mapping.describe()} -> stride {v.worst.stride}, start "
                        f"0x{v.worst.worst_start:x} reaches {v.worst.worst_distinct}/{n}")
                state.say(f"  {mapping.describe()}: refused -- {summary[:80]}")
        return Verdict(v.conflict_free, 1.0 - v.clean_fraction, summary)

    def measure(self, cand: Candidate, stage: str, state: LoopState) -> dict[str, Any] | None:
        m = self.mappings[cand.name]
        return {"hardware_cost": float(m.hardware_cost()), "clean_fraction": 1.0}

    # ---- the decision: the cheapest conflict-free mapping
    def decide(self, pool: list[Scored], state: LoopState) -> tuple[Scored | None, str]:
        if not pool:
            return None, "nothing conflict-free"
        pick = min(pool, key=lambda p: p.metrics["hardware_cost"])
        state.say(f"decision: {pick.name} ({pick.metrics['hardware_cost']:.0f} XOR-equivalent)")
        return pick, "the cheapest conflict-free mapping"

    def conclusion(self, pick: Scored, decided_by: str) -> dict[str, Any]:
        m = self.mappings[pick.name]
        return {"decision": m.describe(), "conflict_free": True,
                "hardware_cost": m.hardware_cost()}


__all__ = ["BankmapProblem", "STAGE", "rule_wired"]
