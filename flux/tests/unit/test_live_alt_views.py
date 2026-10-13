"""D978: LiveAlt names tasks in the timeline's words, draws a pass's flow, and reads what each
box is set to from the document as written."""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).parents[2] / "interfaces/web/src/flux_web/static"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def run(js):
    uri = lambda f: "data:text/javascript;base64," + base64.b64encode(  # noqa: E731
        (STATIC / f).read_text().replace('"./work_style.js"', json.dumps(WS)).replace('"./live_alt_model.js"', json.dumps(MODEL)).encode()).decode()
    script = f"import({json.dumps(uri('live_alt_views.js'))}).then(m => process.stdout.write(JSON.stringify(({js})(m))));"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


WS = "data:text/javascript;base64," + base64.b64encode((STATIC / "work_style.js").read_bytes()).decode()
MODEL = "data:text/javascript;base64," + base64.b64encode((STATIC / "live_alt_model.js").read_bytes()).decode()
TREE = """
  const mk = (id, name, why, parent, t0, t1, failed) => ({id, name, why, parent, t0, t1, failed, kids: []});
  const dse = mk(1, "DSE: batch", "", null, 0, 30), gen = mk(2, "tool:gen.py", "generate lut-chunk=4", dse, 0, 1);
  const test = mk(3, "test: gate", "1 candidate(s)", dse, 1, 10), gold = mk(4, "tool:fluxpreset golden", "test lut-chunk=4", test, 1, 10);
  const sim = mk(5, "simulation: screen", "", dse, 10, 30), rtl = mk(6, "tool:fluxpreset rtl measure", "stage screen lut-chunk=4", sim, 10, 30);
  const evalu = mk(7, "evaluation", "", null, 31, 31.1), agent = mk(8, "agent: claude", "", null, 0, null);
  dse.kids = [gen, test, sim]; test.kids = [gold]; sim.kids = [rtl];
  const all = [dse, gen, test, gold, sim, rtl, evalu];
  const rows = all.map(node => ({node}));
"""


def test_tasks_are_named_kind_first_then_what():
    got = run(f"""m => {{ {TREE} return [...all, agent, {{name: "gate: tools", kids: []}}, {{name: "knowledge: prepare popcount", kids: []}}, {{name: "critique: decision", kids: []}}, {{name: "knowledge: mentor", kids: []}}, {{name: "knowledge: digest", kids: []}}].map(m.taskLabel); }}""")
    assert got == ["Search · lut-chunk=4", "Design · gen.py", "Check", "Check · golden", "Measure · screen", "Measure · rtl measure",
                   "Measure · evaluation", "Agent · claude", "Setup · tools ready", "Setup · inputs", "Choose · decision",
                   "Setup · mentor", "Setup · papers"]


def test_a_pass_flow_has_its_boxes_and_composing_after_measuring_is_no_repair():
    got = run(f"""m => {{ {TREE} const b = m.flowBoxes(rows, 6, false);
      return {{keys: b.map(x => x.key), states: b.map(x => x.state), design: b.find(x => x.key === "design").count, repaired: m.repaired(b)}}; }}""")
    assert got == {"keys": ["search", "design", "check", "measure"], "states": ["ok", "ok", "ok", "ok"], "design": 1, "repaired": False}


def test_a_box_whose_step_failed_says_so_though_it_finished():
    """D995: a measure that ended with one of its tools failing (exit 1) is "!", not ✓."""
    got = run(f"""m => {{ {TREE} rtl.failed = true; const b = m.flowBoxes(rows, 6, false); return b.map(x => x.state); }}""")
    assert got == ["part", "ok", "ok", "part"]          # the search holds the measure; the check passed


def test_a_design_after_a_check_is_a_repair_counted_on_the_box():
    got = run(f"""m => {{ {TREE} const again = {{id: 9, name: "tool:gen.py", why: "generate lut-chunk=4", parent: null, t0: 5, t1: 6, kids: []}};
      const b = m.flowBoxes([...rows, {{node: again}}], 6, false); return [b.find(x => x.key === "design").count, m.repaired(b)]; }}""")
    assert got == [2, True]


