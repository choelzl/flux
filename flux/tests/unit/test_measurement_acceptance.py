"""D897: a measurement is accepted only when it succeeded. An external review reproduced a bench that
printed `time_ms=1` and exited 9 becoming the loop's decision, and a MacArray result whose independent
checker said `validity.ok=False` being scored. A command's exit status and an evaluator's validity are
now enforced before numbers are taken; a refused measurement keeps its reason on the record and never
reaches the cache or the frontier. The background (ahead) path uses the same canonical cache key and
the same successful-result insertion as an ordinary miss."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from flux_loop import Candidate, LoopRequest, LoopState, Problem, PromptProblem, TaskSpec, request_for, run_loop
from flux_loop.measure import Ahead, cached_measure, measure_many, measurement_key


def _doc(tmp_path: Path, bench: str) -> dict:
    (tmp_path / "gen.py").write_text("import sys; open(sys.argv[1], 'w').write('x=' + sys.argv[2])")
    (tmp_path / "bench.py").write_text(bench)
    return {"id": "acc", "statement": "the smallest time", "language": "text",
            "flow": {"orchestrate": {"policy": "sweep", "space": {"x": [1, 2]}},
                     "generate": {"command": "{python} {home}/gen.py {artifact} {x}"},
                     "test": {"test": ["true"]},
                     "measure": {"bench": {"command": "{python} {home}/bench.py {artifact}", "metrics": ["time_ms", "area"]}}},
            "objectives": [{"metric": "time_ms", "direction": "minimize"}], "budget": {"steps": 2, "batch": 10}}


def _run(tmp_path: Path, doc: dict):
    (tmp_path / "out").mkdir(exist_ok=True)
    task = TaskSpec.from_dict(doc, base=tmp_path)
    return run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "out" / "acc.db")), proposer=None,
                    log=lambda _m: None)


def _rows(tmp_path: Path) -> list[tuple[str, str | None, str | None]]:
    con = sqlite3.connect(tmp_path / "out" / "acc.db")
    try:
        return [(s, e, r) for s, e, r in con.execute("SELECT stage, error, result_id FROM trials WHERE stage = 'bench'")]
    finally:
        con.close()


def _cache(tmp_path: Path) -> dict:
    """The measurement cache's entries for the bench stage, whichever file beside the record holds them."""
    held: dict = {}
    for f in (tmp_path / "out").rglob("*.json"):
        try:
            got = json.loads(f.read_text())
        except ValueError:
            continue
        if isinstance(got, dict):
            held.update({k: v for k, v in got.items() if "/bench/" in k})
    return held


def test_a_bench_that_printed_numbers_and_exited_9_is_refused_not_decided(tmp_path):
    """The review's reproduction: `time_ms=1`, exit 9. No decision, the reason on the record, nothing cached."""
    out = _run(tmp_path, _doc(tmp_path, "import sys; print('time_ms=1'); sys.exit(9)"))
    assert out.decision is None and not out.frontier, out.decision
    assert any("exited 9" in why for _n, why in out.refused), out.refused
    rows = _rows(tmp_path)
    assert rows and all(err and "exited 9" in err and res is None for _s, err, res in rows), rows
    assert not _cache(tmp_path)


def test_partial_metrics_then_failure_are_refused_and_a_clean_exit_is_the_control(tmp_path):
    """A tool that printed one metric and then crashed measured nothing; the same output with exit 0 decides."""
    crash = "import sys; print('time_ms=3'); sys.stdout.flush(); raise SystemExit('placement crashed')"
    out = _run(tmp_path, _doc(tmp_path, crash))
    assert out.decision is None and any("placement crashed" in why for _n, why in out.refused), out.refused
    control = tmp_path / "control"
    control.mkdir()
    ok = _run(control, _doc(control, "print('time_ms=3')"))
    assert ok.decision is not None and ok.decision.metrics["time_ms"] == 3.0


def test_an_evaluator_stage_is_refused_and_the_migration_says_what_to_do(tmp_path):
    """D954: no evaluator stages -- a document naming one is refused with what to use instead, and
    the migration drops what only they read (`workload:`, a mined `calibration`) and hands the stage
    to a person."""
    import pytest

    from flux_loop.document import TaskError
    from flux_loop.migrate import migrate

    doc = {"id": "bad", "statement": "valid latency", "language": "text", "workload": "{home}/w.yaml",
           "flow": {"test": ["true"], "measure": {"bench": {"evaluator": "rtl", "metrics": ["latency_cycles"]}},
                    "knowledge": {"mined": {"calibration": "c.db"}}},
           "objectives": [{"metric": "latency_cycles", "direction": "minimize"}]}
    new, said, manual = migrate(doc)
    assert "workload" not in new and "calibration" not in new["flow"]["knowledge"]["mined"]
    assert any(x.startswith("D954: workload") for x in said) and any("mined.calibration" in x for x in said)
    assert manual and "flow.measure.bench.evaluator" in manual[0] and "a command" in manual[0]
    plain = {k: v for k, v in doc.items() if k != "workload"}
    plain["flow"] = {k: v for k, v in doc["flow"].items() if k != "knowledge"}
    with pytest.raises(TaskError, match="evaluator stages are gone"):
        TaskSpec.from_dict(plain, base=tmp_path)


class Keyed(Problem):
    """A problem whose measuring context extends the candidate's key, as document-backed ones do."""

    name = "keyed"

    def __init__(self, result=None):
        self.calls = 0
        self.result = result or {"time_ms": 1.0}

    def cache_key(self, cand, stage, state):
        return cand.key() + "@context"

    def measure(self, cand, stage, state):
        self.calls += 1
        return dict(self.result)


class Cache:
    def __init__(self):
        self.data = {}

    def holds(self, key):
        return key in self.data

    def get(self, key):
        return self.data[key]

    def put(self, key, value):
        self.data[key] = value


def _state() -> LoopState:
    state = LoopState(request=LoopRequest(), proposer=None, feedback=None, say=lambda _m: None)
    state.cache = Cache()
    state.ahead = Ahead(1)
    return state


def test_a_result_measured_ahead_is_cached_under_the_problems_key_and_never_measured_again():
    """The review's reproduction: taking the ahead result left the cache empty, a second request ran the
    tool again, and Ahead started it a third time although the proper entry was there."""
    prob, state, cand = Keyed(), _state(), Candidate("a", "same")
    try:
        assert state.ahead.start(prob, state, cand, "bench")
        assert cached_measure(prob, state, cand, "bench") == {"time_ms": 1.0}
        key = measurement_key(prob, state, cand, "bench")
        assert key.endswith("@context") and state.cache.data == {key: {"time_ms": 1.0}}
        assert cached_measure(prob, state, cand, "bench") == {"time_ms": 1.0} and prob.calls == 1
        assert not state.ahead.start(prob, state, cand, "bench"), "the cache holds it: nothing to work ahead on"
        state.ahead.drain()
        assert prob.calls == 1
    finally:
        state.ahead.close()


def test_a_failed_measurement_ahead_is_not_cached():
    prob, state, cand = Keyed({"error": "the command exited 2"}), _state(), Candidate("a", "same")
    try:
        assert state.ahead.start(prob, state, cand, "bench")
        assert cached_measure(prob, state, cand, "bench") == {"error": "the command exited 2"}
        assert state.cache.data == {}
    finally:
        state.ahead.close()
