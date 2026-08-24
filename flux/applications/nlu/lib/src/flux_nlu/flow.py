"""The NLU study (D408): the model designs a non-linear unit, the tools judge it.

    setup       verilator + yosys (openroad for the confirm stage), the campaign
                record, the operator's reloaded notes, the accumulated test suite
    author      the model writes the unit tests: adversarial FP16 vectors per
                operator, validated, merged over the framework's coverage floor,
                persisted -- a resumed run KEEPS its test suite and grows it
    field       designs from the record (previous runs' sources re-enter, cached
                measurements make them nearly free) plus fresh model proposals
    prove       EXHAUSTIVE correctness: all 65536 inputs per operator through
                Verilator, ULP distance against the FP16 reference -- <= budget or
                refused with the failing inputs attached (they feed the repair
                prompt and the record)
    screen      yosys + STA: area and fmax, seconds each, cached by tool
                fingerprints and source
    frontier    area vs fmax over the survivors (error is a gate, never a trade)
    confirm     OpenROAD placement on finalists spread along the frontier: the PPA
                (area, fmax, power) the report quotes
    decide      the shared arithmetic (flux_frontier): the target-and-floor rule when
                a clock is demanded, the knee otherwise
    report      decision first, per-operator error table, refusals with reasons,
                what is not established

Claude built this rig; the MODEL running in it picks methods (LUT, piecewise
polynomial, Newton-Raphson, CORDIC, ...), sharing, and pipelining -- see invent.py
for the bargain, knowledge.py for the seed it is taught from.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .fp16 import OPCODES, all_inputs, ulp_report
from .invent import (design_prompt, improve_prompt, parse_design, repair_prompt,
                     test_author_prompt)
from .knowledge import knowledge_text
from .vectors import floor_vectors, merge, parse_authored
from .verify import CompileError, build_sim, tools_missing

DEFAULT_OPS = tuple(OPCODES)


@dataclass(frozen=True)
class NluRequest:
    """One NLU study. Field names match demo.py's flags."""

    db: str = "demo-nlu.db"
    ops: tuple[str, ...] = DEFAULT_OPS
    ulp_budget: int = 1
    llm_rounds: int = 4
    test_rounds: int = 1
    repair_attempts: int = 12     # keep fixing one design before starting fresh (D411)
    explore_every: int = 4        # 1 in N rounds starts fresh instead of reworking best (D411)
    agentic: bool = True          # D412: plan one operator at a time, admit and freeze
    op_steps: int = 24            # planner steps per pass in agentic mode
    structured: bool = True       # D413: schema-constrained decoding for design/plan
    patching: bool = True         # D414: repair by find/replace edits, not rewrites
    prototype: bool = True        # D424: prove the method in Python first; False = RTL directly (D472)
    floor: bool = False           # D475/D478: hand-written families -- NOT the flow's own work; off
    regenerate: tuple[str, ...] = ()   # D476: operators to draft again, not resume ("*" = all)
    cooldown_after: int = 3       # consecutive failures before an op yields the floor
    clock_period_ps: float = 1250.0        # what the tools are constrained to
    target_mhz: float | None = None        # a demanded clock; None = report the knee
    decide_on_finalists: int = 3
    screen_only: bool = False
    seed: int = 0


@dataclass(frozen=True)
class Scored:
    candidate: dict[str, Any]              # name/style/latency/method(s)/source
    per_op: dict[str, dict[str, Any]]      # op -> ulp_report summary
    area_um2: float
    worst_slack_ps: float
    clock_period_ps: float
    power_w: float
    flow_depth: str                        # "synthesis" or "placement"

    @property
    def name(self) -> str:
        return self.candidate["name"]

    @property
    def fmax_mhz(self) -> float:
        path = self.clock_period_ps - self.worst_slack_ps
        return 1e6 / path if path > 0 else float("inf")

    @property
    def max_ulp(self) -> int:
        return max((r["max_ulp"] if isinstance(r["max_ulp"], int) else 1 << 17)
                   for r in self.per_op.values())

    @property
    def error_rate(self) -> float:
        return max(r["error_rate"] for r in self.per_op.values())

    def to_dict(self) -> dict[str, Any]:
        c = dict(self.candidate)
        c.pop("source", None)              # provenance JSON stays readable; the
        return {                           # source lives in the campaign record
            "candidate": c, "area_um2": self.area_um2, "fmax_mhz": self.fmax_mhz,
            "power_w": self.power_w, "flow_depth": self.flow_depth,
            "per_op": self.per_op,
        }


