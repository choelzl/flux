"""The prototype stage from a golden model named by a document's gate, and a golden model
written by the model when the named one does not exist (D604)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from flux_cli.main import main
from flux_codegen_rtl_harness import golden_vectors
from flux_llm import ScriptedProposer
from flux_loop import TaskSpec, load_task
from flux_loop.author import write_golden
from flux_loop.golden_proto import capability, check, exhaustive, golden_path, load


def _rtl(tmp_path: Path, **budget) -> Path:
    assert main(["new", "add8", "--kind", "rtl", "--dir", str(tmp_path / "p")]) == 0
    doc = tmp_path / "p" / "add8" / "problem.yaml"
    if budget:
        d = yaml.safe_load(doc.read_text())
        d["budget"].update(budget)
        doc.write_text(yaml.safe_dump(d, sort_keys=False))
    return doc


def test_the_check_runs_the_prototype_on_the_golden_vectors(tmp_path):
    g = load(Path(__file__).resolve().parents[2] / "applications/mul8/golden.py")
    rows = golden_vectors(g)
    good = "def design(a, w):\n    return {'p': (a * w) & 0xFFFF}\n"
    assert check(good, g, rows).ok
    wrong = "def design(a, w):\n    return {'p': a + w}\n"
    v = check(wrong, g, rows)
    assert not v.ok and v.score > 0 and "expected" in v.why and "design(a=" in v.why
    assert "did not load" in check("def design(a, w):\n    return {\n", g, rows).why
    assert check("x = 1\n", g, rows).why.startswith("the prototype defines no")
    table = "T = [(a * w) & 0xFFFF for a in range(256) for w in range(256)]\ndef design(a, w):\n    return {'p': T[(a % 256) * 256 + w % 256]}\n"
    v = check(table, g, rows)
    assert not v.ok and "65536 entries" in v.why and "not a formula" in v.why
    squares = "SQ = [i * i for i in range(256)]\ndef design(a, w):\n    return {'p': (a * w) & 0xFFFF}\n"
    assert "over the 64" in check(squares, g, rows).why     # D616: 64 by default -- coefficients, not answers
    assert "entries" not in check(table, g, rows, table_max=70000).why   # a problem may raise the cap
    cheat = ("import math\nimport numpy as np\ndef design(a, w):\n    v = float(a) * float(w)\n"
             "    return {'p': int(math.floor(v)) & 0xFFFF}\n")
    v = check(cheat, g, rows)
    assert not v.ok and "computes with floats" in v.why and "float(...)" in v.why and "math.floor" in v.why
    table_ok = "import math\nT = [int(math.floor(i * 1.5)) for i in range(16)]\ndef design(a, w):\n    return {'p': (a * w) & 0xFFFF}\n"
    assert check(table_ok, g, rows).ok, "float math at module level (a table) is allowed"
    builder = ("import math\ndef build():\n    return [int(math.floor(i * 1.5)) for i in range(16)]\nT = build()\n"
               "def helper(a, w):\n    return (a * w) & 0xFFFF\ndef design(a, w):\n    return {'p': helper(a, w)}\n")
    assert check(builder, g, rows).ok, "a table BUILDER may use floats; only what design() runs may not"
    called = "import math\ndef helper(v):\n    return math.floor(v)\ndef design(a, w):\n    return {'p': helper(a * w) & 0xFFFF}\n"
    assert "math.floor" in check(called, g, rows).why, "a function design() calls is held to the rule"
    slow = "def design(a, w):\n    while True:\n        pass\n"
    assert "ran longer" in check(slow, g, rows[:2], timeout_s=2).why


def test_a_document_with_a_golden_gate_has_the_stage(tmp_path, capsys):
    doc = _rtl(tmp_path)
    task = load_task(doc)
    cap = capability(task)
    assert cap is not None and cap.extra["signature"] == "design(a, b)" and cap.domain_size > 0
    assert "def golden" in cap.contract and cap.language == "python"
    capsys.readouterr()
    main(["task", "check", str(doc)])
    assert "prototype: python, checked against golden.py" in capsys.readouterr().out   # then spelled by the loop


def test_a_missing_golden_is_written_by_the_model_and_checked(tmp_path, capsys):
    doc = _rtl(tmp_path)
    (doc.parent / "golden.py").unlink()
    task = load_task(doc)
    capsys.readouterr()
    main(["task", "check", str(doc)])
    assert "golden.py does not exist -- the model writes it" in capsys.readouterr().out
    bad = "FILE golden.py\n```python\nPORTS = []\n```\nWHY: first try"
    good = ("FILE golden.py\n```python\nPORTS = [{'name': 'a', 'dir': 'in', 'bits': 8, 'unsigned': True},\n"
            "         {'name': 'b', 'dir': 'in', 'bits': 8, 'unsigned': True},\n"
            "         {'name': 's', 'dir': 'out', 'bits': 9, 'unsigned': True}]\n\n\n"
            "def golden(a, b):\n    return {'s': a + b}\n```\nWHY: the sum")
    model = ScriptedProposer([bad, good])
    said: list[str] = []
    assert write_golden(task, model, say=said.append) == ""
    assert golden_path(task).is_file() and len(model.prompts) == 2
    assert "REFUSED" in model.prompts[1] and any("written by the model" in s for s in said)
    assert capability(task) is not None


def test_the_prototype_is_proven_then_transcribed(tmp_path, monkeypatch):
    """A scripted run: a wrong prototype, repaired from its failing vectors, passes the golden
    check; the loop spells it (D611) and the module passes Verilator and is decided."""
    from flux_loop import PromptProblem, request_for, run_loop

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    doc = _rtl(tmp_path, prototype=True, steps=1)
    task = load_task(doc)
    wrong = json.dumps({"prototype": "def design(a, b):\n    return {'s': a - b}\n", "why": "first"})
    proto = json.dumps({"edits": [{"find": "a - b", "replace": "a + b"}], "why": "the sum"})
    rtl = json.dumps({"artifact": "module add8(input logic [7:0] a, input logic [7:0] b, output logic [8:0] s);\n"
                                  "  assign s = a + b;\nendmodule\n"})
    model = ScriptedProposer([wrong, proto, rtl])
    said: list[str] = []
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "d.db"), screen_only=True),
                   proposer=model, log=said.append)
    assert out.decision is not None, said[-20:]
    assert "PROTOTYPE FIRST" in model.prompts[0] and "design(a, b)" in model.prompts[0]
    assert "There is no prototype yet" in model.prompts[0] and "edits" not in model.schemas[0]["properties"]
    assert "def golden(a: int, b: int)" in model.prompts[0], "the golden model is the prototype's specification"
    assert "vectors wrong" in model.prompts[1], "the repair carries the failing vectors"
    # the verified prototype is spelled by the loop, so the model's third reply (a module) is never asked for (D611)
    assert len(model.prompts) == 2 and "spelled" in out.decision.candidate.name
    assert "flux py2sv" in out.decision.candidate.artifact


def test_a_turn_that_ran_nothing_is_not_told_it_made_things_worse():
    """A turn whose reply carried nothing does not repeat the stale PROGRESS line (D605)."""
    from flux_loop.prototype import _unrun

    measured = "PROGRESS: your last edit made it WORSE, 588 -> 1004 failing vectors.\n1004 of 1004 vectors wrong"
    once = _unrun(measured, "your reply carried neither a prototype nor edits")
    assert once.startswith("NOTHING WAS RUN THIS TURN: your reply carried neither")
    assert "THE LAST MEASURED ATTEMPT (an earlier turn) said:\nPROGRESS: your last edit made it WORSE" in once
    twice = _unrun(once, "your patch was rejected: x")
    assert twice.count("NOTHING WAS RUN") == 1 and twice.count("THE LAST MEASURED") == 1


def test_a_small_input_space_is_checked_exhaustively():
    """16 input bits are checked exhaustively at the prototype stage, so a wrong corner is refused (D606)."""
    from flux_loop.golden_proto import exhaustive

    g = load(Path(__file__).resolve().parents[2] / "applications/mul8/golden.py")
    rows = exhaustive(g)
    assert rows is not None and len(rows) == 65536
    sneaky = "def design(a, w):\n    return {'p': 0 if a == -77 and w == 113 else (a * w) & 0xFFFF}\n"
    assert check(sneaky, g, golden_vectors(g)).ok, "the sampled vectors do not include (-77, 113)"
    v = check(sneaky, g, rows)
    assert not v.ok and v.score == 1 and "a=-0x4d, w=0x71" in v.why


def test_the_prototype_tables_are_spelled_by_the_loop_and_compile(tmp_path):
    """A verified prototype's module-level tables become SystemVerilog functions after the port list, shown folded in the prompt (D606)."""
    from flux_codegen_rtl_harness import Golden, check_rtl
    from flux_loop.golden_proto import TABLES_MARK, fold_tables, insert_tables, table_functions

    code = "T = [-3, 5, 7, -8]\nU = [i * i for i in range(10)]\ndef design(i):\n    return {'y': T[i]}\n"
    fns, lines = table_functions(code)
    assert TABLES_MARK in fns and "function automatic logic signed [3:0] T_rom(input logic [1:0] flux_T_index);" in fns
    assert "function automatic logic [6:0] U_rom(input logic [3:0] flux_U_index);" in fns and "default: U_rom = '0;" in fns
    assert any("`T_rom(i)`" in ln and "two's complement" in ln for ln in lines)
    module = "module t(input logic [1:0] i, output logic signed [3:0] y);\n  assign y = T_rom(i);\nendmodule\n"
    full = insert_tables(module, fns)
    assert insert_tables(full, fns) == full, "inserted once"
    shown = fold_tables(full)
    assert "T_rom, U_rom" in shown and "4'hd" not in shown and shown.count("\n") <= 6
    g = Golden(ports=({"name": "i", "dir": "in", "bits": 2, "unsigned": True},
                      {"name": "y", "dir": "out", "bits": 4}), fn=lambda i: {"y": [-3, 5, 7, -8][i]})
    got = check_rtl(full, g, module="t")
    assert got.ok, (got.error, got.lines)


