"""The bank-mapping world named by `applications/bankmap/problem.yaml` (D539): the
D356 chain as one search generator on the loop (D446).

The chain, cheapest and most certain first; each stage is a batch the loop gates with the same
exhaustive checker and measures (hardware cost) when it passes:

  1. BASELINE   the plain modulo, checked rather than assumed.
  2. z3         the XOR-fold family, searched exactly: the cheapest conflict-free fold, or a
                proof that none exists.
  3. FEASIBLE   when none exists: the largest concurrency the strides admit, and the largest
                stride subset the requested concurrency admits.
  4. MODEL      non-linear families a model proposes, told what failed and why.

The decision is the cheapest conflict-free mapping measured; if none, the report carries the
best partial answer, labelled as partial. The document gives the ask (`params:`), the one
objective, the one stage, `cache: false` (the checker answers in microseconds) and `steps`
(baseline, solver, then model rounds).
"""

from __future__ import annotations

import time
from dataclasses import replace
from itertools import combinations
from typing import Any, Callable, Iterator

from flux_loop import Candidate, LoopState, Scored, StageNames, Verdict

from .check import Verdict as CheckVerdict, check
from .impossible import find_impossibility, max_feasible_concurrency
from .mapping import Mapping, Modulo, XorFold
from .problem import MappingRequest
from .solve_z3 import solve   # looked up through this module, so a test can stub the solver

STAGE = "exhaustive"       # the document's one stage: the checker is exhaustive, the cost analytic


def past_refusals(records: Any, limit: int) -> list[tuple[str, str]]:
    """What the record says was tried and refused, as (mapping, why); read by `knowledge` and
    by the model round's ALREADY TRIED seed in `search`."""
    return [(c.get("name") or c.get("describe", "?"), why[:90])
            for c, why in records.refusals(stage=StageNames.GATE, limit=limit)
            + records.refusals(stage=STAGE, limit=limit)]


def rule_wired(req: MappingRequest) -> MappingRequest:
    """Unsolved free stages wired by rule (the interleave) for checking only (D372).

    An unsolved free stage constrains no pair, so checking against it would judge hardware that
    does not exist (plain modulo would pass). Until the solver chooses a wiring, verdicts use
    the least constrained concrete one, and say so.
    """
    if not any(st.unsolved for st in req.stages):
        return req
    fixed = []
    for st in req.stages:
        if st.unsolved:
            blocks = min(st.blocks, req.concurrent)
            fixed.append(replace(st, partition=tuple(
                tuple(range(b, req.concurrent, blocks)) for b in range(blocks))))
        else:
            fixed.append(st)
    return replace(req, stages=tuple(fixed))


# ---- the partial answers, when the request is not achievable (stage 3)
def feasible_concurrency(request: MappingRequest, log: Callable[[str], None],
                         start: int | None = None) -> tuple[int, XorFold | None]:
    """The largest N for which an XOR-fold exists, by descent.

    `start` is the pigeonhole bound when there is one: every N above it is proved impossible
    for any mapping, so z3 is not asked again.
    """
    top = request.concurrent - 1 if start is None else min(start, request.concurrent - 1)
    for n in range(top, 0, -1):
        m, _ = solve(replace(request, concurrent=n), timeout_s=min(request.z3_seconds, 20))
        if m is not None:
            log(f"  feasible: {n} concurrent accesses are conflict-free for all strides "
                f"({m.describe()}, {m.hardware_cost()} XOR)")
            return n, m
    return 0, None


def feasible_strides(request: MappingRequest, log: Callable[[str], None]
                     ) -> tuple[tuple[int, ...], XorFold | None]:
    """The largest stride subset the requested concurrency admits, greedily by stride order."""
    kept: list[int] = []
    best: XorFold | None = None
    proved, unsettled = [], []
    budget = min(request.z3_seconds, 20)
    for s in request.strides:
        trial = tuple(kept + [s])
        m, trace = solve(replace(request, strides=trial), timeout_s=budget)
        if m is not None:
            kept.append(s)
            best = m
        elif "unsat" in trace.outcome:
            proved.append(s)
        else:
            unsettled.append(s)
    if kept and len(kept) < len(request.strides):
        # A proof and a timeout are different answers: report them separately.
        parts = []
        if proved:
            parts.append(f"adding any of {proved} is proved impossible for the linear family")
        if unsettled:
            parts.append(f"no fold was found within {budget}s when adding any of {unsettled} "
                         "(not a proof; a larger z3_seconds may settle it)")
        log(f"  feasible: strides {kept} together admit {request.concurrent} concurrent "
            f"accesses; " + "; ".join(parts))
    return tuple(kept), best