@dataclass(frozen=True)
class NluResult:
    decision: Scored | None = None
    decided_by: str = ""
    frontier: list[Scored] = field(default_factory=list)
    confirmed: list[Scored] = field(default_factory=list)
    scored: list[Scored] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    not_established: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- study state
@dataclass
class _Study:
    request: NluRequest
    say: Callable[[str], None]
    proposer: Any
    feedback: Any
    records: Any = None
    cache: Any = None                      # MeasurementCache for synth/place (D340)
    workdir: str = ""
    vectors: dict[str, np.ndarray] = field(default_factory=dict)   # op -> screen set
    scored: list[Scored] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    not_established: list[str] = field(default_factory=list)
    human_notes: list[Any] = field(default_factory=list)
    seen_sources: set[str] = field(default_factory=set)
    closest: Any = None                   # best refused design, offered back (D411)
    started: float = 0.0

    def drain(self) -> str | None:
        from flux_feedback import drain_guidance, guidance_lesson, note_sink

        reaches = "the next proposal prompt" if self.proposer is not None else None
        on_note = note_sink(self.records,
                            lambda t: self.lessons.append(guidance_lesson(t, reaches=reaches)))
        return drain_guidance(self.feedback, self.human_notes, on_note=on_note)


def _ask(s: _Study, prompt: str, schema: dict | None = None) -> str:
    """One model call, schema-constrained when the proposer supports it (D413).
    A proposer without the parameter (a test stub, an older backend) still works."""
    from flux_llm import propose

    return propose(s.proposer, prompt, schema, structured=s.request.structured)


def _phase(name: str, why: str = "", **params: Any):
    from flux_profile import phase

    return phase(name, why=why, **params)


# ---------------------------------------------------------------- the stages
def _wrap_per_op(source: str, ops: tuple[str, ...]) -> str:
    from .problem import wrap_per_op

    return wrap_per_op(source, ops)


def _correctness(s: _Study, cand: dict[str, Any]) -> tuple[dict[str, dict], str | None]:
    """The proof stage: exhaustive ULP per operator. Returns (per-op reports, None) or
    (partial reports, refusal text carrying the worst counterexamples)."""
    ops = s.request.ops
    top_source = (cand["source"] if cand["style"] == "shared"
                  else _wrap_per_op(cand["source"], ops))
    xs = all_inputs()
    per_op: dict[str, dict] = {}
    for op in ops:
        with _phase(f"prove: exhaustive ULP ({op})", why=cand["name"],
                    inputs=len(xs)):
            try:
                sim = build_sim(top_source, top="nlu", latency=cand["latency"],
                                opcode=OPCODES[op], workdir=s.workdir)
                got = sim.run(xs)
            except CompileError as exc:
                return per_op, f"verilator refused the source:\n{exc}"
            except Exception as exc:  # noqa: BLE001
                return per_op, f"simulation failed on {op}: {exc}"
        rep = ulp_report(op, xs, got, budget=s.request.ulp_budget)
        per_op[op] = rep
        if not rep["ok"]:
            from .fp16 import describe_failures

            return per_op, (
                f"{op}: {rep['over_budget']} of {rep['n']} inputs beyond "
                f"{s.request.ulp_budget} ULP. " + describe_failures(op, rep))
    return per_op, None