def test_edits_win_over_a_whole_text_in_the_same_reply_and_a_near_copy_is_an_edit():
    """A reply with both `edits` and an edited `prototype` is not refused as a rewrite (D607)."""
    from flux_loop.prototype import _mostly_same, _parse_prototype

    both = json.dumps({"edits": [{"find": "a", "replace": "b"}], "prototype": "def design(x):\n    return {}\n"})
    assert _parse_prototype(both) is None, "the edits are the answer"
    only = json.dumps({"prototype": "def design(x):\n    return {}\n"})
    assert _parse_prototype(only) == "def design(x):\n    return {}\n"
    old = "\n".join(f"line {i}" for i in range(40))
    assert _mostly_same(old, old.replace("line 7", "line seven"))
    assert not _mostly_same(old, "\n".join(f"other {i}" for i in range(40)))


def test_a_spelled_design_sent_back_gets_a_cost_pass_on_its_prototype(tmp_path, monkeypatch):
    """Exploring a spelled design reworks its prototype by hardware cost (`py2sv.cost`); the cheaper passing one is spelled (D613)."""
    import dataclasses

    from flux_loop import PromptProblem, request_for, run_loop

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    main(["new", "sq", "--kind", "rtl", "--dir", str(tmp_path / "p")])
    doc = tmp_path / "p" / "sq" / "problem.yaml"
    (tmp_path / "p" / "sq" / "golden.py").write_text(
        "PORTS = [{'name': 'a', 'dir': 'in', 'bits': 8, 'unsigned': True},\n"
        "         {'name': 'y', 'dir': 'out', 'bits': 12, 'unsigned': True}]\n"
        "COUNT = 64\n\ndef golden(a):\n    return {'y': (a * a) >> 4}\n")
    d = yaml.safe_load(doc.read_text())
    d["statement"] = "module `sq`: y = (a*a) >> 4"
    d["contract"] = "module sq, input a (8 bits), output y (12 bits)"
    d["objectives"] = [{"metric": "area_um2", "direction": "minimize"}]
    d["flow"]["measure"] = {"screen": {"command": "{python} -c \"print('area_um2=' + str(len(open('{artifact}').read())))\"",
                                       "metrics": ["area_um2"]}}
    d["budget"].update(prototype=True, steps=2, prototype_table_max=256)   # the table is the costly one
    doc.write_text(yaml.safe_dump(d, sort_keys=False))
    task = load_task(doc)
    costly = json.dumps({"prototype": "T = [(i * i) >> 4 for i in range(256)]\ndef design(a):\n    return {'y': T[a]}\n"})
    cheap = json.dumps({"prototype": "def design(a):\n    return {'y': (a * a) >> 4}\n", "why": "REWRITE: arithmetic, no table"})
    db = str(tmp_path / "d.db")
    first = run_loop(PromptProblem(task), request_for(task, db=db), proposer=ScriptedProposer([costly]), log=lambda _m: None)
    assert first.decision is not None and "T_rom" in first.decision.candidate.artifact
    model = ScriptedProposer([cheap])
    said: list[str] = []
    second = run_loop(PromptProblem(task), dataclasses.replace(request_for(task, db=db), explore=1), proposer=model,
                      log=said.append)
    assert any("cost pass sq" in m for m in said), said[-30:]
    assert "Cheaper, every input still passing" in model.prompts[0] and "the table `T` (256 x 12 bits)" in model.prompts[0]
    assert second.decision is not None and "T_rom" not in second.decision.candidate.artifact


