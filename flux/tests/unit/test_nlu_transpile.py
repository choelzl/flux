"""D478: the transpiler -- a prototype in the integer subset becomes the RTL, mechanically,
with every width measured over all 65,536 inputs; and the loop uses it, so the model is
asked for the algorithm and never for the transcription.

The three hand-written families of `flux_nlu.floor` (D475/D476) are the FIXTURES here: they
are written in the subset and have known-good numpy faces to compare against. They are not
in the flow (D478): the flow's own work is what the campaign is for."""

from __future__ import annotations

import inspect
import json
import shutil

import numpy as np
import pytest

from flux_nlu import floor as F
from flux_nlu.transpile import TranspileError, transpile


def prototype_of(op: str) -> tuple[str, object, object]:
    """A `design(x)` prototype in the subset from a family's cheapest configuration."""
    fam = F.FAMILIES[op]
    cfg = F.search(op, limit=1)[0]["config"]
    model = fam["model"]
    tables = getattr(F, f"{op}_tables")
    attrs = "\n".join(f"    {k} = {v!r}" for k, v in cfg.__dict__.items())
    if op == "exp":
        attrs += f"\n    LN2_BITS = 10\n    ff = {cfg.xf + cfg.lb}"
    if op == "rsqrt":
        attrs += f"\n    SLOPE_BITS = 12\n    mp_bits = {cfg.t + cfg.cb}"
    src = ("import numpy as np\nimport math\n\n" + inspect.getsource(F._round_half_even) + "\n"
           + inspect.getsource(tables) + "\n" + inspect.getsource(model)
           + f"\nclass cfg:\n{attrs}\n\ndef design(x):\n    return {model.__name__}(x, cfg)\n")
    src = src.replace("QNAN", "0x7E00").replace("PINF", "0x7C00").replace("NINF", "0xFC00")
    return _without_annotations(src), cfg, model


def _without_annotations(src: str) -> str:
    """A model's prototype has no type annotations; the families' functions do, and the
    sandbox has no `RecipConfig` to evaluate them against."""
    import ast

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            node.returns = None
            for a in node.args.args:
                a.annotation = None
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            node.annotation = ast.Constant(value=None)
    return ast.unparse(tree)


def test_the_transpiler_spells_the_subset_with_measured_widths():
    src, cfg, model = prototype_of("exp")
    rtl = transpile(src, top="nlu_exp")
    assert rtl.startswith("module nlu_exp (input logic clk, input logic [15:0] x, output logic [15:0] y);")
    assert "every width\n  // measured over all 65536 inputs" in rtl
    assert "logic signed [32:0] tt__1_" in rtl            # x * log2e, 33 bits signed, measured
    assert "function automatic [12:0] TAB__1(" in rtl     # the 2^f table as a ROM (the inlined `tab`)
    assert rtl.count("assign ") > 60 and "assign y = 16'(" in rtl
    assert "wire" not in rtl and "reg " not in rtl


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
@pytest.mark.parametrize("op", ["exp", "rsqrt", "recip"])
def test_transpiled_rtl_is_bit_identical_to_the_prototype(op, tmp_path):
    from flux_nlu.fp16 import all_inputs, ulp_report
    from flux_nlu.verify import build_sim

    src, cfg, model = prototype_of(op)
    rtl = transpile(src, top=f"nlu_{op}")
    xs = all_inputs()
    sim = build_sim(rtl, top=f"nlu_{op}", latency=0, opcode=None, workdir=str(tmp_path))
    got = sim.run(xs)
    assert (got == model(xs, cfg)).all()
    assert ulp_report(op, xs, got)["over_budget"] == 0


@pytest.mark.parametrize("body,needle", [
    ("    if (x > 5).any():\n        return x\n    return x + 1", "an `if` on a value that depends on the input"),
    ("    return (x // (x + 1)).astype(np.uint16)", "division by a signal"),
    ("    t = np.arange(16)\n    return t[x & 15][x & 1]", "indexing a signal"),
    ("    return np.sqrt(x).astype(np.uint16)", "call `np.sqrt` is not in the subset"),
    ("    y = x - 40000\n    return (y // 3).astype(np.uint16)", "floor division of a signed value by 3"),
])
def test_refusals_name_the_construct_and_the_line(body, needle):
    src = "import numpy as np\n\ndef design(x):\n    x = x.astype(np.int64)\n" + body + "\n"
    with pytest.raises(TranspileError, match=needle):
        transpile(src, top="nlu_t")


