"""D978: Live names tasks in the timeline's words, draws a pass's flow, and reads what each
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
        (STATIC / f).read_text().replace('"./work_style.js"', json.dumps(WS)).replace('"./model.js"', json.dumps(MODEL)).encode()).decode()
    script = f"import({json.dumps(uri('live/views.js'))}).then(m => process.stdout.write(JSON.stringify(({js})(m))));"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


WS = "data:text/javascript;base64," + base64.b64encode((STATIC / "live/work_style.js").read_bytes()).decode()
MODEL = "data:text/javascript;base64," + base64.b64encode((STATIC / "live/model.js").read_bytes()).decode()
TREE = """
  const mk = (id, name, why, parent, t0, t1, failed) => ({id, name, why, parent, t0, t1, failed, kids: []});
  const dse = mk(1, "DSE: batch", "", null, 0, 30), gen = mk(2, "tool:gen.py", "generate lut-chunk=4", dse, 0, 1);
  const test = mk(3, "test: gate", "1 candidate", dse, 1, 10), gold = mk(4, "tool:fluxpreset golden", "test lut-chunk=4", test, 1, 10);
  const sim = mk(5, "simulation: screen", "", dse, 10, 30), rtl = mk(6, "tool:fluxpreset rtl measure", "stage screen lut-chunk=4", sim, 10, 30);
  const evalu = mk(7, "evaluation", "", null, 31, 31.1), agent = mk(8, "agent: claude", "", null, 0, null);
  dse.kids = [gen, test, sim]; test.kids = [gold]; sim.kids = [rtl];
  const all = [dse, gen, test, gold, sim, rtl, evalu];
  const rows = all.map(node => ({node}));
"""


def test_tasks_are_named_kind_first_then_what():
    got = run(f"""m => {{ {TREE} return [...all, agent, {{name: "gate: tools", kids: []}}, {{name: "knowledge: prepare popcount", kids: []}}, {{name: "critique: decision", kids: []}}, {{name: "knowledge: mentor", kids: []}}, {{name: "knowledge: digest", kids: []}}].map(m.taskLabel); }}""")
    assert got == ["Search · lut-chunk=4", "Design · lut-chunk=4", "Check · lut-chunk=4", "Check · golden", "Measure · screen · lut-chunk=4", "Measure · screen · lut-chunk=4",
                   "Measure · evaluation", "Agent · claude", "Setup · tools ready", "Setup · inputs", "Choose · critique",
                   "Setup · mentor", "Setup · papers"]


def test_a_pass_flow_has_its_boxes_and_composing_after_measuring_is_no_repair():
    got = run(f"""m => {{ {TREE} const b = m.flowBoxes(rows, 6, false);
      return {{keys: b.map(x => x.key), states: b.map(x => x.state), design: b.find(x => x.key === "design").count, repaired: m.repaired(b)}}; }}""")
    assert got == {"keys": ["search", "design", "check", "measure"], "states": ["ok", "ok", "ok", "ok"], "design": 1, "repaired": False}


def test_a_search_done_as_a_note_is_a_box_that_ran():
    """D1052: a scripted search's scope list is a note with no task (D1047): its box is there and done, not missing."""
    got = run(f"""m => {{ {TREE} const d = mk(1, "generation: improve x", "", null, 0, 5);
      const b = m.flowBoxes([{{node: d}}], 3, true, [{{name: "orchestrate: scope list", why: "", t: 0, output: {{scopes: 2}}}}]);
      const s = b.find(x => x.key === "search"); return [s.state, s.notes.length, s.runs.length]; }}""")
    assert got == ["ok", 1, 0]
    script = f"import({json.dumps(MODEL)}).then(m => process.stdout.write(String(m.notePass({{name: 'orchestrate: scope list', t: 4, seq: 9}}, [{{name: 'pass', n: 3, t: 1, seq: 2}}]))));"
    assert subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30).stdout == "3"     # the pass it was written in


def test_a_box_whose_step_failed_says_so_though_it_finished():
    """D995: a measure that ended with one of its tools failing (exit 1) is "!", not ✓."""
    got = run(f"""m => {{ {TREE} rtl.failed = true; const b = m.flowBoxes(rows, 6, false); return b.map(x => x.state); }}""")
    assert got == ["ok", "ok", "ok", "part"]            # D1061: the measure is the Measure box's failure, not the search's that holds it


def test_a_design_after_a_check_is_a_repair_counted_on_the_box():
    got = run(f"""m => {{ {TREE} const again = {{id: 9, name: "tool:gen.py", why: "generate lut-chunk=4", parent: null, t0: 5, t1: 6, kids: []}};
      const b = m.flowBoxes([...rows, {{node: again}}], 6, false); return [b.find(x => x.key === "design").count, m.repaired(b)]; }}""")
    assert got == [2, True]