def _measure(s: _Study, cand: dict[str, Any], per_op: dict[str, dict],
             stage: str) -> Scored | None:
    """Screen (synthesis) or confirm (placement) through the shared openroad flow."""
    from flux_evaluator_openroad import run_ppa_flow, run_synthesis_flow

    top_source = (cand["source"] if cand["style"] == "shared"
                  else _wrap_per_op(cand["source"], s.request.ops))
    kw = dict(clock_port="clk" if cand["latency"] > 0 else None,
              clock_period_ps=s.request.clock_period_ps)
    ident = (f"nlu/{stage}/{s.request.clock_period_ps:.0f}ps/"
             f"{hashlib.sha256(top_source.encode()).hexdigest()[:16]}")

    def _run_tools() -> dict[str, Any]:
        try:
            if stage == "screen":
                rep = run_synthesis_flow(top_source, "nlu", **kw)
            else:
                rep = run_ppa_flow(top_source, "nlu", flow_depth="placement",
                                   repair_design=True, **kw)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        return {"area_um2": rep.area_um2, "worst_slack_ps": rep.worst_slack_ps,
                "clock_period_ps": rep.clock_period_ps,
                "power_w": rep.power_total_w or 0.0, "flow_depth": rep.flow_depth}

    label = f"{stage}: {'place' if stage == 'confirm' else 'synth'} {cand['name']}"
    with _phase(label, why=f"latency {cand['latency']}, {cand['style']}"):
        got = (s.cache.get_or_measure(ident, _run_tools)
               if s.cache is not None else _run_tools())
    if "error" in got:
        s.refused.append((cand["name"], f"{stage} failed: {got['error']}"))
        return None
    return Scored(candidate=cand, per_op=per_op, area_um2=float(got["area_um2"]),
                  worst_slack_ps=float(got["worst_slack_ps"]),
                  clock_period_ps=float(got["clock_period_ps"]),
                  power_w=float(got["power_w"]),
                  flow_depth=str(got["flow_depth"]))


def _note_closest(s: _Study, cand: dict[str, Any], per_op: dict[str, dict],
                  why: str) -> None:
    """Remember the closest-yet REFUSED design (fewest over-budget inputs): the next
    design prompt offers its source back, so a near-miss is extended rather than
    rediscovered from a blank page (measured need: the model regenerated its own
    day-old refuted LUTs almost byte-for-byte)."""
    over = sum(r.get("over_budget", 1 << 20) for r in per_op.values()) if per_op \
        else (1 << 20)
    best = getattr(s, "closest", None)
    if best is None or over < best["over"]:
        s.closest = {"over": over, "cand": cand, "why": why[:300]}


def _admit(s: _Study, cand: dict[str, Any], *, provenance: str) -> bool:
    """One candidate through prove + screen; refusals recorded with their reasons."""
    src_key = hashlib.sha256(cand["source"].encode()).hexdigest()[:16]
    if src_key in s.seen_sources:
        s.refused.append((cand["name"], "identical to an already-judged source"))
        return False
    s.seen_sources.add(src_key)
    per_op, why = _correctness(s, cand)
    if why is not None:
        s.refused.append((cand["name"], why))
        _record_trial(s, cand, None, error=why)
        if "beyond" in why:                      # it compiled and was judged: a near-miss
            _note_closest(s, cand, per_op, why)
        return False
    scored = _measure(s, cand, per_op, "screen")
    if scored is None:
        _record_trial(s, cand, None, error=f"screen refused ({provenance})")
        return False
    s.scored.append(scored)
    _record_trial(s, cand, scored)
    s.say(f"  admitted {cand['name']}: max {scored.max_ulp} ULP, "
          f"{scored.area_um2:.0f} um2, {scored.fmax_mhz:.0f} MHz "
          f"({cand['style']}, latency {cand['latency']}, {provenance})")
    return True


# ---------------------------------------------------------------- the record
def _knobs(cand: dict[str, Any]) -> dict[str, Any]:
    return {"style": cand["style"], "method": cand["method"],
            "latency": cand["latency"]}


def _record_trial(s: _Study, cand: dict[str, Any], scored: Scored | None,
                  error: str | None = None) -> None:
    if s.records is None:
        return
    payload = {**_knobs(cand), "name": cand["name"],
               "methods": cand.get("methods", {}), "source": cand["source"]}
    if scored is None:
        s.records.trial(payload, cand["name"], stage="gate", strategy="llm",
                        metrics=None, error=(error or "refused")[:400],
                        analytic=False, evaluator="nlu@exhaustive-ulp")
    else:
        s.records.trial(payload, cand["name"], stage="screen", strategy="llm",
                        metrics={"fmax_mhz": scored.fmax_mhz,
                                 "area_um2": scored.area_um2,
                                 "error_rate": scored.error_rate},
                        analytic=True, evaluator="yosys+opensta@screen")


