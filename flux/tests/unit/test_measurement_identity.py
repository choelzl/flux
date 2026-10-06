"""D898: one evidence identity. An external review reproduced three ways a resumed loop reused a
number its measurement no longer produced, each against a fresh-record control: the same artifact
measured by a command reading a different `{x}` (got 1, fresh 2); a tool in the stage's `needs`
upgraded (got 10, fresh 100); a metric regex changed (got 10, fresh 100). `problem.cache_key` now
holds the command-visible knobs, the metric regexes and the builds of the stage's tools, and it is
both the disk cache's key and every row's `measured_as` -- so the cache and `records.fresh` agree."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from flux_evaluator_abi.toolchain import tool_fingerprint
from flux_loop import PromptProblem, TaskSpec, request_for, run_loop
from flux_loop.types import Candidate, LoopRequest, LoopState


def _doc(home: Path) -> dict:
    (home / "out").mkdir(parents=True, exist_ok=True)
    (home / "gen.py").write_text("import sys; open(sys.argv[1], 'w').write('x=' + sys.argv[2])")
    return {"id": "ident", "statement": "the smallest time", "language": "text",
            "flow": {"orchestrate": {"policy": "sweep", "space": {"x": [1, 2]}},
                     "generate": {"command": "{python} {home}/gen.py {artifact} {x}"},
                     "test": {"test": ["true"]},
                     "measure": {"bench": {"command": "{python} {home}/bench.py {artifact}", "metrics": ["time_ms"]}}},
            "objectives": [{"metric": "time_ms", "direction": "minimize"}], "budget": {"steps": 2, "batch": 10}}


def _run(home: Path, doc: dict, record: str = "ident") -> dict[str, float]:
    """Each point's time on this pass, by name."""
    task = TaskSpec.from_dict(doc, base=home)
    out = run_loop(PromptProblem(task), request_for(task, db=str(home / "out" / f"{record}.db")), proposer=None,
                   log=lambda _m: None)
    return {s.candidate.name: s.metrics["time_ms"] for s in out.scored if "time_ms" in s.metrics}


def test_the_same_artifact_measured_with_another_x_is_measured_again(tmp_path):
    """The command reads `{x}`, the artifact does not change: x=2 got x=1's number."""
    doc = _doc(tmp_path)
    (tmp_path / "gen.py").write_text("import sys; open(sys.argv[1], 'w').write('identical artifact')")
    (tmp_path / "bench.py").write_text("import sys; print('time_ms=' + sys.argv[1])")
    doc["flow"]["measure"]["bench"]["command"] = "{python} {home}/bench.py {x}"
    doc["flow"]["orchestrate"]["space"]["x"] = [1]
    assert _run(tmp_path, doc) == {"x=1": 1.0}
    doc["flow"]["orchestrate"]["space"]["x"] = [2]
    again = _run(tmp_path, doc)
    fresh = _run(tmp_path, doc, "fresh")
    assert fresh == {"x=2": 2.0}
    assert again["x=2"] == 2.0 and again.get("x=1") == 1.0, again      # x=1's own row still stands for x=1


def test_a_needed_tool_upgraded_measures_again(tmp_path, monkeypatch):
    """`needs: [reviewbench]` goes from version one to two: the record's rows are stale. The tool lives
    outside the loop's folder, so the loop's inputs digest does not see it change."""
    home = tmp_path / "home"
    doc = _doc(home)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "reviewbench"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    def tool(version: str, value: int) -> None:
        exe.write_text(f"#!{sys.executable}\nimport sys\n"
                       f"print('version {version}' if '--version' in sys.argv else 'time_ms={value}')\n")
        exe.chmod(0o755)
        tool_fingerprint.cache_clear()

    doc["flow"]["measure"]["bench"] = {"command": "reviewbench {artifact}", "metrics": ["time_ms"], "needs": ["reviewbench"]}
    try:
        tool("one", 10)
        assert set(_run(home, doc).values()) == {10.0}
        assert set(_run(home, doc).values()) == {10.0}                    # unchanged: the rows stand
        tool("two", 100)
        again = _run(home, doc)
        assert set(_run(home, doc, "fresh").values()) == {100.0}
        assert set(again.values()) == {100.0}, again
    finally:
        tool_fingerprint.cache_clear()


def test_a_changed_metric_regex_measures_again(tmp_path):
    """The output says `baseline=10 measured=100`; the stage's regex moves from one to the other."""
    doc = _doc(tmp_path)
    (tmp_path / "bench.py").write_text("print('baseline=10 measured=100')")
    stage = doc["flow"]["measure"]["bench"]
    stage["metrics_re"] = {"time_ms": r"baseline=(\d+)"}
    assert set(_run(tmp_path, doc).values()) == {10.0}
    stage["metrics_re"] = {"time_ms": r"measured=(\d+)"}
    again = _run(tmp_path, doc)
    assert set(_run(tmp_path, doc, "fresh").values()) == {100.0}
    assert set(again.values()) == {100.0}, again


def test_the_key_is_the_measurement_not_the_design(tmp_path):
    """Content dedup stays `Candidate.key`: knobs the command does not read leave the key alone, and
    an artifact-less candidate is its knobs already."""
    doc = _doc(tmp_path)
    (tmp_path / "bench.py").write_text("print('time_ms=1')")
    problem = PromptProblem(TaskSpec.from_dict(doc, base=tmp_path))
    state = LoopState(request=LoopRequest(), proposer=None, feedback=None, say=lambda _m: None)
    a, b = Candidate("a", "same", {"x": 1}), Candidate("b", "same", {"x": 2})
    assert a.key() == b.key()
    assert problem.cache_key(a, "bench", state) == problem.cache_key(b, "bench", state)     # {x} not read
    doc["flow"]["measure"]["bench"]["command"] = "{python} {home}/bench.py {artifact} {x}"
    problem = PromptProblem(TaskSpec.from_dict(doc, base=tmp_path))
    assert problem.cache_key(a, "bench", state) != problem.cache_key(b, "bench", state)     # {x} read
    assert a.key() == b.key()