def stride_compatibility(request: MappingRequest, log: Callable[[str], None]) -> list[str]:
    """Which strides can coexist at all, when the greedy subset stopped at one.

    Proved per pair with the pigeonhole stage (microseconds), so the study can say "each alone,
    never two" when no two strides can share the hardware (e.g. 4-lane crossbars into 4 groups
    with power-of-two strides, D363).
    """
    strides = request.strides
    if len(strides) < 2 or len(strides) > 10:
        return []
    alone = {s: find_impossibility(replace(request, strides=(s,))) is None
             for s in strides}
    pairs = list(combinations(strides, 2))
    bad = [(s, t) for s, t in pairs
           if find_impossibility(replace(request, strides=(s, t))) is not None]
    out: list[str] = []
    if all(alone.values()) and len(bad) == len(pairs):
        out.append(f"each stride alone is servable at {request.concurrent} concurrent, and NO "
                   f"two of them together are -- every pair is proved impossible for any "
                   f"mapping (with a laned first stage, a stride-s chunk fixes the group as "
                   f"a 4s-periodic function of the address and a stride-t chunk then collides "
                   f"whenever 2s divides t)")
    elif bad:
        out.append(f"{len(bad)} of {len(pairs)} stride pairs are proved impossible together: "
                   + ", ".join(f"{s}+{t}" for s, t in bad[:8])
                   + (" ..." if len(bad) > 8 else ""))
    for line in out:
        log(f"  {line}")
    return out