def _attempts_so_far(records) -> list[str]:
    """Refusals are MEASUREMENTS OF ATTEMPTS, not verdicts on approaches (D418m, Cedric:
    "the record might just have had a bad design; refining might reach the target"). So
    they read as how far each attempt got, best first, with the one being refined named
    as such -- never as mistakes to avoid."""
    tried = []
    for c, why in records.refusals(stage="gate", limit=12):
        over = c.get("over_budget")
        if not isinstance(over, int):
            m = re.search(r"(\d+) (?:of \d+ (?:inputs )?beyond|over budget)", why or "")
            over = int(m.group(1)) if m else None
        tried.append((over if over is not None else 10**9, c.get("op") or "",
                      c.get("name", "?"), c.get("method", ""), why))
    tried.sort()
    best_per_op: dict[str, int] = {}
    for over, op, name, method, why in tried:
        if op not in best_per_op:
            best_per_op[op] = over
    lines = []
    for over, op, name, method, why in tried[:6]:
        tag = " -- the current best, being refined" if best_per_op.get(op) == over else ""
        score = f"{over} of 65536 over" if over < 10**9 else "refused"
        lines.append(f"attempt {name}{f' ({method})' if method else ''}: {score}{tag}")
    return lines


def mentor_for(ops: tuple[str, ...]):
    """This study's declared knowledge sources (D449): the method sheet, the operator's own
    papers, and the record's read-back with its duels and its refused-attempt lines. One
    definition, read by `NluProblem.knowledge` (the loop's path) and by the enumeration path's
    own design rounds -- and memoised per `ops`, because the sheet and the library cannot
    change during a run and the library lookup is a BM25 pass over the corpus."""
    if ops not in _MENTORS:
        from flux_knowledge import Corpus, Library, Mentor, RecordReadback

        from .knowledge import knowledge_text

        _MENTORS[ops] = Mentor([
            Corpus("knowledge: methods sheet", knowledge_text()),
            Library(library_queries(ops), max_chars=7000, clip=520),
            RecordReadback(
                stage="screen", metric="fmax_mhz", knobs=("style", "method", "latency"),
                metric_label="MHz at the synthesis screen", top=5,
                conclusion=lambda c: (
                    f"an earlier run decided: {c['decision']} ({c.get('decided_by', '')})"
                    if c.get("decision") else None),
                extra=_attempts_so_far,
                framing="this campaign's earlier runs; directions, not instructions -- a "
                        "refused attempt measures that attempt, not its approach; the best "
                        "one is the one to refine"),
        ])
    return _MENTORS[ops]


#: One `Mentor` per operator set, for this process: what `mentor_for` memoises.
_MENTORS: dict[tuple[str, ...], Any] = {}


def library_queries(ops: tuple[str, ...]) -> list[str]:
    """What to ask the operator's own papers (D407's library, read by D408's designer): one
    lookup per operator plus the three method families this study keeps reaching for. The
    `Library` source (D449) runs them once per run, for both of this study's paths."""
    return [
        # the implementation shapes sampled designs get wrong most (D473/D477), FIRST so
        # the block's budget reaches them: proven sources in the library answer these --
        # fpnew's classifier and rounding, the leading-zero normalisation, FloPoCo's IEEE
        # input handling
        "classify subnormal zero infinity nan exponent all ones",
        "leading zero count normalize subnormal mantissa",
        "round to nearest even sticky guard carry into exponent",
        "exponential range reduction table fixed point",
    ] + [f"{op} hardware approximation half precision FP16" for op in ops] + [
        "piecewise polynomial approximation unit ULP",
        "CORDIC transcendental function hardware",
        "lookup table interpolation activation function circuit"]


def _reload_field(s: _Study) -> int:
    """Previous runs' designs re-enter the study: sources come back from the record,
    and every stage re-judges them (cached where the tools and source are unchanged)."""
    if s.records is None or not s.records.resumed:
        return 0
    known = s.records.known(stage="screen", metric="fmax_mhz")
    n = 0
    for cand, _v in known:
        if cand.get("source"):
            full = {"name": str(cand.get("name", "reloaded")), "style": cand["style"],
                    "latency": int(cand["latency"]), "method": cand.get("method", "?"),
                    "methods": dict(cand.get("methods") or {}),
                    "source": cand["source"]}
            with _phase("field: re-judge a recorded design", why=full["name"]):
                n += bool(_admit(s, full, provenance="from the record"))
    if n:
        s.lessons.append(f"{n} design(s) from earlier runs re-entered and re-measured")
    return n