def test_the_idioms_that_lost_a_zero_over_prototype_are_spelled():
    """D480: `a[mask] = v`, `a, b = e1, e2`, `a = b = e`, `np.full_like` -- and a table the
    size of the domain is refused as the reference in a costume."""
    code = """import numpy as np
T = np.rint(np.exp2(np.arange(64) / 64) * 4096).astype(np.int64)
def design(x):
    x = x.astype(np.int64)
    sign, ex = (x >> 15) & 1, (x >> 10) & 0x1F
    result = np.full_like(x, 0x7E00)
    is_inf = (ex == 31) & ((x & 0x3FF) == 0)
    result[is_inf] = 0x7C00
    lo = hi = x & 63
    result[ex < 31] = (T[lo] >> 2) + hi
    return result.astype(np.uint16)
"""
    rtl = transpile(code, top="nlu_t")
    assert "T(" in rtl and "module nlu_t" in rtl
    with pytest.raises(TranspileError, match="65536-entry table .* is the reference as a ROM"):
        transpile(code.replace("np.arange(64) / 64", "np.arange(65536) / 65536").replace("x & 63", "x"),
                  top="nlu_t")


def test_the_gate_refuses_both_cheats_the_live_run_found():
    """D480: a whole-domain table of the reference (the rule), and the reference smuggled
    through a file the compute tool wrote (the sandbox)."""
    from flux_loop import screen_snippet
    from flux_nlu.prototype_rules import hardware_subset_violations

    rom = ("import numpy as np\n_all = np.arange(65536, dtype=np.uint16)\n"
           "_ref = np.log(_all.view(np.float16).astype(np.float64))\ndef design(x):\n    return _ref[x]\n")
    v = hardware_subset_violations(rom)
    assert v and "65536-entry table is the input domain itself" in v[0]
    assert screen_snippet("import numpy as np\nrefs = np.load('/tmp/refs.npy')\ndef design(x):\n    return refs[x]\n")
    assert screen_snippet("import numpy as np\nnp.save('/tmp/refs.npy', np.arange(3))\n")
    honest = ("import numpy as np\nT = np.rint(np.exp2(np.arange(64) / 64) * 4096).astype(np.int64)\n"
              "def design(x):\n    return (T[x & 63] >> 2).astype(np.uint16)\n")
    assert hardware_subset_violations(honest) == [] and screen_snippet(honest) is None


def test_a_constant_if_and_a_constant_helper_call_are_resolved():
    src = """import numpy as np

class cfg:
    corr = True
    m = 12

def table(cfg):
    return np.rint(np.arange(8) * (1 << cfg.m) / 8).astype(np.int64)

def design(x):
    x = x.astype(np.int64)
    tab = table(cfg)
    v = tab[x & 7]
    if cfg.corr:
        v = v + (x >> 3)
    else:
        v = v - 1
    return (v & 0xFFFF).astype(np.uint16)
"""
    rtl = transpile(src, top="nlu_t")
    assert "TAB(" in rtl and "+ " in rtl and "- " not in rtl.split("TAB(")[0]


@pytest.mark.skipif(shutil.which("verilator") is None, reason="verilator not on PATH")
def test_the_loop_makes_the_rtl_from_the_models_prototype_without_a_transcription_turn(tmp_path):
    """The flow creates the operator: a scripted 'model' answers the prototype prompt with
    a design in the subset and is never asked to write RTL; the transpiler, the fast check
    and the exhaustive gate do the rest, and the part is admitted."""
    from flux_loop import LoopRequest, run_loop
    from flux_nlu.problem import NluProblem

    src, _cfg, _model = prototype_of("recip")
    asked: list[str] = []

    class Proto:
        def propose(self, prompt, schema=None):
            asked.append(prompt)
            if "PROTOTYPE FIRST" in prompt:
                return json.dumps({"prototype": src})
            raise AssertionError("the model was asked to write RTL")

    prob = NluProblem(ops=("recip",), ulp_budget=1, clock_period_ps=1250.0, seed=1, test_rounds=0)
    said: list[str] = []
    out = run_loop(prob, LoopRequest(steps=1, repair_attempts=1, prototype_attempts=2,
                                     screen_only=True, params={"seed": 1}),
                   proposer=Proto(), log=said.append)
    assert "recip" in out.admitted and out.admitted["recip"].name == "transpiled_recip"
    assert out.admitted["recip"].meta.get("transpiled") is True
    assert any("transpiled from the verified prototype; passes the fast check" in m for m in said)
    assert all("TRANSCRIBE" not in p for p in asked)