def test_what_each_box_is_set_to_comes_from_the_document():
    raw = {"flow": {"orchestrate": {"policy": "sweep"}, "generate": {"command": "{python} {home}/gen.py {artifact} {arch}"},
                    "test": {"lint": "x", "golden": "y", "cases": "z"}, "measure": {"screen": {}, "place": {}}},
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize"}, {"metric": "area_um2", "direction": "minimize"},
                          {"metric": "power_w", "direction": "minimize"}]}
    got = run(f"m => m.docNotes({json.dumps(raw)}, 'problem.yaml')")
    assert got["search"] == "sweep" and got["design"] == "gen.py" and got["check"] == "3 checks" and got["measure"] == "2 measurements"
    assert got["choose"] == "fmax_mhz ↑ · area_um2 ↓ +1" and got["Document"] == "problem.yaml"


def run_inspector(js):
    uri = lambda f: "data:text/javascript;base64," + base64.b64encode(  # noqa: E731
        (STATIC / f).read_text().replace('"./work_style.js"', json.dumps(WS)).replace('"./live_alt_model.js"', json.dumps(MODEL)).encode()).decode()
    views = uri("live_alt_views.js")
    ui = "data:text/javascript;base64," + base64.b64encode(b"export const dur = s => s + 's'; export const h = () => null;").decode()
    loops = "data:text/javascript;base64," + base64.b64encode(b"export const conversation = () => null; export const markdown = () => null;").decode()
    src = (STATIC / "live_alt_inspector.js").read_text().replace('"./ui.js"', json.dumps(ui)).replace('"./loops.js"', json.dumps(loops)).replace('"./work_style.js"', json.dumps(WS)).replace('"./live_alt_views.js"', json.dumps(views))
    mod = "data:text/javascript;base64," + base64.b64encode(src.encode()).decode()
    r = subprocess.run(["node", "-e", f"import({json.dumps(mod)}).then(m => process.stdout.write(JSON.stringify(({js})(m))));"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_inspector_reads_a_check_and_a_measurement_from_what_they_printed():
    """D982: a check's "N failing of M" and a measurement's name=value are the result shown first."""
    got = run_inspector("""m => [m.failingOf("FAIL a=1\\nFAIL a=2\\n2 failing of 204"), m.failingOf("0 failing"), m.failingOf("no count"),
      m.metricsOf("fmax_mhz=3162.34 area_um2=4.47606 cells=45 note=x power_w=7.5e-05")]""")
    assert got[0]["failing"] == 2 and got[0]["of"] == 204 and got[1] == {"failing": 0, "of": None, "index": 0} and got[2] is None
    assert got[3] == {"fmax_mhz": 3162.34, "area_um2": 4.47606, "cells": 45, "power_w": 7.5e-05}


def test_a_measurement_names_its_candidate_after_its_stage():
    got = run("""m => [m.candidateOf({why: "stage screen lut-chunk=4", kids: []}), m.candidateOf({why: "estimate place tree-chunk=2", kids: []}),
      m.candidateOf({why: "generate lut-chunk=4", kids: []}), m.candidateOf({why: "test lut-chunk=4", kids: []}), m.candidateOf({why: "decide", kids: []})]""")
    assert got == ["lut-chunk=4", "tree-chunk=2", "lut-chunk=4", "lut-chunk=4", ""]


def test_a_critique_is_in_the_box_it_judges_with_a_badge_not_a_box_of_its_own():
    """D1034: no Critic box -- a critique of a design is the Check box's, of the decision the Choose box's;
    the box says a critic judged it."""
    got = run(f"""m => {{ {TREE} const crit = {{id: 20, name: "critique: d#3", why: "d#3", parent: null, t0: 32, t1: 33, kids: []}};
      const pick = {{id: 21, name: "decide: frontier", why: "", parent: null, t0: 34, t1: 35, kids: []}};
      const judge = {{id: 22, name: "critique: decision", why: "d#3", parent: null, t0: 35, t1: 36, kids: []}};
      const b = m.flowBoxes([...rows, {{node: crit}}, {{node: pick}}, {{node: judge}}], 6, false);
      return b.map(x => [x.key, !!x.critic]); }}""")
    assert ["critic", True] not in got and ["critic", False] not in got
    assert dict(got)["check"] is True and dict(got)["choose"] is True and dict(got)["measure"] is False