# ---------------------------------------------------------------- model rounds
def _author_tests(s: _Study) -> None:
    """The loop writes its own unit tests: model-proposed adversarial vectors merged
    over the floor, persisted as campaign events so the suite accumulates."""
    base = floor_vectors(s.request.seed)
    for op in s.request.ops:
        s.vectors[op] = base
    if s.records is not None:                       # reload earlier authored suites
        try:
            for e in s.records.store.events(s.records.campaign_id):
                if e.get("kind") == "authored_vectors":
                    for op, vals in (e.get("detail") or {}).get("vectors", {}).items():
                        if op in s.vectors:
                            extra = np.array([int(v, 16) for v in vals], dtype=np.uint16)
                            s.vectors[op] = merge(s.vectors[op], extra)
        except Exception:  # noqa: BLE001
            pass
    if s.proposer is None or s.request.test_rounds <= 0:
        return
    for round_ in range(s.request.test_rounds):
        human = s.drain()
        with _phase("author: adversarial vectors", why=f"round {round_ + 1}"):
            try:
                reply = s.proposer.propose(
                    test_author_prompt(ops=s.request.ops, human=human))
            except Exception as exc:  # noqa: BLE001
                s.not_established.append(f"test-author round did not run ({exc})")
                return
        authored, bad = parse_authored(reply)
        for b in bad:
            s.refused.append(("test-author", b))
        added = 0
        for op, vec in authored.items():
            if op in s.vectors:
                before = s.vectors[op].size
                s.vectors[op] = merge(s.vectors[op], vec)
                added += s.vectors[op].size - before
        if authored and s.records is not None:
            try:
                s.records.store.append_event(
                    s.records.campaign_id, "authored_vectors",
                    {"vectors": {op: [f"0x{int(v):04x}" for v in vec]
                                 for op, vec in authored.items()}})
            except Exception:  # noqa: BLE001
                pass
        s.say(f"test author: {added} new adversarial vector(s) joined the suite")
        s.lessons.append(f"[author] the model added {added} adversarial vector(s) "
                         "to the unit-test suite (kept in the record)")


def _standings(s: _Study) -> str:
    if not s.scored:
        return ""
    rows = "\n".join(
        f"  * {x.name}: {x.candidate['style']}, {x.candidate['method']}, latency "
        f"{x.candidate['latency']} -> {x.area_um2:.0f} um2, {x.fmax_mhz:.0f} MHz, "
        f"max {x.max_ulp} ULP" for x in s.scored[-8:])
    return "MEASURED SO FAR in this run (beat these, or land elsewhere on the frontier):\n" + rows


