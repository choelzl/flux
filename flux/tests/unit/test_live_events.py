"""D752, D1095: Live's task model (live/events.js) replayed from journals under node, as the page replays them --
a recorded sweep, a new start, a question, a tool's exit, an agent's time limit, a journal cut short."""

import json
import shutil
from pathlib import Path

import pytest

import live_js

HERE = Path(__file__).parent
EVENTS = HERE.parents[1] / "interfaces/web/src/flux_web/static/live/events.js"
FIX = HERE / "fixtures/looptree"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def replay(events: str, body: str):
    """`body` (JS, with `LT` the module and `m` the model the journal on stdin was applied to) writes its answer."""
    js = ("import * as LT from './live/events.js'; import { readFileSync } from 'fs';\n{ const m = LT.model(), said = [];\n"
          "for (const l of readFileSync(0, 'utf8').split('\\n')) if (l.trim()) { const r = LT.apply(m, JSON.parse(l)); if (r.question) said.push(r.question); }\n"
          + body + "\n}")
    return live_js.run(js, events)


def lines(ev: list[dict]) -> str:
    return "".join(json.dumps(e) + "\n" for e in ev)


def test_a_recorded_sweep_replays_pass_by_pass_every_task_ended():
    got = replay((FIX / "sweep.jsonl").read_text(), "process.stdout.write(JSON.stringify({passes: m.marks.filter(k => k.name === 'pass').map(k => k.n),"
                 " open: [...m.nodes.values()].filter(n => n.t1 == null).length, tasks: m.nodes.size, roots: m.roots.length}));")
    assert got["passes"] == [2, 3] and got["open"] == 0 and got["tasks"] > got["roots"] > 0, got


def test_a_new_start_begins_afresh_and_a_question_is_handed_on():
    events = (FIX / "sweep.jsonl").read_text() + lines([{"t": 99.0, "ev": "hello", "pid": 2},
                                                       {"t": 100.0, "ev": "mark", "name": "question", "why": json.dumps({"q": "which width?"})}])
    got = replay(events, "process.stdout.write(JSON.stringify({tasks: m.nodes.size, marks: m.marks.length, said}));")
    assert got == {"tasks": 0, "marks": 0, "said": [{"q": "which width?"}]}


@pytest.mark.parametrize("exit_status, failed", [(0, False), (1, True), ("timed out", True)])
def test_a_tools_exit_status_decides_whether_it_failed(exit_status, failed):
    """Debug stderr does not fail an exit-0 tool; nonzero exits and timeouts fail it."""
    ev = [{"t": 0, "ev": "hello", "pid": 1}, {"t": 1, "ev": "mark", "name": "pass", "why": json.dumps({"n": 1, "explore": 0})},
          {"t": 2, "ev": "start", "id": 1, "parent": None, "name": "DSE: batch", "why": "", "params": {}},
          {"t": 3, "ev": "start", "id": 2, "parent": 1, "name": "tool:python3", "why": "generate x=1", "params": {}},
          {"t": 4, "ev": "end", "id": 2, "name": "", "seconds": 0.1, "failed": False,
           "output": {"exit": exit_status, "stderr": "DEBUG: testing error handling\n"}},
          {"t": 5, "ev": "end", "id": 1, "name": "", "seconds": 1, "failed": False, "output": {}}]
    assert replay(lines(ev), "process.stdout.write(JSON.stringify(!!m.nodes.get(2).failed));") is failed


def test_a_journal_cut_short_is_settled_interrupted_once_the_run_ended():
    """D928: a run killed before its tasks ended leaves them begun in the journal; once the page knows the
    run is over they are interrupted (failed, at its end) -- and running again should the run go on."""
    ev = [{"t": 0, "ev": "hello"}, {"t": 1, "ev": "mark", "name": "pass", "why": json.dumps({"n": 1})},
          {"t": 2, "ev": "start", "id": 1, "parent": None, "name": "DSE: batch", "why": "", "params": {}},
          {"t": 3, "ev": "start", "id": 2, "parent": 1, "name": "tool:python3", "why": "generate x=1", "params": {}},
          {"t": 4, "ev": "end", "id": 2, "name": "", "seconds": 1, "failed": False, "output": {}},
          {"t": 5, "ev": "start", "id": 3, "parent": 1, "name": "tool:python3", "why": "generate x=2", "params": {}}]
    before, ended, again, early, real = replay(lines(ev), """
const look = () => [...m.nodes.values()].map(n => [n.id, n.t1 == null, !!n.failed, !!n.interrupted, n.t1 ?? null]);
const out = [look()];
LT.settle(m, 9); out.push(look());
LT.settle(m, null); out.push(look());
LT.settle(m, 1); out.push(look());                      // an end before them (a start's time): the last event seen
LT.apply(m, { t: 7, ev: "end", id: 3, seconds: 2, failed: false, output: {} }); out.push(look());
process.stdout.write(JSON.stringify(out));""")
    assert before == [[1, True, False, False, None], [2, False, False, False, 4], [3, True, False, False, None]]
    assert ended == [[1, False, True, True, 9], [2, False, False, False, 4], [3, False, True, True, 9]]
    assert again == before, "the run goes on: what was settled runs again"
    assert early == [[1, False, True, True, 5], [2, False, False, False, 4], [3, False, True, True, 5]]
    assert real[2] == [3, False, False, False, 7], "its own end replaces the settled one"


