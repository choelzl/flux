"""The interconnect study as a `flux_loop` problem (D446): the orchestrator's own menu is the
search policy, and the decision stage is the loop's chain.

    search    the model DIRECTS, one step at a time, from the menu `flow.build_menu` registers
              (enumerate a family, anneal over the screen, propose, repair a near miss, place one
              fabric). Each step runs a whole campaign through the agent-surface tools, so its
              candidates are screened and escalated in the store rather than through the loop's
              own gate -- the step yields nothing to gate, and the loop counts it as a step.
    gate      the fabrics that survive: routable, carrying every client, and clearing the
              frequency floor once the composed estimate is corrected for the measured
              composed-to-placed bias (D318/D322/D324)
    place     THE DECISION STAGE (D280): each finalist placed WHOLE with real Yosys and OpenROAD
              on the vendored `xbar_varlat` core, and real Verilator for throughput
    decide     the most throughput per mm2 among the finalists that clear the floor on placed
              silicon; nothing clearing it is NO DECISION, not a downgraded answer

One stage on purpose. The composed screen is the campaign's stage, already measured and recorded
there with its own provenance; re-recording it here would double-count fabrics in a store that
counts distinct candidates. What the loop adds is the stage nothing else recorded: the whole-fabric
placements the decision is made on, written under the phase `decide` so the campaign's own
`escalate` tables stay exactly what they were.
"""

from __future__ import annotations

from typing import Any, Iterator

from flux_loop import (BuildError, Candidate, DirectedSearch, LoopState, Problem, Scored,
                       SearchReport, Verdict)

from .flow import (
    BARREN_ROUNDS, CLIENTS, BANKS, SCOPE_KEYS, TARGET_MHZ, WIDTH_BITS, StudyContext,
    _decision_cache, _mined_facts, _structural_key, _variant_lookup, already_enumerated,
    author_problem, build_menu, check_toolchain_drift, choose_finalists, dead_note, dead_ports,
    frontier_digest, learn_from_placement, measure_throughput_cached, placed_whole,
    print_memory_served, print_search_summary, qualifying_fabrics, step_cap, study_result,
)
from .study import InterconnectRequest, InterconnectResult

PLACE = "place"          # the one stage: a whole-fabric placement, the number a report may quote