def _sq_doc(tmp_path, measures=True, **budget):
    main(["new", "sq", "--kind", "rtl", "--dir", str(tmp_path / "p")])
    doc = tmp_path / "p" / "sq" / "problem.yaml"
    (tmp_path / "p" / "sq" / "golden.py").write_text(
        "PORTS = [{'name': 'a', 'dir': 'in', 'bits': 8, 'unsigned': True},\n"
        "         {'name': 'y', 'dir': 'out', 'bits': 12, 'unsigned': True}]\n"
        "COUNT = 64\n\ndef golden(a):\n    return {'y': (a * a) >> 4}\n")
    ran = tmp_path / "stage-ran"
    d = yaml.safe_load(doc.read_text())
    d["statement"], d["contract"] = "module `sq`: y = (a*a) >> 4", "module sq, input a (8 bits), output y (12 bits)"
    d["objectives"] = [{"metric": "area_um2", "direction": "minimize"}]
    screen = {"metrics": ["area_um2"],
              "command": "{python} -c \"open('" + str(ran) + "', 'a').write('x'); print('area_um2=' + str(len(open('{artifact}').read())))\""}
    if not measures:                      # a stage that ran and reported nothing, as synthesis at its time cap
        screen["command"] = screen["command"].replace("print('area_um2=' + ", "print('no metric ' + ")
    d["flow"]["measure"] = {"screen": screen}
    d["budget"].update(prototype=True, steps=2, prototype_table_max=256, **budget)
    doc.write_text(yaml.safe_dump(d, sort_keys=False))
    return load_task(doc), ran


