"""The MAC-PE microarchitecture study (docs/decisions.md D365).

Generation is checked without tools: every point of the space produces a module with the port
contract and the structure its name claims. Where Verilator is on PATH the generated designs are
also run against their golden vectors, latency included -- the correctness gate the study
applies before any synthesis time is spent. The objective's rules and the invention loop's
parser and refusals are pinned on their own.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FLUX_ROOT = Path(__file__).resolve().parents[2]

from flux_macarray import (  # noqa: E402
    DEFAULT, MAPPINGS, MULTIPLIERS, PIPELINES, REDUCERS, InvalidConfig, PeConfig, Score, Scored,
    Shape, decide, frontier, generate, golden_vectors, space, spread, validate,
)
from flux_macarray.invent import parse_module, refusal_reason  # noqa: E402

SHAPE = Shape(lanes=8, in_bits=8, w_bits=8, accumulate=True)


# ---- the space -----------------------------------------------------------------------------

def test_the_space_is_exhaustive_and_led_by_the_incumbent():
    pts = space()
    assert len(pts) == len(MULTIPLIERS) * len(REDUCERS) * len(PIPELINES) * len(MAPPINGS) == 96
    assert pts[0] == DEFAULT
    assert len(set(pts)) == 96
    assert len(space(mappings=("delay",))) == 48


def test_the_mapping_is_a_netlist_knob_not_an_rtl_one():
    """Same RTL, a different recipe for the mapper: the label says so, the source does not."""
    a = generate(PeConfig("booth4", "csa", 1, "delay"), SHAPE)
    b = generate(PeConfig("booth4", "csa", 1, "area"), SHAPE)
    assert a.source == b.source
    assert a.config.label == "booth4-csa-p1" and b.config.label == "booth4-csa-p1-area"
    with pytest.raises(InvalidConfig):
        validate(PeConfig(mapping="power"))


# ---- generation ----------------------------------------------------------------------------

def test_every_point_generates_a_module_with_the_port_contract():
    for cfg in space():
        d = generate(cfg, SHAPE)
        src = d.source
        assert "module mac_pe" in src and src.rstrip().endswith("endmodule")
        for i in range(8):
            assert f"a{i}" in src and f"w{i}" in src
        assert "acc_in" in src and "output logic signed [19:0] acc" in src
        assert ("input  logic clk" in src) == cfg.clocked
        assert ("done" in src) == cfg.clocked


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


def test_preserving_fmax_makes_the_incumbents_own_clock_the_target():
    from flux_macarray import MacarrayProblem
    from flux_macarray.flow import MacRequest

    inc = _pt("behavioral-tree-p0", 421, 1167)          # 857 MHz, as placed in the first run
    small = _pt("behavioral-csa-p0", 390, 1072)          # 933 MHz, 7% smaller
    fast = _pt("booth4-csa-p1", 431, 778)
    s = MacarrayProblem(MacRequest(preserve_fmax=True, target_mhz=1000.0))
    assert s.target([inc, small, fast]) == pytest.approx(inc.fmax_mhz)
    pick, how = decide([inc, small, fast], s.target([inc, small, fast]))
    assert pick.label == "behavioral-csa-p0", "the smallest that holds the incumbent's clock"
    # A measured floor is held to within 1%: the 16-lane run placed the incumbent at 805.4 MHz
    # and a 6%-smaller design at 805.1, and an exact comparison chose the incumbent.
    hair = _pt("behavioral-csa-p0", 781, 1000 * 1000 / 805.1)
    inc16 = _pt("behavioral-tree-p0", 831, 1000 * 1000 / 805.4)
    assert decide([inc16, hair], inc16.fmax_mhz)[0].label == "behavioral-tree-p0"
    assert decide([inc16, hair], inc16.fmax_mhz, tolerance=s.tolerance)[0].label == "behavioral-csa-p0"
    assert s.tolerance == 0.01 and plain_tolerance() == 0.0
    assert [p.label for p in frontier([inc16, hair])] == ["behavioral-csa-p0"], "same clock: one point"
    plain = MacarrayProblem(MacRequest(target_mhz=1000.0))
    assert plain.target([inc, small, fast]) == 1000.0


def plain_tolerance() -> float:
    from flux_macarray import MacarrayProblem
    from flux_macarray.flow import MacRequest

    return MacarrayProblem(MacRequest(target_mhz=1000.0)).tolerance


def test_the_shape_derives_the_accumulator_width_never_chooses_it():
    assert SHAPE.product_bits == 16
    assert SHAPE.acc_bits == 16 + 3 + 1, "8 products need 3 bits; the accumulator input one more"
    assert Shape(lanes=8, in_bits=8, w_bits=8, accumulate=False).acc_bits == 19


def test_an_unknown_knob_is_refused():
    with pytest.raises(InvalidConfig):
        validate(PeConfig(multiplier="magic"))
    with pytest.raises(InvalidConfig):
        validate(PeConfig(pipeline=7))



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
    assert v.ok, v.detail
    assert v.latency_cycles == cfg.pipeline


def test_the_inventor_is_told_measured_numbers_not_a_slogan():
    """D370: invention runs after the screen, and the target it must beat carries this run's
    own worst paths and areas for the built-ins, in the PE it will be judged in."""
    from flux_macarray.problem import beat_text

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


# ---- the study on the loop (D446) --------------------------------------------------------

def _stub_run(design, *, stage, clock_period_ps):
    """A measurement without Yosys or OpenROAD: area from the RTL's length, the path from the
    multiplier's name, so the frontier and the decision have something to order."""
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
    """D446: `run_study` is the loop's search path -- the space is the first batch, Verilator
    the gate, the study's own Measurer the stages -- and the result is still a `MacResult`."""
    from flux_macarray.flow import MacRequest, run_study
    from flux_records import Records

    db = str(tmp_path / "mac.db")
    req = MacRequest(db=db, multipliers=("behavioral", "booth4"), reducers=("tree",),
                     pipelines=(0, 1), mappings=("delay",), decide_on_finalists=2,
                     include_invented=False, workers=2)
    lines: list[str] = []
    out = run_study(req, run=_stub_run, log=lines.append)
    assert len(out.screened) == 4 and out.confirmed, "screened all four; placed the finalists"
    assert out.decision is not None and "smallest area" in out.decided_by
    assert out.incumbent is not None and out.incumbent.config == DEFAULT
    assert out.provenance["confirmed_at_placement"] == len(out.confirmed)
    assert any(l.startswith("screen:") for l in lines) and any("confirm:" in l for l in lines)
    assert any("the fmax-vs-area frontier" in l for l in out.lessons)
    rec = Records(db, objective={"study": "macarray", "shape": out.shape.describe(),
                                 "target_mhz": 1000.0, "preserve_fmax": False})
    assert rec.resumed and rec.stages() and "screen" in rec.stages()
    assert len(rec.known(stage="screen", metric="fmax_mhz")) == 4
    assert rec.conclusions(limit=1)[0]["decision"] == out.decision.label