def test_an_agent_stopped_at_its_time_limit_warns_and_a_tool_exit_fails():
    """D997: exit 124 of an agent is its time limit -- a warning, not a failure; a tool's exit 1 fails."""
    ev = [{"ev": "start", "id": 1, "name": "agent: claude", "t": 0}, {"ev": "end", "id": 1, "t": 9, "seconds": 9, "output": {"exit": 124}},
          {"ev": "start", "id": 2, "name": "tool:python3", "t": 9}, {"ev": "end", "id": 2, "t": 10, "seconds": 1, "output": {"exit": 1}}]
    assert replay(lines(ev), "process.stdout.write(JSON.stringify([...m.nodes.values()].map(n => [n.failed, !!n.timedOut])));") \
        == [[False, True], [True, False]]


def test_the_live_state_fills_running_tasks_and_the_standings():
    ev = [{"t": 0, "ev": "hello"}, {"t": 1, "ev": "start", "id": 1, "parent": None, "name": "agent: claude", "why": "", "params": {}},
          {"t": 2, "ev": "start", "id": 2, "parent": None, "name": "tool:x", "why": "", "params": {}},
          {"t": 3, "ev": "end", "id": 2, "seconds": 1, "output": {}}]
    got = replay(lines(ev), """LT.applyLive(m, { updates: { 1: { status: "thinking" }, 2: { status: "late" } }, publish: { best: 3 } });
process.stdout.write(JSON.stringify([m.nodes.get(1).fields, m.nodes.get(2).fields, m.standings.get("best")]));""")
    assert got == [{"status": "thinking"}, {}, 3], "an ended task keeps its own fields"


def _digest_journal(old: bool, mark_between: bool = False) -> list[dict]:
    """The workbench's README by an agent: written now (D1143) the step opens at its own start and holds the turn;
    written before, the turn is a top task and the quiet step comes after it, its start back-dated (D1047)."""
    st = lambda i, t, name, why, parent=None: {"ev": "start", "id": i, "t": t, "parent": parent, "name": name, "why": why}  # noqa: E731
    en = lambda i, t: {"ev": "end", "id": i, "t": t, "seconds": 1, "failed": False, "output": {}}  # noqa: E731
    head = [{"t": 0, "ev": "hello", "pid": 1}, st(1, 1, "validate", "the document"), en(1, 2)]
    if old:
        body = [st(3, 3.1, "agent: claude", "digest workbench"), en(3, 9),
                *([{"ev": "mark", "name": "pass", "t": 9.05, "why": '{"n": 1}'}] if mark_between else []),
                st(2, 3.0, "knowledge: workbench", "its README, when it changed"), en(2, 9.1)]
    else:
        body = [st(2, 3.0, "knowledge: workbench", "its README, when it changed"), st(3, 3.1, "agent: claude", "digest workbench", 2),
                en(3, 9), en(2, 9.1)]
    return head + body + [st(4, 10, "generation: x", "drafts"), st(5, 10.1, "agent: claude", "x#1", 4), en(5, 20), en(4, 20)]


@pytest.mark.parametrize("old", [False, True])
def test_a_quiet_steps_turn_written_before_it_is_under_it(old):
    """D1148: a journal written before D1143 has the workbench's agent turn as a top task and the quiet step after
    it -- Setup listed both ("workbench", and "claude" with its AI pill). The turn that began in the step's time and
    ended before the step was written is the step's, as the engine now writes it: one task, the turn under it."""
    got = replay(lines(_digest_journal(old)), "const n = id => m.nodes.get(id); process.stdout.write(JSON.stringify("
                 "{roots: m.roots.map(x => x.id), kids: n(2).kids.map(x => x.id), parent: n(3).parent && n(3).parent.id}));")
    assert got == {"roots": [1, 2, 4], "kids": [3], "parent": 2}


def test_a_turn_of_another_pass_is_not_taken_in():
    """D1148: a pass's mark between the turn and the step: they are not one pass's -- the turn stays where it was."""
    got = replay(lines(_digest_journal(True, mark_between=True)), "process.stdout.write(JSON.stringify(m.roots.map(x => x.id)));")
    assert got == [1, 3, 2, 4]