def _design_rounds(s: _Study) -> None:
    if s.proposer is None or s.request.llm_rounds <= 0:
        return
    # The same declared sources the loop's path uses (D449), so both of this study's front
    # doors carry the same knowledge and the library is read once for the run either way.
    mentor = mentor_for(s.request.ops)
    ctx = mentor.text("record", s)
    with _phase("mentor: read the library", why="papers -> designer prompt"):
        papers = mentor.text("library", s)
    if papers:
        s.lessons.append("[mentor] the designer prompt carries excerpts from "
                         "the local paper library (D407)")
    know = (papers + "\n\n" + knowledge_text()) if papers else knowledge_text()
    authored_note = ("The unit-test suite includes model-authored adversarial "
                     "vectors; the CORRECTNESS gate is exhaustive regardless.")
    for k in range(s.request.llm_rounds):
        human = s.drain()
        closest = getattr(s, "closest", None)
        # Rework the best compiling design by default; start FRESH only when nothing
        # has compiled yet, or on the explore cadence -- a good direction with flaws
        # is worth more than a blank page, but a stuck lineage needs an escape
        # (D411, Cedric's rule). explore_every=4 => 1 fresh in every 4 rounds.
        every = max(1, s.request.explore_every)
        rework = closest is not None and (k % every != every - 1)
        if rework:
            mode = "rework"
            base = improve_prompt(closest["cand"], closest["why"],
                                  ulp_budget=s.request.ulp_budget)
            prompt = ((human + "\n\n" if human else "") + base
                      + "\n\n" + know)
        else:
            mode = "fresh"
            closest_block = "" if closest is None else (
                "A PREVIOUS ATTEMPT (compiled, judged, refused: " + closest["why"]
                + ") -- borrow what worked, but you may try a different approach:\n"
                "```verilog\n" + closest["cand"]["source"][:6000] + "\n```")
            prompt = design_prompt(
                ops=s.request.ops, ulp_budget=s.request.ulp_budget, knowledge=know,
                record_ctx=ctx, human=human,
                standings=(_standings(s) + ("\n\n" + closest_block if closest_block
                                            else "")),
                refusals=[f"{n}: {w[:140]}" for n, w in s.refused],
                authored_note=authored_note)
        with _phase(f"design: {mode} an NLU", why=f"round {k + 1}"):
            try:
                reply = s.proposer.propose(prompt)
            except Exception as exc:  # noqa: BLE001
                s.not_established.append(f"design round {k + 1} did not run ({exc})")
                return
        cand, why = parse_design(reply, ops=s.request.ops)
        if cand is None:                                     # the FIRST proposal
            s.refused.append((f"round {k + 1}", why or "unparseable"))
            if s.records is not None:
                s.records.trial({"style": "?", "method": "?", "latency": -1,
                                 "name": f"round {k + 1}"},
                                f"round{k + 1}:unparseable", stage="gate",
                                strategy="llm", metrics=None,
                                error=(why or "unparseable")[:400])
            continue
        # Stay on THIS design until it is admitted or the repair budget is spent
        # (D411 follow-up, Cedric's rule: do not abandon a broken design for a fresh
        # one). A malformed repair reply -- a lost header or fence -- is a wasted
        # round, NOT a reason to discard the design: keep `working` and re-issue the
        # repair against it. Only exhausting `repair_attempts` moves on.
        working = cand
        for attempt in range(s.request.repair_attempts + 1):
            before = len(s.refused)
            if _admit(s, working, provenance=f"round {k + 1}"):
                break
            if attempt == s.request.repair_attempts:
                break
            failure = "; ".join(w for _n, w in s.refused[before:]) or "refused"
            with _phase("design: repair", why=working["name"],
                        attempt=attempt + 1):
                try:
                    reply = s.proposer.propose(repair_prompt(working, failure))
                except Exception as exc:  # noqa: BLE001
                    s.not_established.append(f"repair did not run ({exc})")
                    return
            fixed, why = parse_design(reply, ops=s.request.ops)
            if fixed is not None:
                working = fixed                              # the repair advanced it
            else:
                s.say(f"  repair reply lost its header/fence; retrying {working['name']} "
                      f"(attempt {attempt + 1}/{s.request.repair_attempts})")