class World:
    """One bank-mapping study as the hooks of its document problem, built from `params:`."""

    name = "bankmap"

    def __init__(self, problem: Any) -> None:
        self.problem = problem
        self.request = MappingRequest.from_params(dict(problem.task.params or {}))
        self.started = time.monotonic()
        self.mappings: dict[str, Mapping] = {}
        self.candidates: list[tuple[Mapping, CheckVerdict, str]] = []
        #: Every checked mapping in order, as {quality, cost, label, phase, solved}; quality is
        #: the worst resource's clean fraction of start addresses (progress figure, D373).
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
    def prepare(self, state: LoopState) -> None:
        r = self.request
        state.say(f"problem: {r.describe()}")
        for note in r.notes:
            state.say(f"  interconnect: {note}")
            state.lessons.append(f"interconnect: {note}")

    def knowledge(self) -> Any:
        """What earlier runs of this campaign found, as a declared source (D449): the cheapest
        conflict-free family, and what was tried and refused."""
        if self._mentor is None:
            from flux_knowledge import Mentor, RecordReadback

            self._mentor = Mentor([RecordReadback(
                stage=STAGE, metric="hardware_cost", knobs=("family",), higher_is_better=False,
                metric_label="XOR-equivalent gates (fewer is better)",
                title="record: mapping families, cheapest first",
                conclusion=lambda c: (f"an earlier run decided: {c['decision']}"
                                      f"{'' if c.get('conflict_free') else ' (partial)'}"
                                      if c.get("decision") else None),
                extra=lambda r: [f"tried and refused: {name} -- {why}"
                                 for name, why in past_refusals(r, limit=6)])])
        return self._mentor

    # ---- orchestrator: the chain
    def _cand(self, mapping: Mapping, who: str, why: str = "") -> Candidate:
        # The artifact is the mapping's own Verilog (on the record row and in `--out`, D402).
        name = mapping.describe()
        self.mappings[name] = mapping
        return Candidate(name=name,
                         artifact=mapping.verilog(self.request.address_bits, self.request.bank_bits),
                         knobs={"family": type(mapping).__name__, "describe": name},
                         meta={"strategy": who, **({"idea": why} if why else {})})

    def search(self, state: LoopState) -> Iterator[list[Candidate]]:
        say = state.say
        r = self.request
        n = r.concurrent

        # 1. baseline -- against a concrete wiring: rule-fixed interleave until the solver chooses
        base = Modulo(0)
        scored = yield [self._cand(base, "baseline")]
        if scored:
            state.lessons.append("the plain modulo mapping is already conflict-free for these "
                                 "strides; no hashing is needed")
            return

        # 1b. A pigeonhole witness (microseconds) proves no mapping exists, skipping every
        #     solver and model round (D356).
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
        fold, trace = solve(r, log=say)
        self.trace = trace
        if trace.partition is not None:
            # The solver chose the lane wiring (D372): every later stage checks against it.
            self.request = r = replace(r, stages=tuple(
                replace(st, partition=trace.partition) if st.unsolved else st
                for st in r.stages))
            say(f"  z3 chose the lane wiring: {[list(b) for b in trace.partition]}")
            state.lessons.append(f"the solver chose the lane-to-crossbar wiring jointly with "
                                 f"the mapping: {[list(b) for b in trace.partition]}")
        elif any(st.unsolved for st in r.stages):
            # Joint-unsat: no wiring rescues the linear family (D372), but the model round still
            # needs a concrete wiring. Use the interleave (fewest co-located pairs, best measured,
            # D364), chosen by rule and stated as such in the report.
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

        # 4. the model, told what the solver could not do; a resumed campaign's past refusals
        #    (read from the record, D402) seed its ALREADY TRIED list.
        if state.records is not None and state.records.resumed:
            past = past_refusals(state.records, limit=8)
            if past:
                self.tried.extend(past)
                say(f"  the record seeds ALREADY TRIED with {len(past)} past refusal(s)")
        assignment_open = any(st.unsolved for st in r.stages)
        # `steps`: the baseline, the solver's step, then the model rounds -- each told what the
        # last one's proposals failed on.
        rounds = max(0, int(state.request.steps) - 2)
        if state.proposer is not None and r.llm_round > 0 and not assignment_open:
            for round_ in range(1, rounds + 1):
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
        from .propose import build_prompt, parse_proposals

        r = self.request
        prompt = build_prompt(r, baseline_summary=self.base_summary, z3_summary=self.z3_summary,
                              counter_examples=self.counter_examples, count=r.llm_round,
                              tried=self.tried, problem=r.problem, guidance=human)
        return parse_proposals(state.proposer.propose(prompt).text, r.bank_bits)

    def _feasible(self, state: LoopState, *, start: int | None = None) -> int:
        """The partial answers, when the request is not achievable (stage 3)."""
        r = self.request
        n = r.concurrent
        n_ok, m_n = feasible_concurrency(r, state.say, start=start)
        if m_n is not None:
            self.partial.append((f"{n_ok} concurrent (all strides)", m_n))
            self.progress.append({"quality": 1.0, "cost": m_n.hardware_cost(),
                                  "label": f"{m_n.describe()} (at N={n_ok})",
                                  "phase": "feasible", "solved": False})
        strides_ok, m_s = feasible_strides(r, state.say)
        if m_s is not None and strides_ok:
            self.partial.append((f"strides {list(strides_ok)} at {n} concurrent", m_s))
        if self.impossible and len(strides_ok) <= 1:
            state.lessons.extend(stride_compatibility(r, state.say))
        return n_ok

    def _conclude_partial(self, state: LoopState) -> None:
        """A partial answer is a conclusion too (INFERENCE), though nothing was measured."""
        best = self.partial[0][1] if self.partial else None
        if best is not None:
            state.lessons.extend(f"best partial answer: {label} -- {m.describe()}"
                                 for label, m in self.partial)
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

    def analytic_stages(self) -> frozenset[str]:
        """The cost is a formula over the mapping: its rows are tagged modelled."""
        return frozenset({STAGE})

    def measure(self, cand: Candidate, stage: str, state: LoopState) -> dict[str, Any] | None:
        m = self.mappings[cand.name]
        return {"hardware_cost": float(m.hardware_cost()), "clean_fraction": 1.0}

    # ---- the decision is the document's one objective (least hardware_cost); the conclusion
    # row carries `conflict_free`, which `knowledge` reads to tell a decision from a partial answer
    def conclusion(self, pick: Scored, decided_by: str) -> dict[str, Any]:
        m = self.mappings[pick.name]
        return {"decision": m.describe(), "conflict_free": True,
                "hardware_cost": m.hardware_cost()}

    # ---- the result in the study's own terms (helpers for the task report and `--json`)
    def decided(self, out: Any) -> Mapping | None:
        """The loop's decision as a mapping; without one, the best partial answer."""
        if out.decision is not None:
            return self.mappings[out.decision.name]
        return self.partial[0][1] if self.partial else None

    def conflict_free(self, out: Any) -> bool:
        """Whether the requirement was met: a decision is a conflict-free mapping, by the gate."""
        return out.decision is not None

    def provenance(self) -> dict[str, Any]:
        """What the answer rests on: the proof, or the solver's rounds and constraints."""
        p: dict[str, Any] = {"wall_clock_s": round(time.monotonic() - self.started, 1)}
        if self.impossible:
            p["impossible"] = True
        elif self.trace is not None:
            p.update({"z3_rounds": self.trace.rounds, "z3_constraints": self.trace.constraints})
        return p

    def result(self, out: Any) -> dict[str, Any]:
        """The answer as JSON (D584): the decided mapping with its Verilog, whether it is
        conflict-free, the request as wired, every candidate with its verdict, the progress,
        the provenance."""
        r, n = self.request, self.request.concurrent
        best = self.decided(out)

        def mapping(m: Any) -> dict[str, Any]:
            return {"describe": m.describe(), "kind": m.to_dict(), "hardware_cost": m.hardware_cost(),
                    "verilog": m.verilog(r.address_bits, r.bank_bits)}

        return {"decision": mapping(best) if best is not None else None, "conflict_free": self.conflict_free(out),
                "hardware_cost": best.hardware_cost() if best is not None else None,
                "request": {"strides": list(r.strides), "concurrent": n, "banks": r.banks,
                            "address_bits": r.address_bits, "stages": [st.describe() for st in r.stages],
                            "topology": r.topology, "notes": list(r.notes)},
                "candidates": [{**mapping(m), "conflict_free": v.conflict_free, "verdict": v.summary(n), "proposed_by": who}
                               for m, v, who in self.candidates],
                "progress": list(self.progress), "provenance": self.provenance()}

    def report(self, out: Any) -> list[str]:
        r = self.request
        best = self.decided(out)
        lines: list[str] = []
        if best is not None and self.conflict_free(out):
            lines.append("  DECISION -- build this")
            lines.append(f"    {best.describe()}")
            lines.append(f"    hardware         {best.hardware_cost()} XOR-equivalent gate(s)")
            lines.append("    conflict-free    yes, for every start address")
            lines.append("    verilog:")
            lines.extend(f"      {line}" for line in best.verilog(r.address_bits, r.bank_bits).splitlines())
        elif best is not None:
            lines.append("  NO CONFLICT-FREE MAPPING -- the best partial answer")
            lines.append(f"    {best.describe()}   ({best.hardware_cost()} XOR-equivalent)")
            for label, m in self.partial:
                lines.append(f"    partial          {label}: {m.describe()}")
        else:
            lines.append("  NO MAPPING FOUND")
        if r.topology:
            lines.append(f"    interconnect     {r.topology}")
        if r.stages:
            lines.append("    crossbar stages  " + "; ".join(st.describe() for st in r.stages))
        lines.append(f"  CANDIDATES ({len(self.candidates)})")
        for m, v, who in self.candidates:
            mark = "ok     " if v.conflict_free else "REFUSED"
            lines.append(f"    {mark} {who:<9} cost {m.hardware_cost():>4}  {m.describe()}")
            if not v.conflict_free:
                lines.append(f"            {v.summary(r.concurrent)}")
        p = self.provenance()
        lines.append(f"  COST  z3 {p.get('z3_rounds', 0)} round(s), {p.get('z3_constraints', 0)} "
                     f"constraints, {p['wall_clock_s']}s wall clock"
                     + ("; proved impossible before any solver round" if self.impossible else ""))
        return lines


__all__ = ["STAGE", "World", "feasible_concurrency", "feasible_strides", "past_refusals",
           "rule_wired", "stride_compatibility"]