def test_keyword_arguments_bind_by_the_callees_signature():
    """D491: tanh's 0-over member was refused for `from_fixed(v, M, sign=s)` -- a keyword
    the transpiler could bind from the block's signature and did not."""
    from flux_nlu.blocks import with_prelude
    from flux_nlu.fp16 import all_inputs

    src = with_prelude("def design(x):\n    v = (bits(x) & 0x3FF) + 1024\n"
                       "    return from_fixed(v, 10, sign=fp16_sign(x))\n")
    rtl = transpile(src, top="nlu_t", xs=all_inputs())
    assert "module nlu_t" in rtl
    with pytest.raises(TranspileError, match="no keyword `nope`"):
        transpile(src.replace("sign=fp16_sign(x)", "nope=fp16_sign(x)"), top="nlu_t", xs=all_inputs())


def test_a_unary_operator_never_touches_a_size_cast():
    """D495: `~2'(v)` is a cast of size ~2 to yosys ("Static cast with zero or negative
    size!") though Verilator reads it as `~(2'(v))`; sigmoid's RTL passed the bit-exact
    gate and failed synthesis. The operand of a unary operator is parenthesised."""
    import re

    from flux_nlu.blocks import with_prelude
    from flux_nlu.fp16 import all_inputs

    src = with_prelude("def design(x):\n    z = is_zero(x)\n    return np.where(~z & 1, bits(x) & 0x7FFF, -bits(x) & 0xFFFF)\n")
    rtl = transpile(src, top="nlu_t", xs=all_inputs())
    assert "module nlu_t" in rtl and "(~(" in rtl
    assert not re.search(r"[~!-]\d+'\(", rtl), "a unary operator directly before a size cast"


def test_a_pipelined_transpile_is_bit_exact_at_its_latency_and_the_mux_equalises(tmp_path):
    """D496: `PIPELINE = K` (or `pipeline=K`) cuts the measured program into K+1 stages by
    logic level and registers every signal that crosses a cut -- latency K, judged by the
    same exhaustive driver with that latency. Two operators of different latencies under
    the framework's mux answer after the same number of cycles (a delay line on the
    shorter one), so the composition is proven through the top as always."""
    from flux_nlu.blocks import with_prelude
    from flux_nlu.fp16 import OPCODES, all_inputs, ulp_report
    from flux_nlu.problem import wrap_per_op
    from flux_nlu.vectorize import vectorize
    from flux_nlu.verify import build_sim
    from test_nlu_vectorize import SCALAR_EXP

    xs = all_inputs()
    src = with_prelude(vectorize(SCALAR_EXP.replace("T = 5; M = 12", "T = 5; M = 12; PIPELINE = 3", 1))[0])
    assert "PIPELINE = 3" in src
    rtl = transpile(src, top="nlu_exp", xs=xs)                   # the prototype's own K
    assert "PIPELINED into 4 stages by logic level, latency 3" in rtl and "always_ff @(posedge clk)" in rtl
    assert rtl.count("<=") >= 3
    sim = build_sim(rtl, top="nlu_exp", latency=3, opcode=None, workdir=str(tmp_path))
    assert ulp_report("exp", xs, sim.run(xs))["over_budget"] == 0
    # explicit K overrides; K = 0 is the combinational module
    flat = transpile(src, top="nlu_exp", xs=xs, pipeline=0)
    assert "always_ff" not in flat
    # the mux: exp at latency 3 beside a combinational recip, proven per opcode at latency 3
    recip = with_prelude("def design(x):\n    return specials(x, QNAN, PINF, NINF, PZERO, NZERO, "
                         "pack_fp16(fp16_sign(x), -exponent_unbiased(x) - 1, approx_on_mantissa(recip, mantissa11(x), 5, 12) << 1 >> 2, 10))\n")
    recip_rtl = transpile(recip, top="nlu_recip", xs=xs)
    top = wrap_per_op(rtl + "\n" + recip_rtl, ("exp", "recip"), {"exp": 3, "recip": 0})
    assert "y_recip_d3 <= y_recip_d2" in top and "3'd%d: y = y_recip_d3;" % OPCODES["recip"] in top
    got = build_sim(top, top="nlu", latency=3, opcode=OPCODES["exp"], workdir=str(tmp_path / "e")).run(xs)
    assert ulp_report("exp", xs, got)["over_budget"] == 0
    alone = build_sim(recip_rtl, top="nlu_recip", latency=0, opcode=None, workdir=str(tmp_path / "a")).run(xs)
    muxed = build_sim(top, top="nlu", latency=3, opcode=OPCODES["recip"], workdir=str(tmp_path / "r")).run(xs)
    assert (muxed == alone).all()                               # delayed three cycles, unchanged
