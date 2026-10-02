"""The MAC-PE microarchitecture study (D365).

Generation is checked without tools: every point of the space produces a module with the port
contract and structure its name claims. With Verilator on PATH the designs also run against their
golden vectors, latency included. The objective's rules and the invention parser are pinned too.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_macarray import (  # noqa: E402
    DEFAULT, MULTIPLIERS, PIPELINES, REDUCERS, PeConfig, Score, Scored, Shape, decide, frontier,
    generate, golden_vectors, spread,
)
from flux_macarray.invent import parse_module, refusal_reason  # noqa: E402

SHAPE = Shape(lanes=8, in_bits=8, w_bits=8, accumulate=True)


# ---- the space -----------------------------------------------------------------------------

def test_the_space_is_exhaustive():
    """The world's `space` hook: the document's multipliers times the generator's reducers and depths (D553)."""
    from itertools import product

    from flux_loop import LoopRequest, LoopState

    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    sp = macarray_problem(include_invented=False).space(state)
    assert sp["multiplier"] == list(MULTIPLIERS) and sp["reducer"] == list(REDUCERS)
    assert sp["pipeline"] == list(PIPELINES)
    pts = [PeConfig(m, r, p) for m, r, p in product(*sp.values())]
    assert len(pts) == len(set(pts)) == 48 and DEFAULT in pts
    narrow = macarray_problem(include_invented=False, pipelines=[0]).space(state)
    assert len(list(product(*narrow.values()))) == 12




def test_the_structures_are_what_their_names_say():
    assert "a0 * w0" not in generate(PeConfig("array", "tree", 0), SHAPE).source
    assert "pp0_7" in generate(PeConfig("array", "tree", 0), SHAPE).source
    booth = generate(PeConfig("booth4", "tree", 0), SHAPE).source
    assert "case (wb0[2:0])" in booth and "bd0_3" in booth and "bd0_4" not in booth
    wallace = generate(PeConfig("wallace", "tree", 0), SHAPE).source
    assert "m0s0_0" in wallace and "mag0" in wallace
    csa = generate(PeConfig("behavioral", "csa", 0), SHAPE).source
    assert "rs0_0" in csa and "csum" in csa
    chain = generate(PeConfig("behavioral", "chain", 0), SHAPE).source
    assert "ch8" in chain and "t0_0" not in chain


def test_pipeline_depth_is_the_number_of_register_stages():
    p1 = generate(PeConfig("behavioral", "tree", 1), SHAPE).source
    assert "p0_r <= p0" in p1 and "acc_r" not in p1 and "assign done = busy;" in p1
    p2 = generate(PeConfig("behavioral", "tree", 2), SHAPE).source
    assert "acc_r <= " in p2 and "busy[1]" in p2
    p3 = generate(PeConfig("behavioral", "tree", 3), SHAPE).source
    assert "t1_0_r <= t1_0" in p3 and "busy[2]" in p3


def test_an_invented_multiplier_is_instantiated_once_per_lane():
    src = generate(PeConfig("mul1", "tree", 0), SHAPE,
                   invented={"mul1": "module mul1(input logic signed [7:0] a, input logic "
                                     "signed [7:0] w, output logic signed [15:0] p); "
                                     "assign p = a * w; endmodule\n"})
    assert src.source.count("mul1 u_mul") == 8
    assert "mul1" in src.extra_sources and "module mul1" in src.all_sources
    with pytest.raises(ValueError):
        generate(PeConfig("mul9", "tree", 0), SHAPE)


def test_golden_vectors_cover_the_corners_and_never_overflow():
    vecs = golden_vectors(SHAPE, seed="t")
    assert len(vecs) == 6
    corners = [tuple(v["inputs"][f"a{i}"] for i in range(8)) for v in vecs[:4]]
    assert corners[0] == (-128,) * 8 and corners[3] == (127,) * 8
    lo, hi = -(1 << 19), (1 << 19) - 1
    for v in vecs:
        assert lo <= v["expected"]["acc"] <= hi


# ---- the objective -------------------------------------------------------------------------