COSTLY = json.dumps({"prototype": "T = [(i * i) >> 4 for i in range(256)]\ndef design(a):\n    return {'y': T[a]}\n"})
CHEAP = json.dumps({"prototype": "def design(a):\n    return {'y': (a * a) >> 4}\n", "why": "REWRITE: arithmetic, no table"})


def test_a_prototype_over_the_ceiling_is_made_cheaper_before_anything_is_built(tmp_path, monkeypatch):
    """A verified prototype over the cost ceiling gets a cost pass before spelling (D615)."""
    from flux_loop import PromptProblem, request_for, run_loop

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    task, _ran = _sq_doc(tmp_path, prototype_cost_max=120)      # the table costs 154, the multiplier 81
    model, said = ScriptedProposer([COSTLY, CHEAP]), []
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "d.db")), proposer=model, log=said.append)
    assert any("before any RTL is spelled" in m for m in said), said[-20:]
    assert out.decision is not None and "T_rom" not in out.decision.candidate.artifact
    assert "Cheaper, every input still passing" in model.prompts[1]


def test_a_prototype_over_the_ceiling_is_never_built_nor_measured(tmp_path, monkeypatch):
    """Over `prototype_cost_max`, the part stops before spelling and no stage runs (D615)."""
    from flux_loop import PromptProblem, request_for, run_loop

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    task, ran = _sq_doc(tmp_path, prototype_cost_max=10, prototype_shrink_attempts=0)
    prob = PromptProblem(task)
    assert prob.cost_ceiling() == 10
    out = run_loop(prob, request_for(task, db=str(tmp_path / "d.db")), proposer=ScriptedProposer([COSTLY]), log=lambda _m: None)
    assert out.decision is None and not ran.exists()
    assert any("over the ceiling of 10" in why for _n, why in out.refused), out.refused