# ---------------------------------------------------------------- the study
def run_study(request: NluRequest, *, proposer: Any | None = None,
              feedback: Any | None = None,
              log: Callable[[str], None] | None = None) -> NluResult:
    say = log or (lambda m: print(m, flush=True))
    s = _Study(request=request, say=say, proposer=proposer, feedback=feedback,
               started=time.monotonic())
    missing = tools_missing() + [t for t in ("yosys",) if shutil.which(t) is None]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} not on PATH; run from the dev shell: "
            "nix develop --command python3 applications/nlu/demo.py")
    bad_ops = [o for o in request.ops if o not in OPCODES]
    if bad_ops:
        raise ValueError(f"unknown operator(s): {', '.join(bad_ops)} "
                         f"(known: {', '.join(OPCODES)})")
    can_place = shutil.which("openroad") is not None
    from .floor import floor_ops

    floored = request.floor and any(op in floor_ops() for op in request.ops)
    if request.agentic and (proposer is not None or floored):
        # D421: the agentic study IS the shared loop -- NluProblem supplies what an
        # operator module is, how to ask for one, the fast vectors, the exhaustive
        # gate, the oracle, the composed top and the stages; flux_loop does the rest,
        # including opening the record (so it is not opened twice here). Since D475 it
        # runs WITHOUT a model when the floor generator covers an operator: the family
        # search drafts, the gate judges, the tools measure.
        say(f"problem: FP16 NLU [{', '.join(request.ops)}], gate <= "
            f"{request.ulp_budget} ULP on all 65536 inputs per op; tools constrained "
            f"to {request.clock_period_ps:.0f} ps on ASAP7")
        return _run_as_loop(request, proposer=proposer, feedback=feedback, say=say,
                            can_place=can_place)
    s.workdir = tempfile.mkdtemp(prefix="flux-nlu-")

    if request.db:
        try:
            from flux_cache import MeasurementCache
            from flux_evaluator_abi import toolchain_fingerprint

            s.cache = MeasurementCache(request.db, toolchain_fingerprint(),
                                       suffix="nlu.json")
        except Exception:  # noqa: BLE001 -- a cache is an optimisation, never a gate
            s.cache = None
        from flux_records import Records

        s.records = Records(request.db, objective={
            "study": "nlu", "ops": list(request.ops),
            "ulp_budget": request.ulp_budget,
            "clock_period_ps": request.clock_period_ps}, log=say)
        from flux_feedback import reload_notes

        s.human_notes.extend(reload_notes(s.records, say=say))

    say(f"problem: FP16 NLU [{', '.join(request.ops)}], gate <= "
        f"{request.ulp_budget} ULP on all 65536 inputs per op; tools constrained "
        f"to {request.clock_period_ps:.0f} ps on ASAP7")

    with _phase("setup: unit-test suite", why="floor + record + author"):
        _author_tests(s)
    _reload_field(s)
    _design_rounds(s)
    s.drain()      # a note typed during the last round is still recorded

    if not s.scored:
        s.not_established.append(
            "nothing reached the frontier: "
            + ("no model was attached and the record held no designs -- attach a "
               "model (--llm-round) or resume a campaign that has some"
               if proposer is None else
               "no operator was proven yet -- every attempt is refused with its "
               "reason; the campaign record keeps them for the next run"))
        return _result(s, None, "nothing measured", [], [])

    from flux_frontier import frontier as _frontier
    from flux_frontier import spread as _spread

    front = _frontier(s.scored, better=lambda p: p.fmax_mhz,
                      cost=lambda p: p.area_um2)
    confirmed: list[Scored] = []
    if not request.screen_only and can_place:
        finalists = _spread(front, request.decide_on_finalists,
                            cost=lambda p: p.area_um2)
        say(f"confirm: placing {len(finalists)} finalist(s) for PPA")
        for f in finalists:
            got = _measure(s, f.candidate, f.per_op, "confirm")
            if got is not None:
                confirmed.append(got)
        if confirmed and s.records is not None:
            for c in confirmed:
                s.records.trial(
                    {**_knobs(c.candidate), "name": c.name,
                     "source": c.candidate["source"]},
                    f"{c.name}@confirm", stage="confirm", strategy="llm",
                    metrics={"fmax_mhz": c.fmax_mhz, "area_um2": c.area_um2,
                             "power_w": c.power_w}, analytic=False,
                    evaluator="openroad@place")
    elif not request.screen_only and not can_place:
        s.not_established.append("openroad is not on PATH: PPA (power, placed fmax) "
                                 "was not measured; the numbers below are the "
                                 "synthesis screen's")

    pool = confirmed or front
    from flux_frontier import cheapest_meeting, knee_ranked

    if request.target_mhz is not None:
        pick, rule = cheapest_meeting(pool, cost=lambda p: p.area_um2,
                                      value=lambda p: p.fmax_mhz,
                                      floor=request.target_mhz)
        decided_by = {"cheapest-meeting":
                      f"smallest area at >= {request.target_mhz:.0f} MHz",
                      "fallback-best-value":
                      f"nothing reaches {request.target_mhz:.0f} MHz; the fastest",
                      "best-value": "the fastest measured",
                      "nothing": "nothing measured"}[rule]
    else:
        ranked = knee_ranked(pool, [lambda p: p.area_um2,
                                    lambda p: -p.fmax_mhz,
                                    lambda p: p.power_w])
        pick = ranked[0] if ranked else None
        decided_by = "the knee of area / fmax / power"
    if pick is not None:
        stage = "confirmed PPA" if confirmed else "synthesis screen"
        s.lessons.append(
            f"[{stage}] decision {pick.name}: {pick.area_um2:.0f} um2, "
            f"{pick.fmax_mhz:.0f} MHz, {pick.power_w * 1e3:.1f} mW, worst op error "
            f"rate {pick.error_rate:.2%}, max {pick.max_ulp} ULP ({decided_by})")
    if s.records is not None and pick is not None:
        s.records.conclude({"decision": pick.name, "decided_by": decided_by,
                            "fmax_mhz": round(pick.fmax_mhz, 1),
                            "area_um2": round(pick.area_um2, 1),
                            "power_w": pick.power_w,
                            "max_ulp": pick.max_ulp})
    return _result(s, pick, decided_by, front, confirmed)