class InterconnectProblem(Problem):
    """The study's hooks. `flow.run_study` builds it, runs the loop and reads an
    `InterconnectResult` back; the CHIA node and the demo never see this class."""

    name = "interconnect"

    def __init__(self, request: InterconnectRequest) -> None:
        self.request = request
        self.results: list[dict] = []          # one per round the menu ran
        self.attempted_at_start = 0
        self.series = 1
        #: the menu policy's report; never None, so a run that ends before its first step
        #: still has a result to hand back rather than an AttributeError at the very end
        self.report_so_far = SearchReport(steps=[], unexplored=[], stopped_because="not started")
        self.drawn: list[str] = []             # conclusions a model drew from this run
        self.measured: dict[str, dict] = {}
        self.rejected: dict[str, float] = {}
        self.qualifying: dict[str, dict] = {}
        self.corrected: dict[str, tuple[float, str]] = {}
        self.specs: dict[str, dict] = {}       # label -> the fabric's variant document
        self.placed: list[tuple[str, dict, float, dict]] = []
        self.decision: tuple[str, dict, float, dict] | None = None

    # ---- mentor
    def objective(self, request: Any) -> dict[str, Any]:
        return {"study": "interconnect", "clients": CLIENTS, "banks": BANKS,
                "width_bits": WIDTH_BITS, "target_mhz": TARGET_MHZ, "stage": PLACE}

    def tools_missing(self) -> list[str]:
        """A missing binary does not degrade the result, it removes it: every number this study
        prints comes from one of these tools, so it refuses with the shell that carries them
        rather than with a list of names."""
        from flux_evaluator_abi.preflight import require_tools

        from .flow import REQUIRED_TOOLS

        require_tools(REQUIRED_TOOLS,
                      hint="nix develop .#physical --command python3 "
                           "applications/interconnect/demo.py")
        return []

    def cache_suffix(self) -> str | None:
        return None                            # `placed_whole` owns the placement cache (D340)

    def stages(self) -> list[str]:
        return [PLACE]

    def evaluator_name(self, stage: str) -> str:
        from flux_evaluator_interconnect_phys.adapter import EVALUATOR_ID

        return EVALUATOR_ID

    def prepare(self, state: LoopState) -> None:
        from flux_profile import reset as _reset
        from flux_search_campaign import campaign_tally

        from .flow import next_proposal_series

        _reset()  # the measurement window is this run, not this interpreter
        args = self.request
        if args.problem:
            # THE AUTHOR ROLE (D333). The study's five numbers come from a sentence rather than
            # from this file. Globals, because every function in `flow` reads them and threading
            # a problem object through all of them would be a larger change than the capability
            # is worth today -- stated plainly rather than hidden, and set once before anything
            # reads them.
            from . import flow

            if state.proposer is None:
                raise SystemExit("--problem needs a local model; none was reachable")
            print(f"reading the problem from: {args.problem!r}")
            authored = author_problem(args.problem, state.proposer)
            flow.CLIENTS, flow.BANKS = authored["clients"], authored["banks"]
            flow.WIDTH_BITS, flow.BANK_ROWS = authored["width_bits"], authored["bank_rows"]
            flow.TARGET_MHZ = float(authored["target_mhz"])
            print(f"    understood as: {flow.CLIENTS} clients x {flow.WIDTH_BITS}b -> "
                  f"{flow.BANKS} banks x {flow.BANK_ROWS} rows, >= {flow.TARGET_MHZ:.0f} MHz")
        print(f"problem: {CLIENTS} clients x {WIDTH_BITS}b -> {BANKS} banks "
              f"({self._bank_rows()} rows each), CONCURRENT access")
        print(f"goal:    minimize fabric area, keep >= {TARGET_MHZ:.0f} MHz, and carry all "
              f"{CLIENTS} clients at once (a fabric with a narrower waist is refused, not ranked)")
        print(f"store:   {args.db}")
        check_toolchain_drift(args.db)
        self.attempted_at_start = campaign_tally(args.db)["attempted"]
        self.series = next_proposal_series(args.db)
        if state.records is not None:
            # The loop's own rows are the placements, under their own phase: the campaign's
            # `escalate` tables (and every tally that reads them) stay exactly what they were.
            state.records.phase("decide")

    @staticmethod
    def _bank_rows() -> int:
        from . import flow

        return flow.BANK_ROWS

    # ---- orchestrator: the menu directs, then the finalists are chosen
    def search(self, state: LoopState) -> Iterator[list[Candidate]]:
        args = self.request
        ask = state.proposer
        # TWO ROLES, both local models (docs/decisions.md D287). The ORCHESTRATOR decides what
        # the search does next; the GENERATOR designs the fabrics. That split is this project's
        # own architecture rather than an embellishment, and it is the difference between a model
        # that fills in a fixed plan and one that runs the study.
        #
        # The orchestrator directs; it cannot quietly shrink the study. Every scope it does not
        # run is reported below, so an exploration that covered less says so out loud. A DIRECTED
        # run stops when the orchestrator says so, not after a fixed number of steps (D291):
        # once every scope is enumerated, `propose` is the only step that can still find
        # anything, and it is worth repeating. `max_rounds` is a runaway guard, not a plan. An
        # undirected run still follows the fixed schedule exactly.
        ctx = StudyContext(args=args, ask=ask, results=self.results, series=self.series)
        prior = already_enumerated(args.db)
        if prior:
            print(f"resuming: {len(prior)} of {len(SCOPE_KEYS)} families already enumerated in "
                  f"this store ({', '.join(prior)})")
        policy = DirectedSearch(
            build_menu(ctx), ask=ask,
            cap=step_cap(args.rounds, args.max_rounds, directed=ask is not None),
            barren_limit=BARREN_ROUNDS,
            problem=(f"{CLIENTS} clients of {WIDTH_BITS} bits must reach {BANKS} banks at the "
                     f"same time, at >= {TARGET_MHZ:.0f} MHz, in the least silicon"),
            frontier=lambda: frontier_digest(args.db),
            lessons=lambda: _lessons(args.db),
            done=prior)
        self.report_so_far = policy.report
        for _record in policy.steps():
            self.report_so_far = policy.report
            yield []          # the step's candidates were screened in the store, not here
        self.report_so_far = policy.report
        self.drawn = print_search_summary(args, policy.report, ask, self.attempted_at_start)
        self.measured, self.rejected, self.qualifying, self.corrected = qualifying_fabrics(args)
        yield self._finalists(state)

    def _finalists(self, state: LoopState) -> list[Candidate]:
        """Which fabrics earn a whole-fabric placement.

        Chosen by the OBJECTIVE, which is to minimise area -- not by throughput. An earlier cut
        ranked finalists by `served` descending, so a study whose stated goal is the least
        silicon placed its five fastest fabrics and never placed its smallest one at all. The
        decision stage then answered a question the demo was not asking, and reported NO DECISION
        without the area winner ever having been tried on the vendored core.
        """
        args = self.request
        if not (args.decide_on_finalists and self.qualifying):
            return []
        by_area = sorted(self.qualifying.items(), key=lambda kv: kv[1]["area_mm2"])
        # One placement per FABRIC, not per label. A hybrid layer of radix-4 over 28 clients IS
        # 7x(4x4), so `hybrid-radixradix4-xbarswitches4` and `xbar_staged-7x4x4-4x7x8` are the
        # same silicon by two names -- measured identically to four decimals, and placed three
        # times because the shortlist counted labels. Cross-family duplicates appeared when
        # rounds became family-partitioned (D308): enumeration deduplicates structurally WITHIN
        # a call, and families are now separate calls.
        finalists = choose_finalists(by_area, args.decide_on_finalists, _structural_key)
        # The frontier's other end is carried too, when there is room: the fastest fabric is not
        # the answer to this objective, but a decision stage that shows only one end of a
        # trade-off hides the shape of it.
        leader = max(self.qualifying.items(), key=lambda kv: kv[1]["served"])
        if leader[0] not in {k for k, _ in finalists} and len(finalists) > 1:
            finalists = finalists[:-1] + [leader]
        print(f"\n=== DECISION STAGE: {len(finalists)} finalists placed WHOLE on vendored "
              f"xbar_varlat (real Yosys + OpenROAD, every inter-switch wire included)")
        print("    ranked by AREA, the objective; the throughput leader is carried for contrast")
        print("    `dead` counts boundary ports wired to nothing -- inputs beyond the 28 clients, "
              "outputs beyond the 32 banks.")
        print("    They are not refused: 717 of 1,184 feasible fabrics have some, and the "
              "cheapest routable one has four. Synthesis")
        print("    optimises them away, so the screen charges 8-14% more mux bits for them and "
              "the silicon does not.")
        print(f"  {'topology':<38} {'mm2':>8} {'MHz':>6} {'mW':>7} "
              f"{'served':>7} {'cyc':>4} {'dead':>5}  {'screen said':>12}")
        out = []
        for label, row in finalists:
            spec = _variant_lookup.get(label)
            if spec is None:
                continue
            self.specs[label] = spec
            out.append(Candidate(name=label, knobs={"label": label, "variant": spec},
                                 meta={"strategy": "decide", "composed": dict(row)}))
        return out

    # ---- evaluator: the gate, and the one stage
    def build(self, cand: Candidate, subgoal: str | None, state: LoopState) -> Any:
        """A fabric that cannot be built is not a finalist -- and one built before routability
        was checked (D324) must not reach a placement either."""
        from flux_evaluator_interconnect_struct.adapter import _unroutable_reason
        from flux_interconnect import build

        try:
            topo = build(self.specs[cand.name])
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"cannot be built: {type(exc).__name__}: {exc!s:.120}") from None
        why = _unroutable_reason(topo)
        if why:
            raise BuildError(f"cannot deliver to every bank: {why}")
        return topo

    def judge(self, built: Any, cand: Candidate, subgoal: str | None,
              state: LoopState) -> Verdict:
        return Verdict(True, 0.0)

    def measure_batch(self, cands: list[Candidate], stage: str, state: LoopState
                      ) -> list[dict[str, Any] | None]:
        """THE DECISION STAGE (docs/decisions.md D280). Everything above it is a screen: the
        composed stage prices each selector arity in isolation and multiplies by count, so it sees
        no wire between switches at all, and it does not preserve the ORDER (D272). This places
        each finalist WHOLE -- and on the vendored PULP `xbar_varlat` core rather than this
        repo's generated switch, which measured 18% to 65% slower and ~30% larger on every family
        (D279). A ranking is only worth as much as the worst implementation in it, so the
        decision is made on the better one.

        Printed as each finalist lands, not batched at the end: a whole-fabric placement is
        minutes, so a silent half hour tells the operator nothing about whether it is working.
        """
        from flux_profile import phase as _phase

        from flux_interconnect import build

        args = self.request
        out: list[dict[str, Any] | None] = []
        for cand in cands:
            label = cand.name
            spec = self.specs[label]
            composed = dict(cand.meta.get("composed") or {})
            try:
                with _phase("decision stage: one whole placement"):
                    whole = placed_whole(args.db, spec, timeout_s=5400)
                served = measure_throughput_cached(build(spec), cache=_decision_cache(args.db))
            except Exception as exc:  # noqa: BLE001 -- a failed placement is data, not a crash
                print(f"  {label:<38} vendored placement failed: {str(exc)[:50]}")
                out.append({"error": f"vendored placement failed: {str(exc)[:120]}"})
                continue
            self.placed.append((label, whole, served, composed))
            learn_from_placement(args.db, label, composed, whole, spec=spec)
            din, dout = dead_ports(spec)
            dead = f"{din}i/{dout}o" if (din or dout) else "-"
            print(f"  {label:<38} {whole['area_mm2']:>8.4f} {whole['fmax_mhz']:>6.0f} "
                  f"{whole['power_total_w']*1e3:>7.1f} {served:>7.1f} "
                  f"{composed.get('cycles', 0):>4.0f} {dead:>5}  "
                  f"{composed.get('area_mm2', 0):>7.4f}mm2", flush=True)
            out.append({"area_mm2": float(whole["area_mm2"]),
                        "fmax_mhz": float(whole["fmax_mhz"]),
                        "served_per_cycle": float(served),
                        "power_w": float(whole.get("power_total_w") or 0.0),
                        "latency_cycles": float(composed.get("cycles") or 0),
                        "words_per_cycle_per_mm2": served / max(float(whole["area_mm2"]), 1e-9)})
        return out

    # ---- the frontier and the decision
    def frontier_axes(self):
        return (lambda p: p.metrics["served_per_cycle"], lambda p: p.metrics["area_mm2"])

    def decide(self, pool: list[Scored], state: LoopState) -> tuple[Scored | None, str]:
        """Meet the floor, then the most throughput per mm2 of fabric -- the shared
        target-and-floor rule (`flux_frontier`, D399), with the fallback tag read as NO DECISION
        because this study refuses rather than downgrades when nothing makes timing."""
        from flux_frontier import cheapest_meeting

        if not pool:
            return None, "nothing was placed"
        pick, rule = cheapest_meeting(
            pool, cost=lambda p: -p.metrics["words_per_cycle_per_mm2"],
            value=lambda p: p.metrics["fmax_mhz"], floor=TARGET_MHZ)
        if rule != "cheapest-meeting":
            print(f"\n  NO DECISION: none of the finalists clears {TARGET_MHZ:.0f} MHz "
                  "once placed whole. The screen's frequencies do not survive placement, "
                  "which is a result about the screen, not a shortlist.")
            return None, (f"no finalist clears {TARGET_MHZ:.0f} MHz once placed whole")
        self.decision = next(r for r in self.placed if r[0] == pick.name)
        spec = self.specs.get(pick.name)
        print(f"\n  DECISION: {pick.name}{dead_note(spec)}")
        print(f"    {pick.metrics['served_per_cycle']:.1f} words/cycle at "
              f"{pick.metrics['area_mm2']:.4f} mm2 and {pick.metrics['fmax_mhz']:.0f} MHz "
              f"({pick.metrics['power_w']*1e3:.1f} mW) -- "
              f"{pick.metrics['words_per_cycle_per_mm2']:.0f} words/cycle per mm2, the best "
              f"ratio among finalists that clear {TARGET_MHZ:.0f} MHz on placed silicon.")
        return pick, "the most throughput per mm2 among finalists that clear the floor"

    def conclusion(self, pick: Scored, decided_by: str) -> dict[str, Any]:
        return {"decision": pick.name, "decided_by": decided_by,
                "area_mm2": round(pick.metrics["area_mm2"], 4),
                "fmax_mhz": round(pick.metrics["fmax_mhz"], 1),
                "served_per_cycle": round(pick.metrics["served_per_cycle"], 2), "stage": PLACE}

    # ---- the result
    def report(self, out: Any) -> InterconnectResult:
        print_memory_served(self.request, self.qualifying, self.results)
        result = study_result(self.request, self.report_so_far, self.placed, self.decision,
                              self.rejected, self.drawn, self.results)
        for label, why in out.refused:
            result.refused.setdefault(label, why)
        result.not_established.extend(n for n in out.not_established
                                      if not n.startswith("nothing was measured"))
        return result


def _lessons(db: str) -> str:
    from flux_knowledge_mining import lessons_digest

    return lessons_digest(db, _mined_facts(db))


__all__ = ["InterconnectProblem", "PLACE"]