def _pt(label: str, area: float, path_ps: float, period: float = 1000.0) -> Scored:
    m, r, p = label.split("-")
    return Scored(config=PeConfig(m, r, int(p[1:])), provenance="t",
                  score=Score(area_um2=area, worst_slack_ps=period - path_ps,
                              clock_period_ps=period, power_w=0.01, cell_count=100,
                              latency_cycles=int(p[1:]), flow_depth="synthesis"))


def test_fmax_is_the_measured_path_not_the_constraint():
    s = _pt("behavioral-tree-p0", 1000, 1250).score
    assert s.path_ps == 1250 and s.fmax_mhz == pytest.approx(800.0)
    assert not s.meets(1000) and s.meets(800)


def test_the_decision_is_the_smallest_pe_that_makes_the_target():
    pts = [_pt("behavioral-tree-p0", 1000, 1250), _pt("booth4-csa-p1", 1300, 900),
           _pt("wallace-csa-p2", 1600, 600), _pt("array-chain-p0", 900, 2000)]
    pick, how = decide(pts, 1000.0)
    assert pick.label == "booth4-csa-p1" and "smallest" in how
    pick, how = decide(pts, 2000.0)
    assert pick.label == "wallace-csa-p2" and "nothing reaches" in how
    pick, how = decide(pts, None)
    assert pick.label == "wallace-csa-p2"


def test_the_frontier_is_fmax_against_area():
    pts = [_pt("behavioral-tree-p0", 1000, 1250), _pt("booth4-csa-p1", 1300, 900),
           _pt("wallace-csa-p2", 1600, 600), _pt("array-chain-p0", 900, 2000),
           _pt("array-tree-p0", 1100, 1300)]           # dominated: bigger and slower
    front = frontier(pts)
    assert [p.label for p in front] == ["array-chain-p0", "behavioral-tree-p0",
                                         "booth4-csa-p1", "wallace-csa-p2"]
    picked = spread(front, 2)
    assert [p.label for p in picked] == ["array-chain-p0", "wallace-csa-p2"]


def macarray_problem(**params):
    """The MAC-PE problem from `applications/macarray/problem.yaml`, built as `flux task run` builds it (D533)."""
    import yaml
    from flux_loop import PromptProblem, TaskSpec

    doc_path = FLUX_ROOT / "applications" / "macarray" / "problem.yaml"
    doc = yaml.safe_load(doc_path.read_text())
    doc["params"] = {**doc["params"], **params}
    if params.get("target_mhz"):
        doc["objectives"][0]["goal"] = float(params["target_mhz"])
    return PromptProblem(TaskSpec.from_dict(doc, base=doc_path.parent))


def test_preserving_fmax_makes_the_incumbents_own_clock_the_target():
    inc = _pt("behavioral-tree-p0", 421, 1167)          # 857 MHz
    small = _pt("behavioral-csa-p0", 390, 1072)          # 933 MHz, 7% smaller
    fast = _pt("booth4-csa-p1", 431, 778)
    s = macarray_problem(preserve_fmax=True, target_mhz=1000.0)
    assert s.target([inc, small, fast]) == pytest.approx(inc.fmax_mhz)
    pick, how = decide([inc, small, fast], s.target([inc, small, fast]))
    assert pick.label == "behavioral-csa-p0", "the smallest that holds the incumbent's clock"
    # a measured floor is held to within 1%, so a much smaller design at a near-equal clock wins
    hair = _pt("behavioral-csa-p0", 781, 1000 * 1000 / 805.1)
    inc16 = _pt("behavioral-tree-p0", 831, 1000 * 1000 / 805.4)
    assert decide([inc16, hair], inc16.fmax_mhz)[0].label == "behavioral-tree-p0"
    assert decide([inc16, hair], inc16.fmax_mhz, tolerance=s.tolerance)[0].label == "behavioral-csa-p0"
    assert s.tolerance == 0.01 and plain_tolerance() == 0.0
    assert [p.label for p in frontier([inc16, hair])] == ["behavioral-csa-p0"], "same clock: one point"
    plain = macarray_problem(target_mhz=1000.0)
    assert plain.target([inc, small, fast]) == 1000.0