def _run_as_loop(request: NluRequest, *, proposer: Any, feedback: Any,
                 say: Callable[[str], None], can_place: bool) -> NluResult:
    from flux_loop import LoopRequest, run_loop

    from .problem import NluProblem

    problem = NluProblem(ops=request.ops, ulp_budget=request.ulp_budget,
                         clock_period_ps=request.clock_period_ps,
                         target_mhz=request.target_mhz, test_rounds=request.test_rounds,
                         seed=request.seed)
    loop_req = LoopRequest(
        db=request.db, steps=request.op_steps, repair_attempts=request.repair_attempts,
        explore_every=request.explore_every, cooldown_after=request.cooldown_after,
        structured=request.structured, patching=request.patching,
        prototype=request.prototype, regenerate=tuple(request.regenerate),
        screen_only=request.screen_only or not can_place,
        finalists=request.decide_on_finalists,
        params={"evaluator": "nlu@exhaustive-ulp", "seed": request.seed,
                "floor": request.floor})
    started = time.monotonic()
    out = run_loop(problem, loop_req, proposer=proposer, feedback=feedback, log=say)

    def to_scored(sc: Any) -> Scored:
        c = sc.candidate
        return Scored(
            candidate={"name": c.name, "style": c.knobs.get("style", "shared"),
                       "latency": int(c.meta.get("latency", 0)),
                       "method": c.knobs.get("method", "?"),
                       "methods": dict(c.meta.get("methods") or {}), "source": c.artifact},
            per_op=problem.per_op.get(c.name, {}),
            area_um2=sc.metrics.get("area_um2", 0.0),
            worst_slack_ps=sc.metrics.get("worst_slack_ps", 0.0),
            clock_period_ps=sc.metrics.get("clock_period_ps", request.clock_period_ps),
            power_w=sc.metrics.get("power_w", 0.0),
            flow_depth="placement" if sc.metrics.get("placed") else "synthesis")

    not_established = list(out.not_established)
    if not request.screen_only and not can_place:
        not_established.append("openroad is not on PATH: PPA (power, placed fmax) was "
                               "not measured; the numbers below are the synthesis "
                               "screen's")
    return NluResult(
        decision=(to_scored(out.decision) if out.decision else None),
        decided_by=out.decided_by,
        frontier=[to_scored(x) for x in out.frontier],
        confirmed=[to_scored(x) for x in out.confirmed],
        scored=[to_scored(x) for x in out.scored],
        refused=list(out.refused), lessons=list(out.lessons),
        not_established=not_established, notes=list(out.notes),
        provenance={"ops": list(request.ops), "ulp_budget": request.ulp_budget,
                    "screen_vectors": {op: int(v.size) for op, v in problem.vectors.items()},
                    "exhaustive_inputs": 65536,
                    "clock_period_ps": request.clock_period_ps,
                    "admitted": sorted(out.admitted),
                    "loop": out.provenance,
                    "wall_clock_s": round(time.monotonic() - started, 1)})


def _result(s: _Study, pick: Scored | None, decided_by: str,
            front: list[Scored], confirmed: list[Scored]) -> NluResult:
    return NluResult(
        decision=pick, decided_by=decided_by, frontier=front, confirmed=confirmed,
        scored=s.scored, refused=s.refused, lessons=s.lessons,
        not_established=s.not_established,
        notes=[n.text for n in s.human_notes],
        provenance={
            "ops": list(s.request.ops), "ulp_budget": s.request.ulp_budget,
            "screen_vectors": {op: int(v.size) for op, v in s.vectors.items()},
            "exhaustive_inputs": 65536,
            "clock_period_ps": s.request.clock_period_ps,
            "wall_clock_s": round(time.monotonic() - s.started, 1),
        })
