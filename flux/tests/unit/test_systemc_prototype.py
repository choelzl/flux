"""A SystemC prototype (D635): `budget.prototype: systemc` makes the model prove the algorithm as
an SC_MODULE, checked on every golden vector by a generated testbench against libsystemc."""

from __future__ import annotations

import pytest

from flux_loop import PromptProblem, TaskSpec
from flux_loop.systemc_proto import icsc, systemc_home, translate

GOLDEN = '''PORTS = [{"name": "a", "dir": "in", "bits": 4, "unsigned": True},
         {"name": "b", "dir": "in", "bits": 4, "unsigned": True}, {"name": "s", "dir": "out", "bits": 5}]


def golden(a, b):
    return {"s": a + b}
'''

RIGHT = """#include <systemc.h>
SC_MODULE(add4) {
  sc_in<sc_uint<4>> a, b;
  sc_out<sc_uint<5>> s;
  void run() { s.write(a.read() + b.read()); }
  SC_CTOR(add4) { SC_METHOD(run); sensitive << a << b; }
};
"""


def _prototype(tmp_path):
    (tmp_path / "golden.py").write_text(GOLDEN)
    doc = {"id": "add4",
           "statement": "An unsigned 4-bit adder: module `add4`, inputs `a`, `b`, output `s`.",
           "language": "systemverilog",
           "objectives": [{"metric": "area_um2", "direction": "minimize"}],
           "budget": {"prototype": "systemc"},
           "flow": {"test": "flux rtl test {artifact} --golden {home}/golden.py"}}
    return PromptProblem(TaskSpec.from_dict(doc, base=tmp_path)).prototype()


def test_the_document_asks_for_a_systemc_prototype(tmp_path):
    cap = _prototype(tmp_path)
    assert cap.language == "systemc" and cap.extra["marker"] == "SC_MODULE(add4)" and cap.domain_size == 256
    assert "sc_uint" in cap.extra["rules"]


@pytest.mark.skipif(systemc_home() is None, reason="SystemC is not installed (SYSTEMC_HOME)")
def test_every_vector_is_checked_against_the_golden_model(tmp_path):
    cap = _prototype(tmp_path)
    assert cap.check(RIGHT, None, None).ok
    wrong = cap.check(RIGHT.replace("a.read() + b.read()", "a.read() | b.read()"), None, None)
    assert not wrong.ok and "of 256 vectors wrong" in wrong.why and wrong.score > 0
    broken = cap.check(RIGHT.replace("s.write(", "s.wrte("), None, None)
    assert not broken.ok and "does not compile" in broken.why
    floats = cap.check(RIGHT.replace("void run()", "double k = 1.0; void run()"), None, None)
    assert not floats.ok and "double" in floats.why
    assert "no `SC_MODULE(add4)`" in cap.check("int x;", None, None).why


@pytest.mark.skipif(icsc() is None, reason="no ICSC_HOME: run in `nix develop .#systemc`")
def test_icsc_translates_the_verified_module_and_the_gate_proves_it(tmp_path):
    """D636: the verified SC_MODULE becomes SystemVerilog by ICSC, not by the model; the SV
    has the golden ports and passes `flux rtl test` on every vector."""
    import subprocess
    import sys

    from flux_loop.golden_proto import load

    cap = _prototype(tmp_path)
    # `s` read as unsigned by the RTL harness (5 signed bits cannot hold 30)
    (tmp_path / "golden.py").write_text(GOLDEN.replace('"bits": 5}', '"bits": 5, "unsigned": True}'))
    sv, why = cap.extra["translate"](RIGHT)
    assert sv and not why and "module add4" in sv and "always_comb" in sv
    (tmp_path / "add4.sv").write_text(sv)
    run = subprocess.run([sys.executable, "-c", "import sys; from flux_cli.main import main; sys.exit(main())",
                          "rtl", "test", str(tmp_path / "add4.sv"), "--golden", str(tmp_path / "golden.py")],
                         capture_output=True, text=True, timeout=600)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    bad, why = translate(RIGHT.replace("sc_out<sc_uint<5>> s", "sc_out<sc_uint<6>> s"), "add4", load(tmp_path / "golden.py"))
    assert not bad and why                    # a port the golden does not have: no SV