def test_the_default_ceiling_is_the_calibrated_constant(tmp_path):
    from flux_loop import PromptProblem

    task, _ = _sq_doc(tmp_path)
    assert PromptProblem(task).cost_ceiling() == 2000
    task2, _ = _sq_doc(tmp_path / "b", prototype_cost_max=-1)
    assert PromptProblem(task2).cost_ceiling() is None


def test_a_spelled_design_on_record_is_not_synthesised_again_once_over_the_ceiling(tmp_path, monkeypatch):
    """The spelled row names its prototype by digest, so a reload finds its cost for the ceiling (D615)."""
    import time

    from flux_loop import PromptProblem, request_for, run_loop
    from flux_loop.records import _reload
    from flux_loop.types import LoopState

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    task, _ran = _sq_doc(tmp_path, prototype_cost_max=120)      # the table, then the multiplier: two
    db = str(tmp_path / "d.db")                                   # verified prototypes on record
    first = run_loop(PromptProblem(task), request_for(task, db=db), proposer=ScriptedProposer([COSTLY, CHEAP]),
                     log=lambda _m: None)
    spelled = first.decision.candidate
    assert spelled.meta.get("prototype_sha")
    tight, _ = _sq_doc(tmp_path, prototype_cost_max=10, prototype_shrink_attempts=0)
    prob, req = PromptProblem(tight), request_for(tight, db=db)
    said: list[str] = []
    state = LoopState(request=req, say=said.append, proposer=None, feedback=None, started=time.monotonic(), depth=0)
    (tmp_path / "work").mkdir()
    state.workdir = str(tmp_path / "work")              # the stage writes its artifact there, not in the cwd
    state.records = prob.open_records(req, said.append)
    _reload(prob, state, [])
    assert "*" in state.admitted and "T[a]" not in state.prototypes.get("*", "T[a]"), said
    assert prob.measure(state.admitted["*"], "screen", state) == {"error": prob._over_ceiling(state.admitted["*"], state)}
    assert "over the ceiling of 10" in prob._over_ceiling(state.admitted["*"], state)


def test_the_failures_are_shown_where_they_are_not_only_the_first_ones(tmp_path):
    """Failures are grouped by the input's leading bits (sign and exponent for FP16), one example per run of groups (D617)."""
    (tmp_path / "golden.py").write_text(
        "import numpy as np\n"
        "PORTS = [{'name': 'x', 'dir': 'in', 'bits': 16, 'unsigned': True},\n"
        "         {'name': 'y', 'dir': 'out', 'bits': 16, 'unsigned': True}]\n"
        "COUNT = 64\nTOLERANCE_ULP = {'y': 1}\n\n"
        "def golden(x):\n    return {'y': x}\n")
    g = load(tmp_path / "golden.py")
    rows = exhaustive(g)
    halves = "def design(x):\n    return {'y': x if (x >> 10) & 31 < 15 else 0}\n"   # wrong from 1.0 up, both signs
    v = check(halves, g, rows)
    assert not v.ok and "WHERE they fail" in v.why and "the sign, then the exponent" in v.why
    assert "x[15:10] = 0xf..0x1f: ALL wrong" in v.why and "x[15:10] = 0x2f..0x3f: ALL wrong" in v.why
    assert "0x0..0xe" not in v.why                     # the passing range is not listed


AGENT = '''
import sys
brief = open(sys.argv[1]).read()
out = sys.argv[2]
assert "design(" in brief and "rtl proto" not in brief, brief[-800:]   # D673: it writes, the loop runs
open(out, "w").write("def design(a):\\n    return {'y': (a * a) >> 4}\\n")
print("written")
'''


def test_a_coding_agent_writes_the_prototype_and_the_loop_checks_it(tmp_path, monkeypatch):
    """With `flow.generate: {by: ...}` and `prototype: true`, the agent writes the prototype, the loop
    checks it (D673) and spells the RTL (D618)."""
    from flux_loop import PromptProblem, request_for, run_loop

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    _sq_doc(tmp_path)
    doc = tmp_path / "p" / "sq" / "problem.yaml"
    d = yaml.safe_load(doc.read_text())
    fake = tmp_path / "agent.py"
    fake.write_text(AGENT)
    d.setdefault("flow", {})["generate"] = {"by": {"command": ["{python}", str(fake), "{prompt_file}", "{artifact}"]}}
    doc.write_text(yaml.safe_dump(d, sort_keys=False))
    task = load_task(doc)
    said: list[str] = []
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "d.db")), proposer=ScriptedProposer([]),
                   log=said.append)
    assert out.decision is not None and out.decision.candidate.knobs.get("generator") == "py2sv", (said[-12:], out.refused)