def plain_tolerance() -> float:
    return macarray_problem(target_mhz=1000.0).tolerance


def test_the_shape_derives_the_accumulator_width_never_chooses_it():
    assert SHAPE.product_bits == 16
    assert SHAPE.acc_bits == 16 + 3 + 1, "8 products need 3 bits; the accumulator input one more"
    assert Shape(lanes=8, in_bits=8, w_bits=8, accumulate=False).acc_bits == 19


# ---- invention -----------------------------------------------------------------------------

def test_the_model_reply_is_parsed_and_the_rules_are_enforced():
    reply = ("IDEA: radix-4 Booth\n```verilog\nmodule mul1(input logic signed [7:0] a, "
             "input logic signed [7:0] w, output logic signed [15:0] p);\n  assign p = a * w;\n"
             "endmodule\n```")
    src, idea = parse_module("mul1", reply)
    assert idea == "radix-4 Booth" and src.startswith("module mul1")
    assert refusal_reason(src) and "behavioral" in refusal_reason(src)
    assert parse_module("mul2", reply) is None
    assert "sequential" in refusal_reason("module mul1(); always @(posedge clk) x <= 1; endmodule")
    assert "casts" in refusal_reason("module mul1(); assign p = 16'(a); endmodule")
    assert refusal_reason("module mul1(); assign p = $signed(x) + y; endmodule") is None


# ---- with the real tools -------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
@pytest.mark.parametrize("cfg", [PeConfig("array", "chain", 1), PeConfig("booth4", "csa", 2),
                                 PeConfig("wallace", "tree", 3)])
def test_generated_designs_pass_their_golden_vectors_at_the_claimed_latency(cfg):
    from flux_macarray import verify

    d = generate(cfg, SHAPE)
    v = verify(d, golden_vectors(SHAPE, seed="t"))
    assert v.ok, v.why
    assert v.latency == cfg.pipeline


def test_the_inventor_is_told_measured_numbers_not_a_slogan():
    """Invention runs after the screen, against this run's own worst paths and areas for the built-ins (D370)."""
    from flux_macarray.world import beat_text

    assert "behavioral" in beat_text([]) and "ps" not in beat_text([]), (
        "no measurements yet: the slogan, not invented numbers")
    screened = [
        _pt("behavioral-tree-p0", 420, 1041), _pt("booth4-tree-p0", 378, 1063),
        _pt("wallace-tree-p0", 414, 1351), _pt("array-tree-p0", 429, 1332),
        _pt("behavioral-csa-p1", 400, 700),          # wrong reducer: not a comparison point
    ]
    text = beat_text(screened)
    assert "behavioral: 1041 ps worst path, 420 um2" in text
    assert text.index("behavioral") < text.index("booth4") < text.index("wallace"), (
        "shortest path first")
    assert "csa" not in text
    # once a PE makes the clock, the number to beat is the smallest one that does, on area (D543)
    assert "CLOCK IS MET" not in beat_text(screened, target=1500.0, clock_ps=667.0)   # nothing makes 1.5 GHz
    shrink = beat_text(screened, target=900.0, tolerance=0.0, clock_ps=1111.0)
    assert "CLOCK IS MET" in shrink and "booth4-tree-p0: 378 um2" in shrink and "1111 ps" in shrink
    assert "NOT a win now" in shrink


# ---- the study on the loop (D446) --------------------------------------------------------

def _stub_run(design, *, stage, clock_period_ps):
    """A tool-free measurement: area from the RTL's length, the path from the multiplier's name."""
    slack = {"behavioral": 120.0, "booth4": 200.0}.get(design.config.multiplier, 60.0)
    if design.config.pipeline:
        slack += 150.0 * design.config.pipeline
    if stage == "placement":
        slack -= 80.0
    return {"area_um2": 300.0 + len(design.all_sources) / 40 + 40 * design.config.pipeline,
            "worst_slack_ps": slack, "clock_period_ps": clock_period_ps, "power_w": 0.01,
            "cell_count": 100, "flow_depth": stage, "critical_path": "a -> p"}


