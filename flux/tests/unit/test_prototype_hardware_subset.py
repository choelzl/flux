"""A prototype that calls the reference function is refused before it runs (docs/decisions.md
D468).

The live NLU run's `exp` prototype was `np.exp(x.view(np.float16))`: 0 over, because it IS the
reference, and nothing to transcribe. It sat in every RTL prompt for three days as "VERIFIED
PROTOTYPE -- TRANSCRIBE it 1:1", and the best SystemVerilog that came of it was 43% wrong. The
prompt stated the integer-only rule; nothing checked it.

What these pin: the shortcut is refused with the line and what to do instead, an honest
table-plus-shifts prototype passes, module-level table generation with float math is allowed
(that is a ROM being generated), and the NLU gate refuses BEFORE spending the 65536-input run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "applications" / "nlu" / "lib" / "src"))
from flux_nlu.prototype_rules import hardware_subset_violations  # noqa: E402

SHORTCUT = """import numpy as np

def design(x):
    x_f16 = x.view(np.float16)
    y_f16 = np.exp(x_f16)
    return y_f16.view(np.uint16)
"""

HONEST = """import numpy as np
# A ROM, generated once with float math -- what a table generator does.
_LUT = np.round(np.exp(np.arange(256) / 256.0) * 2**10).astype(np.uint16)

