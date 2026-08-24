"""The interconnect_mapping study on the loop (D446): the same skeleton as before (D378),
as one search generator.

  batch 1     STAGE A finds interconnects (`interconnect_loop`); STAGE B builds the policy
              field (the curated catalog, the XOR hill-climb, bankmap's proven fold); the
              cross product policy x fabric is the batch -- every design point is a PAIR,
              because a hash is only as good as the fabric that carries it and vice versa.
  batch 1+k   an optional model round: XOR taps as JSON, through the injectivity gate; the
              accepted policy paired with every found fabric.
  then        THE BIG LOOP (D386): coordination rounds alternate the two little loops --
              a mapping tuned per fabric, fabrics fitted per mapping -- until the front
              stops moving; every new pair is a batch through the same scorer.

One stage, `analytic`: TRAIN and HOLDOUT traffic through the cycle law (the anti-overfitting
split); four costs per pair (area, padding, latency, throughput) and Pareto dominance over
all four is the frontier hook. Certificates by exhaustion are the report's (flow.py), drawn
over the frontier the loop returns.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterator

from flux_loop import Candidate, LoopState, Problem, Scored, Verdict

from .fabric import FabricModel, xbar_full
from .model import Memory
from .solutions import Solution, catalog, injective
from .workloads import Workload, train_holdout

STAGE = "analytic"


def app_scored(p: Scored):
    """The study's own `Scored` pair, carried in the loop's `Scored.payload`."""
    return p.payload["scored"]


class ImappingProblem(Problem):
    name = "interconnect_mapping"

    def __init__(self, *, seed: int = 0, mem: Memory | None = None, ops: int = 8,
                 climb_rounds: int = 40, llm_rounds: int = 0,
                 track: Callable[[Any], None] | None = None,
                 vu_probability: float = 0.7, dma_probability: float = 0.6,
                 coordination_rounds: int = 2) -> None:
        self.seed, self.ops = seed, ops
        self.mem = mem or Memory()
        self.climb_rounds, self.llm_rounds = climb_rounds, llm_rounds
        self.track = track
        self.vu, self.dma = vu_probability, dma_probability
        self.coordination_rounds = coordination_rounds
        self.train: list[Workload] = []
        self.holdout: list[Workload] = []
        self.pairs: dict[str, tuple[Solution, FabricModel]] = {}
        self.memo: dict[str, Any] = {}          # pair_name -> the study's Scored (deterministic)
        self.refused_text: list[str] = []       # the report's refusals, in the study's words
        self.fabrics: list[FabricModel] = []
        self._mentor: Any = None                # the declared sources (D449)

    # ---- mentor
    def objective(self, request: Any) -> dict[str, Any]:
        return {"study": "interconnect_mapping", "seed": self.seed, "ops": self.ops,
                "vu": self.vu, "dma": self.dma}

    def cache_suffix(self) -> str | None:
        return None                                # the cycle law is microseconds; no cache

    def stages(self) -> list[str]:
        return [STAGE]

    def analytic_stages(self) -> frozenset[str]:
        return frozenset({STAGE})

    def evaluator_name(self, stage: str) -> str:
        return "imapping@cycle-law"

    def prepare(self, state: LoopState) -> None:
        self.train, self.holdout = train_holdout(self.seed, ops=self.ops,
                                                 vu_probability=self.vu,
                                                 dma_probability=self.dma)

    def knowledge(self) -> Any:
        """One source (D449): what earlier runs of this campaign measured, as duels over the
        pair's two real knobs. The tab and the proposer's prompt read the same text."""
        if self._mentor is None:
            from flux_knowledge import Mentor

            from .flow import record_readback

            self._mentor = Mentor([record_readback()])
        return self._mentor

    # ---- orchestrator: the search
    def _cand(self, sol: Solution, fabric: FabricModel, *, strategy: str) -> Candidate:
        name = f"{sol.name} + {fabric.name}"
        self.pairs[name] = (sol, fabric)
        return Candidate(name=name, knobs={"policy": sol.name, "fabric": fabric.name,
                                           "schedule": sol.schedule,
                                           "pipe_latency": fabric.pipe_latency,
                                           "area_units": fabric.gate_units},
                         meta={"strategy": strategy})

    def search(self, state: LoopState) -> Iterator[list[Candidate]]:
        from flux_profile import mark, phase as _tphase

        from .flow import (climb_xor, conclude_dict_safe, coordinate, interconnect_loop,
                           pareto_front, z3_mapping_policy)

        mem, train, holdout = self.mem, self.train, self.holdout
        field_ = catalog(mem)
        # STAGE A (D392): find interconnects first -- the interconnect little-loop generates
        # ~30 parameterized candidates and keeps the screen-front under a reference mapping.
        ref = next(s_ for s_ in field_ if s_.name == "S1-xor-global")
        self.fabrics = fabrics = interconnect_loop(train, holdout, mem, ref, track=self.track)
        # STAGE B: mapping functions -- the climb, bankmap's z3 route (a PROVEN fold for the
        # traffic's own strides), and the model's proposals below, all judged on the same
        # holdout cross-product as everything else.
        mark("mapping little-loop: build the policy field")
        if self.climb_rounds > 0:
            with _tphase("search: xor hill-climb", why="ideal fabric", rounds=self.climb_rounds):
                searched = climb_xor(train, holdout, mem, ref, rounds=self.climb_rounds,
                                     seed=self.seed)
            if searched is not None:
                field_.append(searched.solution)
        z3_policy = z3_mapping_policy(train, mem)
        if z3_policy is not None:
            field_.append(z3_policy)
        mark("combined evaluation: policies x found fabrics")
        # The cross product IS the design space: every report line is a pair.
        yield [self._cand(sol, fabric, strategy="cross") for sol in field_ for fabric in fabrics]

        if self.llm_rounds > 0 and state.proposer is not None:
            for k in range(self.llm_rounds):
                sol = self._llm_round(k, field_, state)
                if sol is None:
                    yield []
                    continue
                field_.append(sol)
                yield [self._cand(sol, fabric, strategy="llm") for fabric in fabrics]

        # THE BIG LOOP (D386): alternate the two little loops -- mapping tuned per fabric,
        # fabrics fitted per mapping -- until the front stops moving or the round budget ends.
        for round_i in range(self.coordination_rounds):
            scored = [app_scored(p) for p in state.scored]
            before = {s_.pair_name for s_ in pareto_front(scored)}
            with _tphase("coordinate: little loops", why=f"round {round_i + 1}"):
                new = coordinate(scored, train, holdout, mem,
                                 climb_rounds=self.climb_rounds or 30,
                                 seed=self.seed + 100 + round_i, track=self.track)
            existing = {s_.pair_name for s_ in scored}
            batch = []
            for s_ in new:
                if s_.pair_name in existing:
                    continue
                existing.add(s_.pair_name)
                self.memo[s_.pair_name] = s_       # scored once, inside the little loops
                batch.append(self._cand(s_.solution, s_.fabric, strategy="coordinate"))
            yield batch
            after = {s_.pair_name for s_ in pareto_front([app_scored(p) for p in state.scored])}
            if after == before:
                break
        if state.records is not None:
            state.records.remember("conclusion-so-far", {
                "conclusion": conclude_dict_safe([app_scored(p) for p in state.scored])})

    def _llm_round(self, k: int, field_: list[Solution], state: LoopState) -> Solution | None:
        """The model proposes XOR tap sets as JSON; every proposal passes the injectivity gate
        and the same evaluator, or is refused with the reason -- proposals are hypotheses,
        measurements are verdicts (D297)."""
        from flux_bankmap.mapping import XorFold
        from flux_llm import propose, strip_markdown_fence

        from .conflict import BankHash
        from .flow import score

        mem = self.mem
        fabric = xbar_full(mem.banks)
        history = [(sol.name, score(sol, fabric, self.train, self.holdout, mem).train.avg_latency)
                   for sol in field_]
        best = min(h[1] for h in history)
        lines = "\n".join(f"{n}: train avg latency {v:.3f}" for n, v in history)
        # Round boundary: whatever the operator typed since the last round joins THIS prompt,
        # labelled (D388) -- advisory; the gate below is unchanged.
        human = state.drain()
        readback = self.knowledge().text("record", state)
        prompt = (
            (human + "\n" if human else "") +
            (readback + "\n" if readback else "") +
            "Bank-hash design: 32 banks, bank bit i = XOR of address bits taps[i].\n"
            "Propose taps as JSON {\"taps\": [[..5 lists of address-bit indices..]]}, "
            "address bits 0..15, at most 4 bits per bank bit. The low 5x5 submatrix "
            "must be invertible over GF(2) or the hash corrupts data and is refused.\n"
            f"Best train avg latency so far: {best:.3f} cycles.\n"
            f"Measured so far:\n{lines}\nJSON only.")
        try:
            reply = json.loads(strip_markdown_fence(propose(state.proposer, prompt)))
            taps = tuple(tuple(int(b) for b in t) for t in reply["taps"])
            mapping = XorFold(taps=taps, name=f"xor-llm-{k}")
        except Exception as exc:  # noqa: BLE001 -- refusal, not crash
            self._refuse(state, f"round {k}: unparseable proposal ({exc})")
            return None
        if not injective(mapping, mem.m):
            self._refuse(state, f"round {k}: taps {taps} not injective on low {mem.m} bits")
            return None
        h = BankHash(mapping=mapping, bank_bits=mem.m)
        return Solution(name=f"S8-xor-llm-{k}", hash_of=lambda layout, _h=h: _h,
                        targets=("intra-operand",), metadata=(),
                        assumptions=("model-proposed taps",))

    def _refuse(self, state: LoopState, why: str) -> None:
        self.refused_text.append(why)
        state.refused.append((why.split(":")[0], why))
        if state.records is not None:
            state.records.trial({"refused": why}, f"refused:{hash(why) & 0xffff:04x}",
                                stage="gate", strategy="llm", metrics=None, error=why)

    # ---- evaluator: the gate admits every built pair; the stage is the cycle law
    def build(self, cand: Candidate, subgoal: str | None, state: LoopState) -> Any:
        return self.pairs[cand.name]

    def judge(self, built: Any, cand: Candidate, subgoal: str | None,
              state: LoopState) -> Verdict:
        return Verdict(True, 0.0)

    def measure_batch(self, cands: list[Candidate], stage: str, state: LoopState
                      ) -> list[dict[str, Any] | None]:
        from flux_profile import phase as _tphase

        from .flow import score

        out: list[dict[str, Any] | None] = []
        for c in cands:
            s = self.memo.get(c.name)
            if s is None:
                sol, fabric = self.pairs[c.name]
                with _tphase("score: policy x fabric", why=c.name):
                    s = score(sol, fabric, self.train, self.holdout, self.mem)
                self.memo[c.name] = s
                if self.track:
                    self.track(s)
            out.append({"holdout_latency": s.holdout.avg_latency,
                        "holdout_throughput": s.holdout.throughput,
                        "area_units": float(s.area_score), "pad_fraction": s.pad_fraction,
                        "train_latency": s.train.avg_latency, "scored": s})
        return out

    # ---- the frontier and the decision: four costs
    def frontier(self, scored: list[Scored], state: LoopState) -> list[Scored]:
        from .flow import pareto_front

        front = {id(s_) for s_ in pareto_front([app_scored(p) for p in scored])}
        return [p for p in scored if id(app_scored(p)) in front]

    def decide(self, pool: list[Scored], state: LoopState) -> tuple[Scored | None, str]:
        from flux_frontier import knee_ranked

        if not pool:
            return None, "nothing measured"
        ranked = knee_ranked(pool, [lambda p: app_scored(p).holdout.avg_latency,
                                    lambda p: -app_scored(p).holdout.throughput,
                                    lambda p: app_scored(p).area_score,
                                    lambda p: app_scored(p).pad_fraction])
        return ranked[0], "the balanced pick: the knee over latency, throughput, area and padding"

    def conclusion(self, pick: Scored, decided_by: str) -> dict[str, Any]:
        from .flow import conclude_dict_safe

        return {"conclusion": conclude_dict_safe(list(self.memo.values())),
                "decision": pick.name, "decided_by": decided_by}


__all__ = ["ImappingProblem", "STAGE", "app_scored"]