@pytest.mark.skipif(shutil.which("verilator") is None or shutil.which("yosys") is None
                    or shutil.which("openroad") is None, reason="needs the three tools on PATH")
def test_the_study_runs_on_the_loop_and_reports_in_its_own_terms(tmp_path):
    """The document's world is the loop's search path: space as first batch, Verilator as gate,
    the study's Measurer as stages, and the report in the study's terms (D446, D533)."""
    from flux_loop import request_for, run_loop, task_report_lines
    from flux_macarray.world import pe_scored
    from flux_records import Records

    db = str(tmp_path / "mac.db")
    prob = macarray_problem(multipliers=["behavioral", "booth4"], reducers=["tree"], pipelines=[0, 1],
                            include_invented=False)
    prob.world.run = _stub_run                                  # the three tools, stood in for
    req = request_for(prob.task, db=db, finalists=2, workers=2)
    lines: list[str] = []
    out = run_loop(prob, req, proposer=None, log=lines.append)
    screened, confirmed = prob.world.screened(out), prob.world.confirmed(out)
    assert len(screened) == 4 and confirmed, "screened all four; placed the finalists"
    assert out.decision is not None and "smallest area" in out.decided_by
    inc = prob.world.incumbent(out)
    assert inc is not None and inc.config == DEFAULT
    assert any(l.startswith("screen:") for l in lines) and any("confirm:" in l for l in lines)
    report = task_report_lines(prob.task, out, prob)
    assert any("the fmax-vs-area frontier" in l for l in report) and any("DECISION" in l for l in report)
    rec = Records(db, objective={"study": "macarray"}, name="macarray")
    assert rec.resumed and rec.stages() and "screen" in rec.stages()
    assert len(rec.known(stage="screen", metric="fmax_mhz")) == 4
    assert rec.conclusions(limit=1)[0]["decision"] == pe_scored(out.decision).label
    rec.close("paused")


@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs Verilator on PATH for the gate")
@pytest.mark.heavy
@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
def test_a_kept_invention_joins_the_space_with_its_pragmas_and_its_pes_build(tmp_path, monkeypatch):
    """An invented multiplier joins the space as kept, with its lint pragmas, so its PEs verify (D552)."""
    import flux_macarray.invent as inv
    from flux_llm import ScriptedProposer
    from flux_loop import request_for, run_loop

    prob = macarray_problem(multipliers=["behavioral"], reducers=["tree"], pipelines=[0],
                            include_invented=False)
    prob.world.run = _stub_run
    prob.world.invented_dir = tmp_path                  # D578: the document's out/invented, here a scratch dir
    reply = ("IDEA: the behavioral product, as a check of the plumbing\n```verilog\n"
             "module mul1(input logic signed [7:0] a, input logic signed [7:0] w, output logic signed [15:0] p);\n"
             "  wire signed [15:0] t;\n  assign t = a * w;\n  assign p = t;\nendmodule\n```")   # not the refused `p = a * w` literal
    out = run_loop(prob, request_for(prob.task, db=str(tmp_path / "mac.db"), steps=2, finalists=0, screen_only=True,
                                     workers=2, tools=False),
                   proposer=ScriptedProposer([reply] * 4), log=lambda _m: None)
    assert "mul1" in prob.world.invented and prob.world.invented["mul1"].startswith(inv.LINT_PRAGMA)
    assert (tmp_path / "mul1.sv").is_file()
    assert not any("did not compile" in why for _n, why in out.refused), out.refused
    assert any(name.startswith("mul1-") for name in prob.world.designs), "the invented PE never joined the space"


