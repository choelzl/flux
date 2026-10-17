"""D977: Live's timeline cuts work by the box each task says it is (D1233), and a task's own time leaves its
children's out."""

import shutil

import pytest

import live_js

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def run(js):
    return live_js.run("import * as W from './live/work_style.js';\n"
                       f"process.stdout.write(JSON.stringify(({js})(W)));")


def test_a_task_bar_is_its_own_time_and_its_box_comes_from_its_tags():
    got = run("""m => {
      const p = {name: "DSE: batch", why: "", fmt: 2, tags: {box: "search"}, t0: 0, t1: 10, kids: []};
      const a = {name: "tool:x", why: "test c", fmt: 2, tags: {box: "check"}, t0: 1, t1: 4, kids: [], parent: p};
      const b = {name: "agent: claude", fmt: 2, tags: {worker: "agent:claude"}, t0: 3, t1: 6, kids: [], parent: p};
      const c = {name: "tool:y", why: "", fmt: 2, tags: {}, t0: 4, t1: 5, kids: [], parent: b};
      p.kids = [a, b]; b.kids = [c];
      const end = n => n.t1;
      return {own: m.ownSpans(p, end), leaf: m.ownSpans(a, end), agent: m.ownSpans(b, end),
              kinds: [m.boxOf(p), m.boxOf(a), m.boxOf(b), m.boxOf(c)]};
    }""")
    assert got["own"] == [[0, 1], [6, 10]], "the children's time, side by side counted once, is not the batch's"
    assert got["leaf"] == [[1, 4]] and got["agent"] == [[3, 4], [5, 6]]
    assert got["kinds"] == ["search", "check", "search", "search"], "a task that says no box is in the one around it"


def test_an_agents_turn_taken_into_its_draft_is_agent_activity_while_it_runs():
    """D1138: a draft that took in its first agent or model turn (D1050) is agent work while it runs: the turn's
    worker is the draft's (D1233)."""
    got = run("""m => {
      const draft = {name: "generation: x draft 1", why: "x#1", fmt: 2, tags: {worker: "agent:claude"}, t0: 0, t1: null, kids: []};
      const plain = {name: "generation: x draft 2", why: "x#2", fmt: 2, tags: {}, t0: 0, t1: null, kids: []};
      const model = {name: "plan: the loop", fmt: 2, tags: {worker: "model:qwen"}, t0: 0, t1: 5, kids: []};
      const tool = {name: "tool:check.py", why: "test x#1", fmt: 2, tags: {}, t0: 1, t1: 2, kids: [], parent: draft};
      const now = 10, end = n => n.t1 ?? now;
      return [m.aiWorking(draft), m.aiWorking(plain), m.aiWorking(model), m.aiWorking(tool), m.ownSpans(draft, end)]; }""")
    assert got == [True, False, True, True, [[0, 10]]], "the running turn's activity is drawn up to now"


def test_a_short_charts_ticks_are_milliseconds():
    """codec_parts' Pass 7 lasted under a second: its ticks all read "+0.0s". Under a second they are milliseconds."""
    got = run("m => { const dur = s => s < 60 ? `${s.toFixed(1)}s` : `${Math.floor(s / 60)}m`; return [[0, .05], [.025, .05], [.05, .05], [0, 4], [2, 4], [0, 400], [200, 400]].map(([at, span]) => m.tickText(at, span, dur)); }")
    assert got == ["+0 ms", "+25 ms", "+50 ms", "+0.0s", "+2.0s", "+0.0s", "+3m"]
