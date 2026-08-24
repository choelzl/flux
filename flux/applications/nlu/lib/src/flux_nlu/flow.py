"""The NLU study (D408): the model designs a non-linear unit, the tools judge it.

    setup       verilator + yosys (openroad for the confirm rung), the campaign
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
    decide      the shared arithmetic (flux_decide): the target-and-floor rule when
                a clock is demanded, the knee otherwise
    report      decision first, per-operator error table, refusals with reasons,
                what is not established

Claude built this rig; the MODEL running in it picks methods (LUT, piecewise
polynomial, Newton-Raphson, CORDIC, ...), sharing, and pipelining -- see invent.py
for the bargain, knowledge.py for the seed it is taught from.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .fp16 import OPCODES, all_inputs, ulp_report
from .invent import design_prompt, parse_design, repair_prompt, test_author_prompt
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
    repair_attempts: int = 2
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
    started: float = 0.0

    def drain(self) -> str | None:
        from flux_feedback import drain_guidance

        def on_note(n: Any) -> None:
            if self.records is not None:
                self.records.note(n.text)
            has_model = self.proposer is not None
            self.lessons.append(
                f"[human] operator guidance: {n.text!r}"
                + (" -- it goes into the next proposal prompt" if has_model
                   else " -- no model role in this run; recorded and reported"))

        return drain_guidance(self.feedback, self.human_notes, on_note=on_note)


def _phase(name: str, why: str = "", **params: Any):
    from flux_profile import phase

    return phase(name, why=why, **params)


# ---------------------------------------------------------------- the rungs
def _wrap_per_op(source: str, ops: tuple[str, ...]) -> str:
    """The framework's op mux around per-op modules, so BOTH styles present the one
    `nlu` top to simulation and synthesis. The mux is real hardware an NLU serving
    several operators pays either way; charging it to per-op designs keeps the
    area comparison honest."""
    insts = "\n".join(
        f"  wire [15:0] y_{op}; nlu_{op} u_{op}(.clk(clk), .x(x), .y(y_{op}));"
        for op in ops)
    cases = "\n".join(f"      3'd{OPCODES[op]}: y = y_{op};" for op in ops)
    return (source + "\n\nmodule nlu(input wire clk, input wire [15:0] x,\n"
            "           input wire [2:0] op, output reg [15:0] y);\n"
            + insts + "\n  always @* begin\n    case (op)\n" + cases +
            "\n      default: y = 16'h7e00;\n    endcase\n  end\nendmodule\n")


def _correctness(s: _Study, cand: dict[str, Any]) -> tuple[dict[str, dict], str | None]:
    """The proof rung: exhaustive ULP per operator. Returns (per-op reports, None) or
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
            worst = "; ".join(
                f"x={w['x']} got={w['got']} want={w['want']} ulp={w['ulp']}"
                for w in rep["worst"][:4])
            return per_op, (
                f"{op}: {rep['over_budget']} of {rep['n']} inputs beyond "
                f"{s.request.ulp_budget} ULP (max {rep['max_ulp']}) -- worst: {worst}")
    return per_op, None


