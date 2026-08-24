"""The interconnect_mapping WORLD (review 2 step R3, docs/decisions.md D539; the shape of D519
and D533): what `interconnect_mapping.problem.yaml` names once as its `world:` -- the study
of D378/D386 as the hooks of the document problem that runs it, one search generator
(D446) over the cycle law.

  batch 1     STAGE A finds interconnects (`interconnect_loop`); STAGE B builds the policy
              field (the curated catalog, the XOR hill-climb, bankmap's proven fold); the
              cross product policy x fabric is the batch -- every design point is a PAIR,
              because a hash is only as good as the fabric that carries it and vice versa.
  batch 1+k   a model round when the document asks for one: XOR taps as JSON, through the
              injectivity gate; the accepted policy paired with every found fabric.
  then        THE BIG LOOP (D386): coordination rounds alternate the two little loops --
              a mapping tuned per fabric, fabrics fitted per mapping -- until the front
              stops moving; every new pair is a batch through the same scorer.

Two stages the document declares: `analytic` -- TRAIN and HOLDOUT traffic through the cycle
law (the anti-overfitting split), four costs per pair and Pareto dominance over all four as
the frontier -- and `phys`, Yosys + OpenSTA on the finalists' distinct hash blocks and fabric
elements, grounding the gate-unit score in um2 (a screen, not a placement: D272), skipped
without the tools. Certificates by exhaustion are drawn over the frontier the loop returns
and reported with the conclusion.

What the DOCUMENT says: the ask (`params:` -- the seed, the traffic regime, the rounds, the
tiles to certify), the four objectives, the two stages, `cache: false`, the budget, the
campaign. Before R3 this was a `Problem` subclass built by the flow's `run_study` from its
keywords, with a demo on top; a new ask in this world is a copy of the document.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

from flux_loop import Candidate, LoopState, Scored, Verdict

from .fabric import FabricModel, xbar_full
from .flow import Certificate, Scored as PairScore, certify, conclude, pareto_front
from .model import Memory, Mode
from .solutions import Solution, catalog, injective
from .workloads import Workload, train_holdout

STAGE = "analytic"
PHYS = "phys"
CERTIFY_MODES = (Mode.Loop_Row_Col, Mode.Loop_Col_Row, Mode.Loop_4x4_H)


def app_scored(p: Scored) -> PairScore:
    """The study's own `Scored` pair, carried in the loop's `Scored.payload`."""
    return p.payload["scored"]


@dataclass(slots=True)
class Study:
    """The study in its own terms -- what the node returns and the tests read: every scored
    pair, the four-cost front, the certificates over it, the refusals in the study's words,
    the operator's notes -- derived from the loop's result by `World.study`, which is kept
    beside it with the problem that ran."""

    scored: list[PairScore]
    front: list[PairScore]
    certificates: list[Certificate]
    refused: list[str]
    notes: list[str] = field(default_factory=list)
    out: Any = None
    problem: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scored": [s.to_dict() for s in self.scored],
            "front": [s.pair_name for s in self.front],
            "certificates": [
                {"solution": c.solution, "mode": c.mode, "tile": list(c.tile),
                 "pitch": c.pitch, "holds": c.holds,
                 "checked_origins": c.checked_origins,
                 "counterexample": c.counterexample}
                for c in self.certificates],
            "refused": list(self.refused),
            "notes": list(self.notes),
        }


class World:
    """One banked-L1 conflict study as the hooks of the document problem that runs it: built
    from the document's `params:`; `objective`, `stages` and `cache_suffix` are the document's."""

    name = "interconnect_mapping"
    PARAMS = ("seed", "ops", "vu_probability", "dma_probability", "climb_rounds", "llm_rounds",
              "coordination_rounds", "certify_tiles", "bank_bits", "evaluator")

    def __init__(self, problem: Any) -> None:
        self.problem = problem
        p = dict(problem.task.params or {})
        unknown = sorted(set(p) - set(self.PARAMS))
        if unknown:
            raise ValueError(f"params {unknown} are not the interconnect-mapping study's; known: "
                             f"{', '.join(self.PARAMS)}")
        self.seed = int(p.get("seed") or 0)
        self.ops = int(p.get("ops") or 8)
        self.vu = float(p.get("vu_probability") if p.get("vu_probability") is not None else 0.7)
        self.dma = float(p.get("dma_probability") if p.get("dma_probability") is not None else 0.6)
        self.climb_rounds = int(p.get("climb_rounds") if p.get("climb_rounds") is not None else 40)
        self.llm_rounds = int(p.get("llm_rounds") or 0)
        self.coordination_rounds = int(p.get("coordination_rounds") if p.get("coordination_rounds") is not None else 2)
        tiles = p.get("certify_tiles") or [[8, 4, 1], [4, 16, 1]]
        self.certify_tiles = tuple(tuple(int(x) for x in t) for t in tiles)
        self.mem = Memory(m=int(p.get("bank_bits") or 5))
        self.train: list[Workload] = []
        self.holdout: list[Workload] = []
        self.pairs: dict[str, tuple[Solution, FabricModel]] = {}
        self.memo: dict[str, PairScore] = {}     # pair_name -> the study's Scored (deterministic)
        self.refused_text: list[str] = []        # the report's refusals, in the study's words
        self.fabrics: list[FabricModel] = []
        self.screens: dict[str, Any] = {}        # pair_name -> its `phys` screen (PairScreen)
        self._certs: dict[int, list[Certificate]] = {}
        self._mentor: Any = None                 # the declared sources (D449)

    # ---- mentor
    def prepare(self, state: LoopState) -> dict[str, Any]:
        self.train, self.holdout = train_holdout(self.seed, ops=self.ops,
                                                 vu_probability=self.vu,
                                                 dma_probability=self.dma)
        return {"train workloads": len(self.train), "holdout workloads": len(self.holdout),
                "banks": self.mem.banks}

    def knowledge(self) -> Any:
        """One source (D449): what earlier runs of this campaign measured, as duels over the
        pair's two real knobs. The tab and the proposer's prompt read the same text."""
        if self._mentor is None:
            from flux_knowledge import Mentor

            from .flow import record_readback

            self._mentor = Mentor([record_readback()])
        return self._mentor

    def analytic_stages(self) -> frozenset[str]:
        return frozenset({STAGE})

    def evaluator_name(self, stage: str) -> str:
        return "imapping@yosys-screen" if stage == PHYS else "imapping@cycle-law"

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
                           z3_mapping_policy)

        mem, train, holdout = self.mem, self.train, self.holdout
        field_ = catalog(mem)
        # STAGE A (D392): find interconnects first -- the interconnect little-loop generates
        # ~30 parameterized candidates and keeps the screen-front under a reference mapping.
        ref = next(s_ for s_ in field_ if s_.name == "S1-xor-global")
        self.fabrics = fabrics = interconnect_loop(train, holdout, mem, ref, say=state.say)
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
        z3_policy = z3_mapping_policy(train, mem, say=state.say)
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
                                 seed=self.seed + 100 + round_i)
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
        from flux_llm import strip_markdown_fence

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
            reply = json.loads(strip_markdown_fence(state.proposer.propose(prompt).text))
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

    # ---- evaluator: the gate admits every built pair; the stages are the cycle law and the screen
    def build(self, cand: Candidate, subgoal: str | None, state: LoopState) -> Any:
        return self.pairs[cand.name]

    def judge(self, built: Any, cand: Candidate, subgoal: str | None,
              state: LoopState) -> Verdict:
        return Verdict(True, 0.0)

    def _numbers(self, s: PairScore) -> dict[str, Any]:
        return {"holdout_latency": s.holdout.avg_latency,
                "holdout_throughput": s.holdout.throughput,
                "area_score": float(s.area_score), "pad_fraction": s.pad_fraction,
                "train_latency": s.train.avg_latency, "scored": s}

    def measure_batch(self, cands: list[Candidate], stage: str, state: LoopState
                      ) -> list[dict[str, Any] | None]:
        from flux_profile import phase as _tphase

        from .flow import score

        out: list[dict[str, Any] | None] = []
        if stage == PHYS:
            # the finalists' distinct hash blocks and fabric elements through Yosys + OpenSTA,
            # once each (D272: composed um2 is a screen, and says so wherever it is printed)
            from .phys import screen_pairs

            pairs = [self.memo[c.name] for c in cands if c.name in self.memo]
            with _tphase("screen: hash blocks and fabric elements", why=f"{len(pairs)} finalist(s)"):
                try:
                    screens = screen_pairs(pairs)
                except Exception as exc:  # noqa: BLE001
                    return [{"error": f"{PHYS} failed: {type(exc).__name__}: {exc!s:.200}"} for _ in cands]
            self.screens.update(screens)
            for c in cands:
                s = self.memo.get(c.name)
                sc = screens.get(c.name)
                if s is None or sc is None:
                    out.append({"error": f"{c.name}: not screened"})
                    continue
                m = self._numbers(s)
                m["worst_slack_ps"] = float(sc.worst_slack_ps)
                if sc.composed_um2 is not None:
                    m["area_um2"] = float(sc.composed_um2)
                state.say(f"  {c.name}: worst slack {sc.worst_slack_ps:.0f} ps"
                          + (f", composed {sc.composed_um2:.0f} um2" if sc.composed_um2 is not None else "")
                          + ("" if sc.meets_600mhz else " -- misses 600 MHz"))
                out.append(m)
            return out
        for c in cands:
            s = self.memo.get(c.name)
            if s is None:
                sol, fabric = self.pairs[c.name]
                with _tphase("score: policy x fabric", why=c.name):
                    s = score(sol, fabric, self.train, self.holdout, self.mem)
                self.memo[c.name] = s
            out.append(self._numbers(s))
        return out

    # ---- the frontier and the decision: four costs
    def frontier(self, scored: list[Scored], state: LoopState) -> list[Scored]:
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

    # ---- the study in its own terms (what the flow's `ConflictStudy` was), the node's and the tests'
    def certificates(self, out: Any) -> list[Certificate]:
        """Certificates by exhaustion over the front the loop returned (three modes x the
        document's tiles), computed once per result."""
        key = id(out)
        if key not in self._certs:
            from flux_profile import phase as _tphase

            front = [app_scored(p) for p in out.frontier]
            certs: list[Certificate] = []
            with _tphase("prove: certificates by exhaustion",
                         why=f"{len(front)} frontier pairs x {len(CERTIFY_MODES)} modes x {len(self.certify_tiles)} tiles"):
                for s in front:
                    for mode in CERTIFY_MODES:
                        for tile in self.certify_tiles:
                            certs.append(certify(s.solution, self.mem, mode=mode, rt=tile[0],
                                                 ct=tile[1], lt=tile[2], dim=64,
                                                 fabric=s.fabric, label=s.pair_name))
            self._certs[key] = certs
        return self._certs[key]

    def study(self, out: Any) -> Study:
        return Study(scored=[app_scored(p) for p in out.scored],
                     front=[app_scored(p) for p in out.frontier],
                     certificates=self.certificates(out),
                     refused=list(self.refused_text), notes=list(out.notes),
                     out=out, problem=self.problem)

    def conclude(self, out: Any) -> dict[str, Any]:
        """The decision-first summary derived from the measured field, with the certificates."""
        st = self.study(out)
        return conclude(st.scored, st.front, st.certificates)

    def report(self, out: Any) -> list[str]:
        st = self.study(out)
        c = self.conclude(out)
        lines: list[str] = []
        if "note" in c:
            return [f"  {c['note']}"]
        bal = c.get("balanced_pick") or {}
        lines.append("  DECISION -- the balanced pick (the knee over latency, throughput, area, padding)")
        lines.append(f"    {bal.get('pair')}: {bal.get('latency', 0):.2f} cy, {bal.get('throughput', 0):.2f} rows/cy, "
                     f"{bal.get('area_score', 0):.0f} areaU, padding {bal.get('pad_fraction', 0):.3f}")
        for label, key in (("latency corner", "latency_corner"), ("throughput corner", "throughput_corner"),
                           ("area corner", "area_corner")):
            corner = c.get(key) or {}
            if corner.get("pair"):
                lines.append(f"    {label:<18} {corner['pair']}")
        if c.get("consensus_fabric"):
            lines.append(f"    consensus fabric   {c['consensus_fabric']}")
        proved = [x for x in st.certificates if x.holds]
        refuted = [x for x in st.certificates if not x.holds]
        lines.append(f"  CERTIFICATES  {len(proved)} proved by exhaustion, {len(refuted)} refuted with a counterexample")
        for x in proved[:6]:
            lines.append(f"    proved   {x.solution}: {x.mode} tile {list(x.tile)} ({x.checked_origins} origins)")
        if self.screens:
            lines.append(f"  PHYS SCREEN ({len(self.screens)} finalist(s), Yosys + OpenSTA at 600 MHz; composed um2 is a screen, D272)")
            for name, sc in self.screens.items():
                lines.append(f"    {'ok     ' if sc.meets_600mhz else 'MISSES '} {name}: slack {sc.worst_slack_ps:.0f} ps"
                             + (f", composed {sc.composed_um2:.0f} um2" if sc.composed_um2 is not None else ""))
        losers = c.get("losers") or {}
        if losers:
            lines.append(f"  OFF THE FRONT ({len(losers)})")
            for name, why in list(losers.items())[:8]:
                lines.append(f"    {name}: {why}")
        return lines


__all__ = ["CERTIFY_MODES", "PHYS", "STAGE", "Study", "World", "app_scored"]
