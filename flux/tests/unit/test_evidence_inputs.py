"""D853: evidence holds only for what made it. An external review reproduced three ways a resumed
loop trusted numbers or admissions its inputs no longer supported: a sweep that changed its
measuring script kept the old numbers; a checker whose imported helper changed kept its admission;
a workload rewritten under the same name was a cache hit. The loop's inputs (its files beside the
document, and its params) now key admissions and measurements alike."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flux_loop import LoopRequest, PromptProblem, TaskSpec, request_for, run_loop
from flux_loop.inputs import BIG, digest


def _sweep(tmp_path: Path, factor: int) -> dict:
    (tmp_path / "gen.py").write_text("import sys; open(sys.argv[1], 'w').write('x=' + sys.argv[2])")
    (tmp_path / "bench.py").write_text(
        f"import sys; x = int(open(sys.argv[1]).read().split('=')[1]); print(f'time_ms={{{factor} * x}}')")
    return {"id": "sw", "statement": "the smallest x", "language": "text",
            "flow": {"orchestrate": {"policy": "sweep", "space": {"x": [1, 2]}},
                     "generate": {"command": f"{{python}} {tmp_path}/gen.py {{artifact}} {{x}}"},
                     "test": {"test": ["true"]},
                     "measure": {"bench": {"command": "{python} {home}/bench.py {artifact}", "metrics": ["time_ms"]}}},
            "objectives": [{"metric": "time_ms", "direction": "minimize"}], "budget": {"steps": 2, "batch": 10}}


def _run(tmp_path: Path, doc: dict, say=None):
    task = TaskSpec.from_dict(doc, base=tmp_path)
    return run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "out" / "sw.db")), proposer=None,
                    log=(say.append if say is not None else (lambda _m: None)))


def test_a_resumed_sweep_measures_again_what_a_changed_script_measures(tmp_path):
    """The review's reproduction (#5): `time_ms = 10 * x`, then the script says `100 * x` -- the resumed
    sweep proposed nothing and kept 10. Now its points are measured again and decide on 100."""
    (tmp_path / "out").mkdir()
    first = _run(tmp_path, _sweep(tmp_path, 10))
    assert first.decision.metrics["time_ms"] == 10.0
    said: list[str] = []
    again = _run(tmp_path, _sweep(tmp_path, 100), said)
    assert again.decision is not None and again.decision.metrics["time_ms"] == 100.0, again.decision
    assert any("measured under other inputs" in m for m in said), said
    said = []
    same = _run(tmp_path, _sweep(tmp_path, 100), said)        # nothing changed: the record's rows stand
    assert same.decision.metrics["time_ms"] == 100.0
    assert not any("measured under other inputs" in m for m in said) and any("rejoin the search" in m for m in said)


def test_rows_from_before_carry_no_key_and_are_measured_again(tmp_path):
    """Records made before D853 have no `measured_as` on their rows: measured again once."""
    import sqlite3

    (tmp_path / "out").mkdir()
    _run(tmp_path, _sweep(tmp_path, 10))
    con = sqlite3.connect(tmp_path / "out" / "sw.db")
    for rid, cj in con.execute("SELECT id, candidate_json FROM trials").fetchall():
        doc = json.loads(cj)
        ((doc.get("meta") or {}).get("provenance") or {}).pop("measured_as", None)
        con.execute("UPDATE trials SET candidate_json = ? WHERE id = ?", (json.dumps(doc), rid))
    con.commit()
    con.close()
    said: list[str] = []
    _run(tmp_path, _sweep(tmp_path, 10), said)
    assert any("measured under other inputs (or before these were recorded)" in m for m in said), said


def test_a_checker_whose_helper_changed_rechecks_its_admission(tmp_path):
    """The review's reproduction (#6): `check.py` imports FAIL from `helper.py`; FAIL turned True after
    admission, the reload kept the design frozen. Now the judge's fingerprint holds the loop's inputs
    and params, so a changed helper -- or a changed param -- re-verifies it."""
    from flux_llm import ScriptedProposer

    (tmp_path / "helper.py").write_text("FAIL = False\n")
    (tmp_path / "check.py").write_text("import sys\nsys.path.insert(0, sys.argv[2])\nfrom helper import FAIL\n"
                                       "print(f'{int(FAIL)} failing')\n")
    doc = {"id": "j", "statement": "anything", "language": "text", "budget": {"steps": 1, "prototype": False},
           "params": {"n": 1}, "flow": {"test": "{python} {home}/check.py {artifact} {home}", "knowledge": "off"}}
    good = json.dumps({"artifact": "x\n", "why": "w"})
    db = str(tmp_path / "out" / "j.db")
    (tmp_path / "out").mkdir()

    def run(d):
        said: list[str] = []
        run_loop(PromptProblem(TaskSpec.from_dict(d, base=tmp_path)), LoopRequest(db=db, steps=1, prototype=False),
                 proposer=ScriptedProposer([good] * 6), log=said.append)
        return said

    run(doc)
    assert any("kept frozen (its row was made by today's" in m for m in run(doc)), "nothing changed: kept"
    (tmp_path / "helper.py").write_text("FAIL = True\n")
    said = run(doc)
    assert any("re-verif" in m for m in said) and not any("kept frozen (its row was made by today's" in m for m in said), said
    (tmp_path / "helper.py").write_text("FAIL = False\n")
    run(doc)
    said = run({**doc, "params": {"n": 2}})                  # a gate can read {params}: they are in the judge too
    assert any("re-verif" in m for m in said), said


def test_a_workloads_content_is_in_the_measurement_key(tmp_path):
    """The review's reproduction (#7): a workload rewritten under the same name was a cache hit. A
    file beside the document is among the loop's inputs (D853), so it counts without `workload:`."""
    from flux_loop.types import Candidate

    (tmp_path / "w.yaml").write_text("n: 1\n")
    doc = {"id": "wk", "statement": "s", "language": "text",
           "flow": {"test": {"test": ["true"]}, "measure": {"m": {"command": "true", "metrics": ["cycles"]}}},
           "objectives": [{"metric": "cycles", "direction": "minimize"}]}
    problem = PromptProblem(TaskSpec.from_dict(doc, base=tmp_path))
    cand = Candidate("a", "x")
    k1 = problem.cache_key(cand, "m", None)
    assert problem.cache_key(cand, "m", None) == k1, "unchanged: reusable"
    (tmp_path / "w.yaml").write_text("n: 2\n")
    problem2 = PromptProblem(TaskSpec.from_dict(doc, base=tmp_path))
    assert problem2.cache_key(cand, "m", None) != k1