def design(x):
    x = np.asarray(x, dtype=np.uint16)
    mant = x & 0x3FF
    expo = (x >> 10) & 0x1F
    y = _LUT[mant >> 2] >> (expo // 4)
    return (y & 0xFFFF).astype(np.uint16)
"""


def test_the_live_runs_shortcut_is_refused_and_told_what_to_do_instead():
    got = hardware_subset_violations(SHORTCUT)
    assert len(got) == 2, got
    assert got[0].startswith("line 4:") and ".view(np.float16)" in got[0]
    assert "masks and shifts" in got[0]
    assert got[1].startswith("line 5:") and "np.exp" in got[1]
    assert "reference function, not an algorithm" in got[1] and "build a ROM" in got[1]


def test_an_honest_prototype_passes_and_its_module_level_table_is_allowed():
    assert hardware_subset_violations(HONEST) == []


def test_a_table_built_inside_design_from_a_constant_range_is_a_rom_not_the_data_path():
    """The seeded reciprocal (D412) builds its 1024-entry table INSIDE design() with float
    math over np.arange(1024) -- the table does not depend on x, so it is a ROM generator."""
    code = """import numpy as np
def design(x):
    x = x.astype(np.int64)
    f = x & 0x3ff
    idx = np.arange(1024)
    val = 2.0 / (1.0 + idx / 1024.0)
    r = np.round((val - 1.0) * 1024.0).astype(np.int64)
    g = r[f]
    return (g & 0x3ff).astype(np.uint16)
"""
    assert hardware_subset_violations(code) == []
    tainted = code.replace("idx = np.arange(1024)", "idx = f")
    got = hardware_subset_violations(tainted)
    assert got and "true division" in got[0], got


@pytest.mark.parametrize("body,expect", [
    ("    return x / 2\n", "true division"),
    ("    return (x * 0.5).astype(np.uint16)\n", "float constant 0.5"),
    ("    return np.sqrt(x).astype(np.uint16)\n", "np.sqrt"),
    ("    import math\n    return math.exp(x)\n", "float math on the data path"),
    ("    return x.astype(np.float32).astype(np.uint16)\n", "turns the FP16 bit pattern"),
    ("    return float(x)\n", "reinterprets the data path as float"),
])
def test_each_way_out_of_the_subset_is_named(body, expect):
    code = "import numpy as np\ndef design(x):\n" + body
    got = hardware_subset_violations(code)
    assert got and expect in got[0], got


def test_a_prototype_without_design_and_one_that_does_not_parse():
    assert "no `design(x)` function" in hardware_subset_violations("import numpy as np\n")[0]
    assert "does not parse" in hardware_subset_violations("def design(x:\n")[0]


def test_the_nlu_gate_refuses_before_running_the_harness(monkeypatch):
    from flux_nlu.problem import NluProblem

    ran: list[str] = []

    def never(*args, **kwargs):
        ran.append("ran")
        return [("prototype", "")]

    monkeypatch.setattr("flux_loop.run_compute", never)
    problem = NluProblem.__new__(NluProblem)      # the check needs nothing of the instance
    problem.ulp_budget = 1
    problem.last_fast = {}

    class State:
        class request:
            compute_timeout_s = 10.0

    verdict = problem.prototype_check(SHORTCUT, "exp", State())
    assert not verdict.ok and "not hardware-implementable" in verdict.why
    assert "np.exp" in verdict.why and verdict.payload["hardware_subset"]
    assert ran == [], "refused before the 65536-input run was spent"


def test_a_rejected_patch_names_the_closest_real_lines():
    from flux_loop import apply_patch

    source = "module m;\n  // Let us request a table\n  assign y = x + 1;\nendmodule\n"
    out, err = apply_patch(source, [{"find": "  // Let's request a tab", "replace": "//"}])
    assert out is None and "not in the source" in err
    assert "closest lines in the current source" in err and "2: // Let us request a table" in err


def test_struct_is_allowed_in_the_sandbox():
    from flux_loop import run_compute

    (name, out), = run_compute([{"name": "bits", "code":
                                 "import struct\nprint(struct.pack('<e', 1.0).hex())"}],
                               timeout_s=10.0)
    assert out.strip() == "003c", out


def test_a_recorded_prototype_is_re_verified_on_resume_like_an_admitted_part(tmp_path):
    """A prototype the record blessed under an older check is not verified under today's:
    the resume runs `prototype_check` on it and drops what no longer passes, saying so."""
    from flux_loop import Candidate, LoopRequest, Problem, Verdict, run_loop

    class Proto(Problem):
        name = "proto"
        strict = False

        def __init__(self, strict: bool) -> None:
            self.strict = strict
            self.asked: list[str] = []

        def objective(self, request):
            return {"study": "proto"}

        def subgoals(self):
            return ["op"]

        def prototype_spec(self, subgoal, state):
            return "write the prototype", None

        def prototype_check(self, code, subgoal, state):
            if self.strict and "shortcut" in code:
                return Verdict(False, float("inf"), "the shortcut is the reference, not an algorithm")
            return Verdict(True, 0.0)

        def design_prompt(self, subgoal, method, state, human, prior, prior_why):
            self.asked.append(state.prototypes.get("op", "(no prototype)"))
            return "write it", None

        def parse_design(self, reply, subgoal):
            return Candidate(name="d", artifact="design"), ""

        def build(self, cand, subgoal, state):
            return cand.artifact

        def judge(self, built, cand, subgoal, state):
            return Verdict(False, 1.0, "not yet")

        def compose(self, admitted, state):
            return None

    db = str(tmp_path / "p.db")

    class Scripted:
        def propose(self, prompt: str) -> str:
            if "prototype" in prompt.lower():
                return '{"prototype": "def design(x):\\n    return shortcut(x)"}'
            return "design"

    lax = Proto(strict=False)
    run_loop(lax, LoopRequest(db=db, steps=1, compute=False, critique_rounds=0,
                              prototype_attempts=1, repair_attempts=0),
             proposer=Scripted(), log=lambda _m: None)
    assert lax.asked and "shortcut" in lax.asked[0], "the lax check blessed the shortcut"

    said: list[str] = []
    strict = Proto(strict=True)
    run_loop(strict, LoopRequest(db=db, steps=1, compute=False, critique_rounds=0,
                                 prototype_attempts=1, repair_attempts=0),
             proposer=Scripted(), log=said.append)
    assert any("no longer passes" in m and "reference, not an algorithm" in m for m in said), said
    assert strict.asked == [], (
        "with the only prototype refused, no design was drafted from it: the part stops at "
        "the prototype stage instead of transcribing a shortcut")