def _measure(s: _Study, cand: dict[str, Any], per_op: dict[str, dict],
             rung: str) -> Scored | None:
    """Screen (synthesis) or confirm (placement) through the shared openroad flow."""
    from flux_evaluator_openroad import run_ppa_flow, run_synthesis_flow

    top_source = (cand["source"] if cand["style"] == "shared"
                  else _wrap_per_op(cand["source"], s.request.ops))
    kw = dict(clock_port="clk" if cand["latency"] > 0 else None,
              clock_period_ps=s.request.clock_period_ps)
    ident = (f"nlu/{rung}/{s.request.clock_period_ps:.0f}ps/"
             f"{hashlib.sha256(top_source.encode()).hexdigest()[:16]}")

    def _run_tools() -> dict[str, Any]:
        try:
            if rung == "screen":
                rep = run_synthesis_flow(top_source, "nlu", **kw)
            else:
                rep = run_ppa_flow(top_source, "nlu", flow_depth="placement",
                                   repair_design=True, **kw)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        return {"area_um2": rep.area_um2, "worst_slack_ps": rep.worst_slack_ps,
                "clock_period_ps": rep.clock_period_ps,
                "power_w": rep.power_total_w or 0.0, "flow_depth": rep.flow_depth}

    label = f"{rung}: {'place' if rung == 'confirm' else 'synth'} {cand['name']}"
    with _phase(label, why=f"latency {cand['latency']}, {cand['style']}"):
        got = (s.cache.get_or_measure(ident, _run_tools)
               if s.cache is not None else _run_tools())
    if "error" in got:
        s.refused.append((cand["name"], f"{rung} failed: {got['error']}"))
        return None
    return Scored(candidate=cand, per_op=per_op, area_um2=float(got["area_um2"]),
                  worst_slack_ps=float(got["worst_slack_ps"]),
                  clock_period_ps=float(got["clock_period_ps"]),
                  power_w=float(got["power_w"]),
                  flow_depth=str(got["flow_depth"]))


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
        s.records.trial(payload, cand["name"], rung="gate", strategy="llm",
                        metrics=None, error=(error or "refused")[:400],
                        analytic=False, evaluator="nlu@exhaustive-ulp")
    else:
        s.records.trial(payload, cand["name"], rung="screen", strategy="llm",
                        metrics={"fmax_mhz": scored.fmax_mhz,
                                 "area_um2": scored.area_um2,
                                 "error_rate": scored.error_rate},
                        analytic=True, evaluator="yosys+opensta@screen")


def _record_context(records, ops: tuple[str, ...]) -> str:
    """The flywheel's read-back: earlier conclusions, plus head-to-head verdicts over
    the design knobs (style, method, latency) on measured fmax."""
    if records is None or not getattr(records, "resumed", False):
        return ""
    lines: list[str] = []
    for c in records.conclusions(limit=2):
        d = c.get("decision")
        if d:
            lines.append(f"an earlier run decided: {d} ({c.get('decided_by', '')})")
    known = records.known(rung="screen", metric="fmax_mhz")
    from flux_extract import head_to_head

    duels = head_to_head(
        [({k: c.get(k) for k in ("style", "method", "latency")}, v)
         for c, v in known], metric="MHz at the synthesis screen", top=5)
    lines += [d.describe() for d in duels]
    for c, why in records.refusals(rung="gate", limit=4):
        lines.append(f"refused earlier: {c.get('name', '?')} -- {why[:120]}")
    if not lines:
        return ""
    return ("WHAT THE RECORD SHOWS (this campaign's earlier runs; directions, not "
            "instructions):\n" + "\n".join(f"  * {ln}" for ln in lines))


def _library_context(ops: tuple[str, ...], *, k_per_query: int = 2,
                     max_chars: int = 2600) -> str:
    """What the operator's own papers say (D407's library, read by D408's designer):
    a few targeted lookups against the local BM25 index, deduplicated, source-cited.
    No library or no index yields "" -- the loop runs the same without papers."""
    try:
        from flux_knowledge.retrieval import knowledge_lookup
    except Exception:  # noqa: BLE001
        return ""
    queries = [f"{op} hardware approximation half precision FP16" for op in ops]
    queries += ["piecewise polynomial approximation unit ULP",
                "CORDIC transcendental function hardware",
                "lookup table interpolation activation function circuit"]
    seen: set[str] = set()
    lines: list[str] = []
    used = 0
    try:
        for q in queries:
            for hit in knowledge_lookup(q, standard_id="library", k=k_per_query):
                c = hit.chunk
                if c.id in seen:
                    continue
                seen.add(c.id)
                src = c.source_path.rsplit("/", 1)[-1]
                text = c.text if len(c.text) <= 320 else c.text[:320] + "..."
                line = f"  * [{src}] {text}"
                if used + len(line) > max_chars:
                    return _library_header(lines)
                lines.append(line)
                used += len(line)
    except Exception:  # noqa: BLE001
        return ""
    return _library_header(lines)


