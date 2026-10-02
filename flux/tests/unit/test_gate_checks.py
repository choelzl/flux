"""A gate as a sequence of named checks (D652), `flux rtl lint` (D653) and the tool catalog (D654)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from flux_loop import BuildError, Candidate, LoopRequest, LoopState, PromptProblem, TaskError, TaskSpec
from flux_loop.document import DEFAULT_COUNT_RE, Check, Gate
from flux_loop.gradient import CHECK_WEIGHT, gate_score

FLUX = Path(__file__).resolve().parents[2]
TOOLS_JSON = FLUX.parent / "website" / "docs" / "assets" / "tools.json"


# ---- the document
def test_a_gate_is_a_map_of_its_checks_in_order():
    """D789: `flow.test` is a map by name, like `flow.measure`."""
    task = TaskSpec.from_dict({"id": "t",
                               "statement": "x",
                               "flow": {"test": {"lint": "flux rtl lint {artifact}",
                                                 "golden": {"run": ["flux", "rtl", "test", "{artifact}", "--golden", "{home}/golden.py"], "timeout_s": 300},
                                                 "suite": {"run": "pytest -q", "fail_re": "FAILED"}}}})
    lint, golden, suite = task.gate
    assert [c.name for c in task.gate] == ["lint", "golden", "suite"]
    assert lint.run[-3:] == ("rtl", "lint", "{artifact}") and lint.count_re == DEFAULT_COUNT_RE
    assert golden.timeout_s == 300 and task.gate.timeout_s == 300
    assert suite.fail_re == "FAILED" and suite.count_re is None and not any(c.builds for c in task.gate)
    again = TaskSpec.from_dict(task.to_dict())
    assert again == task and again.digest == task.digest
    assert list(task.to_dict()["flow"]["test"]) == ["lint", "golden", "suite"], "written back as a map"


def test_a_command_alone_is_the_check_test_and_build_refuses_on_any_exit():
    one = TaskSpec.from_dict({"id": "t", "statement": "x", "flow": {"test": "check {artifact}"}}).gate
    assert one == Gate([Check("test", ("check", "{artifact}"), DEFAULT_COUNT_RE)])
    two = TaskSpec.from_dict({"id": "t",
                              "statement": "x",
                              "flow": {"test": {"build": "make {artifact}",
                                                "test": {"run": "run {artifact}", "fail_re": "BAD", "timeout_s": 9}}}}).gate
    assert [c.name for c in two] == ["build", "test"] and two.named("build").builds and not two.named("test").builds
    assert two.named("build").count_re is None and two.named("test").fail_re == "BAD" and two.named("test").timeout_s == 9
    task = TaskSpec.from_dict({"id": "t", "statement": "x", "flow": {"test": {"build": "make {artifact}", "test": "run {artifact}"}}})
    assert task.to_dict()["flow"]["test"] == {"build": ["make", "{artifact}"], "test": ["run", "{artifact}"]}
    assert TaskSpec.from_dict(task.to_dict()) == task


@pytest.mark.parametrize("gate, message", [
    ({"a": {"timeout_s": 3}}, r"flow.test.a needs `run`"),
    ({"a": {"name": "a", "run": "x"}}, "a check's name is its key"),
    ({"a": {"run": "x", "build": "y"}}, "not a check's"),
    ({"a": {"run": "x", "count_re": "("}}, r"flow.test.a.count_re is not a regex"),
    ({"a": "x {nope}"}, r"gate a says \{nope\}"),
    ({"test": "x", "timeout_s": 60}, "timeout_s is a check's setting, said under its name"),
    ({"9a": "x"}, "a check's name is letters"),
    ([{"name": "a", "run": "x"}], "a command"),
])
def test_a_bad_check_is_named(gate, message):
    with pytest.raises(TaskError, match=message):
        TaskSpec.from_dict({"id": "t", "statement": "x", "flow": {"test": gate}})


# ---- running it
_CHECK = ("import sys, pathlib\n"
          "word, art, log = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])\n"
          "with log.open('a') as f: f.write(word + '\\n')\n"
          "t = art.read_text()\n"
          "if 'SYNTAX' in t: print('does not parse'); sys.exit(3)\n"
          "bad = t.count(word)\n"
          "print(f'{word} x{bad}\\n{bad} failing'); sys.exit(1 if bad else 0)\n")


def _two_checks(tmp_path: Path) -> tuple[PromptProblem, LoopState, Path]:
    (tmp_path / "check.py").write_text(_CHECK)
    log = tmp_path / "ran.log"
    doc = {"id": "d",
           "statement": "text",
           "flow": {"test": {"lint": ["{python}", "{home}/check.py", "LATCH", "{artifact}", str(log)],
                             "golden": ["{python}", "{home}/check.py", "WRONG", "{artifact}", str(log)]}}}
    prob = PromptProblem(TaskSpec.from_dict(doc, base=tmp_path))
    state = LoopState(request=LoopRequest(db=""), say=lambda _m: None, proposer=None, feedback=None, workdir=str(tmp_path))
    return prob, state, log


def _verdict(prob, state, cand):
    return prob.judge(prob.build(cand, None, state), cand, None, state)


def test_the_first_check_that_fails_refuses_and_the_rest_do_not_run(tmp_path):
    prob, state, log = _two_checks(tmp_path)
    v = _verdict(prob, state, Candidate("a", "LATCH LATCH WRONG"))
    assert not v.ok and v.why.startswith("failed at lint: ") and "2 failing" in v.why
    assert log.read_text().split() == ["LATCH"], "golden never ran, and the checks ran once"
    log.unlink()
    v2 = _verdict(prob, state, Candidate("b", "fine WRONG WRONG WRONG"))
    assert not v2.ok and v2.why.startswith("failed at golden: ") and "3 failing" in v2.why
    assert log.read_text().split() == ["LATCH", "WRONG"]
    assert v.score > v2.score, "a design stopped at lint ranks worse than one stopped at golden"
    assert v.score == 2 + CHECK_WEIGHT and gate_score(v.score) == "2 (with 1 later check not reached)"
    ok = _verdict(prob, state, Candidate("c", "fine"))
    assert ok.ok and ok.score == 0


def test_exit_3_at_any_check_is_did_not_build(tmp_path):
    prob, state, log = _two_checks(tmp_path)
    with pytest.raises(BuildError, match="did not build at lint: does not parse"):
        prob.build(Candidate("s", "SYNTAX"), None, state)
    assert log.read_text().split() == ["LATCH"]


def test_task_check_lists_the_checks_with_their_pass_rule(tmp_path, capsys):
    from flux_cli.main import main

    (tmp_path / "check.py").write_text(_CHECK)
    (tmp_path / "t.problem.yaml").write_text(
        "statement: text\nflow:\n  test:\n"
        "    lint: '{python} {home}/check.py LATCH {artifact} log'\n"
        "    golden: {run: '{python} {home}/check.py WRONG {artifact} log', fail_re: 'x[1-9]'}\n")
    main(["task", "check", str(tmp_path / "t.problem.yaml")])
    out = capsys.readouterr().out
    assert "gate: 2 check(s) in order" in out
    assert out.index("1. lint: ") < out.index("2. golden: ")
    assert "passes when `(\\d+) failing` reads 0; exit 3 = did not build" in out
    assert "passes when no line matches `x[1-9]`" in out


# ---- flux rtl lint
_LATCH = """module latchy(input logic en, input logic [3:0] d, output logic [3:0] q);
  always_comb begin
    if (en) q = d;
  end