def test_the_documents_knowledge_reaches_the_prototype_stage(tmp_path):
    """`knowledge:` reaches the prototype stage's prompt too (D618)."""
    from flux_loop import PromptProblem
    from flux_loop.prototype import prefix_for
    from flux_loop.types import LoopRequest, LoopState

    task, _ = _sq_doc(tmp_path)
    task = TaskSpec.from_dict({**task.to_dict(), "flow": {**task.to_dict().get("flow", {}), "knowledge": {"text": "SQUARE BY SHIFT-AND-ADD"}}}, base=tmp_path / "p" / "sq")
    prob = PromptProblem(task)
    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None)
    assert "SQUARE BY SHIFT-AND-ADD" in prefix_for(prob, prob.prototype(), None, state)


def test_a_prototype_the_loop_cannot_spell_is_told_at_the_check(tmp_path):
    """A prototype using a construct the spelling lacks is not verified, and the check names it (D618)."""
    (tmp_path / "golden.py").write_text(
        "PORTS = [{'name': 'a', 'dir': 'in', 'bits': 8, 'unsigned': True},\n"
        "         {'name': 'y', 'dir': 'out', 'bits': 8, 'unsigned': True}]\n"
        "COUNT = 16\n\ndef golden(a):\n    return {'y': a}\n")
    g = load(tmp_path / "golden.py")
    rows = exhaustive(g)
    looping = "def design(a):\n    y = a\n    while False:\n        y += 1\n    return {'y': y}\n"
    v = check(looping, g, rows)
    assert not v.ok and "cannot spell it" in v.why and "While" in v.why
    assert check("def design(a):\n    return {'y': a}\n", g, rows).ok


RESUMING = '''
import json, sys
from pathlib import Path
here = Path(sys.argv[0]).parent
mode = sys.argv[1]
if mode == "first":
    sid, out, shift = "ses_p1", sys.argv[3], 3             # a wrong first prototype
else:
    sid, out, shift = sys.argv[2], sys.argv[4], 4
    assert "REFUSED" in sys.argv[3] and "HOW TO ANSWER" not in sys.argv[3], sys.argv[3][:400]
open(out, "w").write(f"def design(a):\\n    return {{'y': (a * a) >> {shift}}}\\n")
with open(here / "turns.txt", "a") as f:
    f.write(f"{mode} {sid} {Path.cwd()}\\n")
print(json.dumps({"type": "text", "sessionID": sid, "part": {"text": "written"}}))
'''


def test_the_prototype_agent_is_resumed_until_its_prototype_passes(tmp_path, monkeypatch):
    """The prototype agent's refused prototype goes back into the same session with the check's
    report (D669); the prototype that passes ends the session."""
    from flux_loop import PromptProblem, request_for, run_loop

    monkeypatch.setenv("FLUX_TRACE_ROOT", str(tmp_path / "traces"))
    _sq_doc(tmp_path)
    doc = tmp_path / "p" / "sq" / "problem.yaml"
    d = yaml.safe_load(doc.read_text())
    fake = tmp_path / "agent.py"
    fake.write_text(RESUMING)
    d.setdefault("flow", {})["generate"] = {"by": {
        "command": ["{python}", str(fake), "first", "{prompt_file}", "{artifact}"],
        "resume": ["{python}", str(fake), "resume", "{session}", "{answer}", "{artifact}"], "output": "opencode"}}
    doc.write_text(yaml.safe_dump(d, sort_keys=False))
    task = load_task(doc)
    said: list[str] = []
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "d.db")), proposer=ScriptedProposer([]),
                   log=said.append)
    assert out.decision is not None, (said[-12:], out.refused)
    turns = [ln.split() for ln in (tmp_path / "turns.txt").read_text().splitlines()]
    assert [t[0] for t in turns] == ["first", "resume"] and turns[0][1:] == turns[1][1:], turns
    assert Path(turns[0][2]).parent.name == "prototype"
    assert any("prototype" in m and "resumed session ses_p1" in m for m in said), said