def _library_header(lines: list[str]) -> str:
    if not lines:
        return ""
    return ("FROM THE OPERATOR'S LIBRARY (papers on this machine, retrieved "
            "lexically -- excerpts, cite-worthy for direction only):\n"
            + "\n".join(lines))


def _reload_field(s: _Study) -> int:
    """Previous runs' designs re-enter the study: sources come back from the record,
    and every rung re-judges them (cached where the tools and source are unchanged)."""
    if s.records is None or not s.records.resumed:
        return 0
    known = s.records.known(rung="screen", metric="fmax_mhz")
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
    ctx = _record_context(s.records, s.request.ops)
    with _phase("mentor: read the library", why="papers -> designer prompt"):
        papers = _library_context(s.request.ops)
    if papers:
        s.lessons.append("[mentor] the designer prompt carries excerpts from "
                         "the local paper library (D407)")
    know = (papers + "\n\n" + knowledge_text()) if papers else knowledge_text()
    authored_note = ("The unit-test suite includes model-authored adversarial "
                     "vectors; the CORRECTNESS gate is exhaustive regardless.")
    for k in range(s.request.llm_rounds):
        human = s.drain()
        prompt = design_prompt(
            ops=s.request.ops, ulp_budget=s.request.ulp_budget, knowledge=know,
            record_ctx=ctx, human=human, standings=_standings(s),
            refusals=[f"{n}: {w[:140]}" for n, w in s.refused],
            authored_note=authored_note)
        with _phase("design: model proposes an NLU", why=f"round {k + 1}"):
            try:
                reply = s.proposer.propose(prompt)
            except Exception as exc:  # noqa: BLE001
                s.not_established.append(f"design round {k + 1} did not run ({exc})")
                return
        cand, why = parse_design(reply, ops=s.request.ops)
        for attempt in range(s.request.repair_attempts + 1):
            if cand is None:
                s.refused.append((f"round {k + 1}", why or "unparseable"))
                break
            before = len(s.refused)
            if _admit(s, cand, provenance=f"round {k + 1}"):
                break
            if attempt == s.request.repair_attempts:
                break
            failure = "; ".join(w for _n, w in s.refused[before:]) or "refused"
            with _phase("design: repair", why=cand["name"]):
                try:
                    reply = s.proposer.propose(repair_prompt(cand, failure))
                except Exception as exc:  # noqa: BLE001
                    s.not_established.append(f"repair did not run ({exc})")
                    return
            cand, why = parse_design(reply, ops=s.request.ops)


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
            "nothing survived to the frontier: "
            + ("no model was attached and the record held no designs -- attach a "
               "model (--llm-round) or resume a campaign that has some"
               if proposer is None else
               "every proposal was refused; the reasons are in the refusal list"))
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
                    f"{c.name}@confirm", rung="confirm", strategy="llm",
                    metrics={"fmax_mhz": c.fmax_mhz, "area_um2": c.area_um2,
                             "power_w": c.power_w}, analytic=False,
                    evaluator="openroad@place")
    elif not request.screen_only and not can_place:
        s.not_established.append("openroad is not on PATH: PPA (power, placed fmax) "
                                 "was not measured; the numbers below are the "
                                 "synthesis screen's")

    pool = confirmed or front
    from flux_decide import cheapest_meeting, knee_ranked

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
        rung = "confirmed PPA" if confirmed else "synthesis screen"
        s.lessons.append(
            f"[{rung}] decision {pick.name}: {pick.area_um2:.0f} um2, "
            f"{pick.fmax_mhz:.0f} MHz, {pick.power_w * 1e3:.1f} mW, worst op error "
            f"rate {pick.error_rate:.2%}, max {pick.max_ulp} ULP ({decided_by})")
    if s.records is not None and pick is not None:
        s.records.conclude({"decision": pick.name, "decided_by": decided_by,
                            "fmax_mhz": round(pick.fmax_mhz, 1),
                            "area_um2": round(pick.area_um2, 1),
                            "power_w": pick.power_w,
                            "max_ulp": pick.max_ulp})
    return _result(s, pick, decided_by, front, confirmed)


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