def test_the_inputs_fingerprint(tmp_path):
    """What counts: files beside the document and params -- not the document, a record's files, out/,
    runs/, workbench/, library/, hidden or cache files. A big file by its size, time and two ends."""
    (tmp_path / "problem.yaml").write_text("statement: s\n")
    (tmp_path / "check.py").write_text("print(0)\n")
    base = digest(tmp_path, {"a": 1})
    assert base and digest(tmp_path, {"a": 1}) == base
    assert digest(tmp_path, {"a": 2}) != base, "params count"
    (tmp_path / "problem.yaml").write_text("statement: s\nobjectives: [time_ms]\n")
    for folder in ("out", "runs", "workbench", "library", "__pycache__"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "f.txt").write_text("x")
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "e.db").write_text("x")
    (tmp_path / "e.e.json").write_text("{}")          # the record's measurement cache beside it
    assert digest(tmp_path, {"a": 1}) == base, "none of these is an input"
    (tmp_path / "check.py").write_text("print(1)\n")
    assert digest(tmp_path, {"a": 1}) != base, "an input changed"
    big = tmp_path / "trace.bin"
    big.write_bytes(b"\0" * (BIG + 10))
    d1 = digest(tmp_path)
    with open(big, "r+b") as fh:                      # the middle changed, size and time kept: unseen (a trade-off)
        st = os.stat(big)
        fh.seek(BIG // 2)
        fh.write(b"\1")
    os.utime(big, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert digest(tmp_path) == d1
    with open(big, "r+b") as fh:                      # its first bytes changed: seen
        fh.write(b"\2")
    os.utime(big, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert digest(tmp_path) != d1