def test_a_boxs_count_is_how_many_times_its_work_ran_not_how_many_tasks_it_holds():
    """D1038: a gate of four tests is one Check; a design an evaluation composes is the measurement's;
    a decision's own steps are one Choose; four library reads in a row are one Library."""
    got = run("""m => {
      const mk = (id, name, parent, t0) => ({id, name, parent, t0, t1: t0 + 1, kids: []});
      const gen = mk(1, "generation: improve x#1", null, 0), turn = mk(2, "agent: claude", gen, 0);
      const gate = mk(3, "test: gate", null, 2), tests = [4, 5, 6, 7].map(i => mk(i, "tool:check.py", gate, 2 + i / 10));
      const sim = mk(8, "simulation: esl", null, 4), tool = mk(9, "tool:measure.py", sim, 4);
      const ev = mk(10, "evaluation", null, 6), comp = mk(11, "template-fill: compose", ev, 6), climb = mk(12, "simulation: esl", ev, 7);
      const dec = mk(13, "decide", null, 9), front = mk(14, "frontier", dec, 9), pick = mk(15, "decide", dec, 9.5), crit = mk(16, "critique: decision", dec, 9.8);
      gen.kids = [turn]; gate.kids = tests; sim.kids = [tool]; ev.kids = [comp, climb]; dec.kids = [front, pick, crit];
      const all = [gen, turn, gate, ...tests, sim, tool, ev, comp, climb, dec, front, pick, crit];
      const b = m.flowBoxes(all.map(node => ({node})), 1, false);
      const lib = [1, 2, 3, 4].map(i => mk(20 + i, "knowledge: mentor", null, i)), doc = mk(30, "gate: tools", null, 0);
      const s = m.flowBoxes([doc, ...lib].map(node => ({node})), null, false);
      return [Object.fromEntries(b.map(x => [x.key, x.count])), Object.fromEntries(s.map(x => [x.key, x.count])), [dec, pick, crit].map(m.taskLabel)]; }""")
    assert got == [{"design": 1, "check": 1, "measure": 1, "choose": 1}, {"Document": 1, "Library": 1},
                   ["Choose", "Choose", "Choose · critique"]]


def test_what_each_box_is_set_to_comes_from_the_document():
    raw = {"flow": {"orchestrate": {"policy": "sweep"}, "generate": {"command": "{python} {home}/gen.py {artifact} {arch}"},
                    "test": {"lint": "x", "golden": "y", "cases": "z"}, "measure": {"screen": {}, "place": {}}},
           "objectives": [{"metric": "fmax_mhz", "direction": "maximize"}, {"metric": "area_um2", "direction": "minimize"},
                          {"metric": "power_w", "direction": "minimize"}]}
    got = run(f"m => m.docNotes({json.dumps(raw)}, 'problem.yaml')")
    assert got["search"] == "sweep" and got["design"] == "gen.py" and got["check"] == "3 checks" and got["measure"] == "2 measurements"
    assert got["choose"] == "fmax_mhz ↑ · area_um2 ↓ +1" and got["Document"] == "problem.yaml"


def run_inspector(js):
    """D1095: the inspector's readers, live/inspector/parts.js -- plain functions, under node."""
    uri = lambda f: "data:text/javascript;base64," + base64.b64encode(  # noqa: E731
        (STATIC / f).read_text().replace('"./work_style.js"', json.dumps(WS)).replace('"./model.js"', json.dumps(MODEL)).encode()).decode()
    src = (STATIC / "live/inspector/parts.js").read_text().replace('"../work_style.js"', json.dumps(WS)).replace('"../views.js"', json.dumps(uri("live/views.js"))) \
        .replace('"../../format.js"', json.dumps(uri("format.js")))
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


def test_a_command_is_named_by_its_check_or_stage_its_program_beside():
    """D1037: `build.sh` run for the check named build is "Check · build", its program a note; a stage's
    command "Measure · esl"; the gate's default check, and a command for no check, keep the program."""
    got = run("""m => { const mk = (name, why) => ({name, why, kids: [], parent: {name: "test: fast check d#1", kids: []}});
      const xs = [mk("tool:build.sh", "build d#43"), mk("tool:sim.py", "stage esl d#43"), mk("tool:fluxpreset golden", "test d#3"),
                  mk("tool:gen.py", "generate x=1")];
      return xs.map(x => [m.taskLabel(x), m.taskNote(x)]); }""")
    assert got == [["Check · build", "build.sh"], ["Measure · esl · d#43", "sim.py"], ["Check · golden", ""], ["Design · x=1", "gen.py"]]


def test_a_box_whose_result_a_critic_rejected_is_partial_not_failed():
    """D1065: the box did its work -- "!"; the critique is its red ✗."""
    got = run("""m => { const st = {id: 1, name: "simulation: esl", why: "1 design", parent: null, t0: 0, t1: 1, kids: []};
      st.kids = [{id: 2, name: "tool:sim.py", why: "stage esl d#3", parent: st, t0: 0, t1: 1, kids: []}];
      const cr = {id: 3, name: "critique: measurement", why: "d#3@esl", parent: null, t0: 1, t1: 2, failed: true, kids: []};
      return m.flowBoxes([st, st.kids[0], cr].map(node => ({node})), 2, false).find(b => b.key === "measure").state; }""")
    assert got == "part"