def test_the_invention_round_runs_on_the_problem_and_asks_to_shrink_once_the_clock_is_met(tmp_path):
    """The multiplier turn is the problem's (its tools, its defaults), and once a PE makes the
    clock the brief asks for a smaller one (D543)."""
    from flux_llm import ScriptedProposer
    from flux_loop import request_for, run_loop

    prob = macarray_problem(multipliers=["behavioral", "booth4"], reducers=["tree"], pipelines=[0],
                            include_invented=False)
    prob.world.run = _stub_run
    proposer = ScriptedProposer(["IDEA: nothing\nnothing to build in this reply"] * 16)
    req = request_for(prob.task, db=str(tmp_path / "mac.db"), steps=2, finalists=0, screen_only=True,
                      workers=2, tools=True)
    out = run_loop(prob, req, proposer=proposer, log=lambda _m: None)
    assert not any("did not run" in n for n in out.not_established), out.not_established
    assert proposer.prompts, "the model was asked"
    assert "CLOCK IS MET" in proposer.prompts[0] and "Beat it on AREA" in proposer.prompts[0]
    assert any("no module named" in why for _n, why in out.refused), out.refused


def test_the_world_measures_through_the_loops_cache_under_the_tools_stage_names():
    """`measure` passes the tools their own stage name, returns raw numbers under `raw`, and
    `cache_key` is the design's identity on that stage (D567)."""
    from flux_loop import LoopRequest, LoopState
    from flux_macarray.measure import CONFIRM, SCREEN
    from flux_macarray.world import CONFIRM_STAGE, SCREEN_STAGE, pe_scored

    prob = macarray_problem(multipliers=["behavioral"], reducers=["tree"], pipelines=[0])
    world = prob.world
    seen = []

    def spy(d, *, stage, clock_period_ps):
        seen.append(stage)
        return _stub_run(d, stage=stage, clock_period_ps=clock_period_ps)

    world.run = spy
    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    [cand] = world.instantiate([{"multiplier": "behavioral", "reducer": "tree", "pipeline": 0}], state)
    got = world.measure(cand, SCREEN_STAGE, state)
    assert seen == [SCREEN] and "raw" in got and got["provenance"] == "enumerate" and got["fmax_mhz"] > 0
    world.measure(cand, CONFIRM_STAGE, state)
    assert seen == [SCREEN, CONFIRM]
    assert world.cache_key(cand, SCREEN_STAGE, state) != world.cache_key(cand, CONFIRM_STAGE, state)
    assert world.cache_key(cand, SCREEN_STAGE, state).endswith("|abc=full")
    from flux_loop import Scored

    p = Scored(cand, SCREEN_STAGE, {k: v for k, v in got.items() if isinstance(v, (int, float))},
               {k: v for k, v in got.items() if not isinstance(v, (int, float))})
    assert pe_scored(p) is pe_scored(p) and pe_scored(p).config.label == "behavioral-tree-p0"


def test_the_macarray_stands_on_its_goal_and_what_it_is_doing():
    """The results tab's head for the PE study (D573)."""
    from flux_loop import LoopRequest, LoopState

    prob = macarray_problem(multipliers=["behavioral"], reducers=["tree"], pipelines=[0])
    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    st = prob.standing(state)
    assert st["goal"].startswith("the smallest PE (8 lanes, int8 x int8 -> int20 with accumulator input) that makes 1000 MHz placed on ASAP7")
    assert st["now"].startswith("verifying and screening the space")


@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
def test_a_pe_that_lies_about_its_latency_is_refused_and_a_wrong_multiplier_says_what_went_in():
    """The shared check refuses a design whose cycles differ from its claim, and a failing multiplier hears the failing inputs (D582)."""
    from dataclasses import replace

    from flux_codegen_rtl_harness import check_rtl
    from flux_macarray import verify
    from flux_macarray.invent import multiplier_golden

    cfg = PeConfig("array", "chain", 2)
    d = generate(cfg, SHAPE)
    v = verify(replace(d, config=replace(cfg, pipeline=3)), golden_vectors(SHAPE, seed="t"))
    assert not v.ok and "claims 3 cycle(s) of latency, measured 2" in v.why
    wrong = "module m(input signed [7:0] a, input signed [7:0] w, output signed [15:0] p);\n  assign p = a * w + 16'sd1;\nendmodule\n"
    got = check_rtl(wrong, multiplier_golden(SHAPE), module="m", relaxed=True)
    assert not got.ok and "-- for a=" in got.why and "expected p=" in got.why
