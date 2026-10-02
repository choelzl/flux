"""The MAC-PE microarchitecture study (D365), a document and its step commands since D798.

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
from flux_macarray.invent import refusal_reason  # noqa: E402

SHAPE = Shape(lanes=8, in_bits=8, w_bits=8, accumulate=True)


# ---- the space -----------------------------------------------------------------------------


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


def test_the_shape_derives_the_accumulator_width_never_chooses_it():
    assert SHAPE.product_bits == 16
    assert SHAPE.acc_bits == 16 + 3 + 1, "8 products need 3 bits; the accumulator input one more"
    assert Shape(lanes=8, in_bits=8, w_bits=8, accumulate=False).acc_bits == 19


# ---- invention -----------------------------------------------------------------------------

def test_an_invented_multiplier_s_rules_are_enforced_before_any_tool():
    src = ("module mul1(input logic signed [7:0] a, input logic signed [7:0] w, output logic signed [15:0] p);\n"
           "  assign p = a * w;\nendmodule\n")
    assert refusal_reason(src) and "behavioral" in refusal_reason(src)
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


# ---- the study on the loop (D446) --------------------------------------------------------


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


# ---- D798: the study is two documents and the commands of `flux_macarray.steps`
def test_the_document_spans_the_space_and_kept_inventions_join_it(tmp_path):
    """The PE study's space is every multiplier, reducer and depth the generator spells, plus the
    multipliers `invent.problem.yaml` kept in `out/invented/` -- read at each load."""
    from flux_loop import load_task

    home = tmp_path / "macarray"
    home.mkdir()
    for f in ("problem.yaml", "invent.problem.yaml"):
        shutil.copy(FLUX_ROOT / "applications/macarray" / f, home / f)
    task = load_task(home / "problem.yaml")
    assert task.space["multiplier"] == list(MULTIPLIERS) and task.space["reducer"] == ["tree", "chain", "csa"]
    assert len(task.space["pipeline"]) * len(task.space["reducer"]) * len(task.space["multiplier"]) == 48
    (home / "out/invented").mkdir(parents=True)
    (home / "out/invented/inv_0123456789.sv").write_text("module inv_0123456789(); endmodule\n")
    again = load_task(home / "problem.yaml")
    assert again.space["multiplier"] == [*MULTIPLIERS, "inv_0123456789"]
    written = again.to_dict()["flow"]["orchestrate"]["space"]["multiplier"]
    assert written == {"values": list(MULTIPLIERS), "from": "out/invented/*.sv"}, "written back as said, not as found"
    assert load_task(home / "invent.problem.yaml").record == "macarray.invent"


@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs Verilator")
def test_the_steps_generate_check_and_keep(tmp_path):
    from flux_macarray.steps import main

    pe = tmp_path / "pe.sv"
    assert main(["gen", str(pe), "booth4", "tree", "1"]) == 0 and "module mac_pe" in pe.read_text()
    assert main(["check", str(pe), "1"]) == 0
    assert main(["check", str(pe), "0"]) != 0, "a PE that lies about its latency is refused"
    mult = tmp_path / "m.sv"
    mult.write_text("module mult_inv(input logic signed [7:0] a, input logic signed [7:0] w,\n"
                    "                output logic signed [15:0] p);\n  wire signed [15:0] x = a;\n  assign p = x * w;\nendmodule\n")
    keep = tmp_path / "invented"
    assert main(["mult-check", str(mult), "--keep", str(keep)]) == 0
    (kept,) = keep.glob("inv_*.sv")
    assert f"module {kept.stem}" in kept.read_text(), "renamed by its content"
    pe2 = tmp_path / "pe2.sv"
    assert main(["gen", str(pe2), kept.stem, "tree", "0", "--invented", str(keep)]) == 0
    assert main(["check", str(pe2), "0"]) == 0
    wrong = tmp_path / "w.sv"
    wrong.write_text(mult.read_text().replace("x * w", "x + w"))
    assert main(["mult-check", str(wrong), "--keep", str(keep)]) == 1 and len(list(keep.glob("*.sv"))) == 1