endmodule
"""
_CLEAN = """module clean(input logic en, input logic [3:0] d, output logic [3:0] q);
  always_comb q = en ? d : 4'd0;
endmodule
"""


def _lint(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "flux_cli.main", "rtl", "lint", str(path)],
                          capture_output=True, text=True, timeout=120)


@pytest.mark.skipif(shutil.which("verilator") is None, reason="needs verilator")
def test_rtl_lint_counts_a_latch_and_passes_a_clean_module(tmp_path):
    (tmp_path / "l.sv").write_text(_LATCH)
    (tmp_path / "c.sv").write_text(_CLEAN)
    (tmp_path / "s.sv").write_text("module s(input a, output y)\n assign y = a;\nendmodule\n")
    bad, good, broken = _lint(tmp_path / "l.sv"), _lint(tmp_path / "c.sv"), _lint(tmp_path / "s.sv")
    assert bad.returncode == 1 and "LATCH: line 2:" in bad.stdout and bad.stdout.rstrip().endswith("1 failing")
    assert good.returncode == 0 and good.stdout.strip() == "0 failing"
    assert broken.returncode == 3 and "did not parse" in broken.stdout


# ---- the catalog
def test_the_crafters_tools_json_is_flux_tools_json(capsys):
    from flux_cli.main import main

    assert main(["tools", "--json"]) == 0
    out = capsys.readouterr().out
    assert TOOLS_JSON.read_text() == out, "regenerate: flux tools --json > website/docs/assets/tools.json"


def test_flux_tools_lists_checks_then_stages(capsys):
    from flux_cli.main import main
    from flux_loop.toolbox import TOOLS, fill

    assert main(["tools"]) == 0
    out = capsys.readouterr().out
    assert out.index("CHECKS") < out.index("rtl-lint:") < out.index("STAGES") < out.index("rtl-synth:")
    assert "gate: passes with no defect" in out and "metrics: fmax_mhz (MHz)" in out
    ids = {t["id"] for t in TOOLS}
    assert {"rtl-lint", "rtl-golden", "rtl-synth", "rtl-place", "rtl-route", "champsim-build", "champsim-check",
            "champsim-run", "python-test-script", "bench-script", "zigzag-model", "custom-check", "custom-stage"} <= ids
    assert all(("pass" in t) == (t["role"] == "check") and ("metrics" in t) == (t["role"] == "stage") for t in TOOLS)
    assert fill("rtl-synth", clock_ps=300) == "flux rtl measure {artifact} --stage synth --clock-ps 300"


def test_every_catalog_command_loads_as_a_document(tmp_path):
    """What the crafter writes from the catalog is a document Flux accepts."""
    from flux_loop.toolbox import TOOLS, fill

    for t in TOOLS:
        if t["id"].startswith("custom"):
            continue
        if "stage" in t:                  # an evaluator stage (D663): its keys, and the document's
            defaults = {k: str(p["default"]) for k, p in t["params"].items()}
            top = {k: re.sub(r"\{(\w+)\}", lambda m: defaults.get(m.group(1), m.group(0)), v) for k, v in t["document"].items()}
            TaskSpec.from_dict({"id": "t", "statement": "x", **top,
                                "flow": {"test": "true", "measure": {"s": {**t["stage"], "metrics": list(t["metrics"])}}}})
            continue
        run = fill(t["id"])
        if t["role"] == "check":
            flow = {"test": {"c": run}}
        else:
            flow = {"test": "true", "measure": {"s": {"command": run, "metrics": list(t["metrics"])}}}
        TaskSpec.from_dict({"id": "t", "statement": "x", "flow": flow})
    json.dumps(TOOLS)
