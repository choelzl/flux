"""D977: LiveAlt's timeline classifies and cuts work as Live's does -- the JS kind rule equals
`flux_web.timeline.kind_of`, and a task's own time leaves its children's out."""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from flux_web.timeline import kind_of

STATIC = Path(__file__).parents[2] / "interfaces/web/src/flux_web/static"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

PHASES = [("agent: claude", ""), ("llm: propose", ""), ("model call", ""), ("tool:gen.py", "generate x=1"),
          ("tool:fluxpreset rtl measure", "stage screen x=1"), ("tool:python3", "estimate y"),
          ("tool:fluxpreset golden", "test x"), ("tool: compile", "build all"), ("tool:yosys", ""),
          ("knowledge: prepare p", ""), ("propose: decompose", ""), ("propose: next", ""), ("propose: brief d#1", "once per part"), ("gate: tools", ""),
          ("gate: the problem", ""), ("gate: admitted", ""), ("generation: build x", ""), ("generation: current", ""),
          ("build: lib", ""), ("test: gate", ""), ("simulation: screen", ""), ("evaluation", ""),
          ("template-fill: compose", ""), ("decide", ""), ("frontier", ""), ("critique: decision", ""),
          ("DSE: batch", ""), ("orchestrate: next step", ""), ("agent: claude", "orchestrate"), ("plan: round", ""), ("search: tree", ""), ("Setup", ""), ("unknown thing", "")]


def run(js):
    uri = "data:text/javascript;base64," + base64.b64encode((STATIC / "work_style.js").read_bytes()).decode()
    script = f"import({json.dumps(uri)}).then(m => process.stdout.write(JSON.stringify(({js})(m))));"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_kind_of_work_is_the_same_in_live_and_livealt():
    got = run(f"m => {json.dumps(PHASES)}.map(([n, w]) => m.kindOf(n, w))")
    assert got == [kind_of(n, w) for n, w in PHASES]


def test_a_task_bar_is_its_own_time_and_a_kind_comes_from_above():
    got = run("""m => {
      const p = {name: "DSE: batch", why: "", t0: 0, t1: 10, kids: []};
      const a = {name: "tool:x", why: "test c", t0: 1, t1: 4, kids: [], parent: p}, b = {name: "agent: claude", t0: 3, t1: 6, kids: [], parent: p};
      const c = {name: "tool:y", why: "", t0: 4, t1: 5, kids: [], parent: b};
      p.kids = [a, b]; b.kids = [c];
      const end = n => n.t1;
      return {own: m.ownSpans(p, end), leaf: m.ownSpans(a, end), agent: m.ownSpans(b, end),
              kinds: [m.phaseKind(p), m.phaseKind(a), m.phaseKind(b), m.phaseKind(c)]};
    }""")
    assert got["own"] == [[0, 1], [6, 10]], "the children's time, side by side counted once, is not the batch's"
    assert got["leaf"] == [[1, 4]] and got["agent"] == [[3, 4], [5, 6]]
    assert got["kinds"] == ["search", "check", "search", "search"], "an agent and its tools take the work they are under"


def test_a_designs_brief_is_its_design_not_a_choice():
    """D1029: `propose: brief` prepares the design agent's brief before generation -- it was Choose, done
    in seconds, so the pass looked decided while its checks still ran."""
    from flux_web.timeline import kind_of

    assert kind_of("propose: brief bau#3", "once per part") == "design"
    assert kind_of("propose: what next", "") == "choose" and kind_of("propose: finalists", "") == "choose"


def test_the_climb_through_the_stages_is_measure_work():
    """D1031: `evaluation` (compose, measure, cutoffs, routing) is what Measure does -- it was Design, so a pass
    measuring its design said "Design · evaluation, running"."""
    from flux_web.timeline import kind_of

    assert kind_of("evaluation", "compose, chain, cutoffs, routing") == "measure"
