"""D978: Live names tasks in the timeline's words, draws a pass's flow, and reads what each
box is set to from the document as written."""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import live_js
from flux_loop.journal_migrate import migrate_events

STATIC = Path(__file__).parents[2] / "interfaces/web/src/flux_web/static"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def run(js):
    return live_js.module("live/views.js", js)
# a pass of a sweep as the engine writes it (D1233): each task says its box, its design, its check or stage; a check's
# and a stage's one command are the step's own (D1235)
TREE = """
  const mk = (id, name, why, parent, t0, t1, failed, tags = {}) => ({id, name, why, parent, t0, t1, failed, kids: [], fmt: 2, tags});
  const dse = mk(1, "DSE: batch", "", null, 0, 30, false, {box: "search"});
  const gen = mk(2, "tool:gen.py", "generate lut-chunk=4", dse, 0, 1, false, {box: "design", design: "lut-chunk=4", try: 1});
  const test = mk(3, "test: gate", "1 candidate", dse, 1, 10, false, {box: "check", design: "lut-chunk=4", step: "golden"});
  test.params = {turn: "tool:fluxpreset golden", command: "fluxpreset golden x.sv"};
  const sim = mk(5, "simulation: screen", "1 design", dse, 10, 30, false, {box: "measure", design: "lut-chunk=4", step: "screen"});
  sim.params = {turn: "tool:fluxpreset rtl measure"};
  const agent = mk(8, "agent: claude", "", null, 0, null, false, {box: "design", worker: "agent:claude"});
  dse.kids = [gen, test, sim];
  const all = [dse, gen, test, sim];
  const rows = all.map(node => ({node}));
"""


def test_tasks_are_named_kind_first_then_what():
    note = lambda name, box, step: f'{{name: "{name}", kids: [], fmt: 2, tags: {{box: "{box}", step: "{step}"}}}}'  # noqa: E731
    got = run(f"""m => {{ {TREE} return [...all, agent, {note("gate: tools", "setup", "tools ready")}, {note("knowledge: prepare popcount", "setup", "inputs")},
      {{name: "critique: decision", kids: [], fmt: 2, tags: {{box: "choose"}}}}, {note("knowledge: mentor", "setup", "mentor")}, {note("knowledge: digest", "setup", "papers")}].map(m.taskLabel); }}""")
    assert got == ["Search · lut-chunk=4", "Design · lut-chunk=4", "Check · lut-chunk=4", "Measure · screen · lut-chunk=4",
                   "Agent · claude", "Setup · tools ready", "Setup · inputs", "Choose · critique", "Setup · mentor", "Setup · papers"]


def test_the_workbench_digest_is_the_passs_setup_not_its_design():
    """D1143: the workbench's README is written by an agent turn under `knowledge: workbench`, the Setup's: the
    step says its box and its turn takes it (D1233)."""
    got = run("""m => {
      const mk = (id, name, why, parent, t0, t1, tags) => ({id, name, why, parent, t0, t1: t1 ?? t0 + 1, kids: [], fmt: 2, tags});
      const gen = mk(3, "generation: x draft 1", "", null, 6, 7, {box: "design"});
      const step = mk(4, "knowledge: workbench", "its README", null, 0, 5, {box: "setup", step: "workbench"}), turn = mk(5, "agent: claude", "digest workbench", step, 0.1, 5, {worker: "agent:claude"});
      step.kids = [turn];
      const box = (nodes, key) => (m.flowBoxes(nodes.map(node => ({node})), 2, true).find(b => b.key === key)?.runs || []).map(n => n.id);
      return [box([step, turn, gen], "setup"), box([step, turn, gen], "design"), m.taskLabel(step), m.flowBoxes([step, turn].map(node => ({node})), null, false).map(b => [b.key, b.runs.map(n => n.id)])]; }""")
    assert got == [[4], [3], "Setup · workbench", [["Library", [4]]]]


def test_a_pass_flow_has_its_boxes_and_composing_after_measuring_is_no_repair():
    got = run(f"""m => {{ {TREE} const b = m.flowBoxes(rows, 6, false);
      return {{keys: b.map(x => x.key), states: b.map(x => x.state), design: b.find(x => x.key === "design").count, repaired: m.repaired(b)}}; }}""")
    assert got == {"keys": ["search", "design", "check", "measure"], "states": ["ok", "ok", "ok", "ok"], "design": 1, "repaired": False}


def test_a_search_done_as_a_note_is_a_box_that_ran():
    """D1052: a scripted search's scope list is a note with no task (D1047): its box is there and done, not missing."""
    got = run(f"""m => {{ {TREE} const d = mk(1, "generation: improve x", "", null, 0, 5, false, {{box: "design"}});
      const b = m.flowBoxes([{{node: d}}], 3, true, [{{name: "orchestrate: scope list", why: "", t: 0, output: {{scopes: 2}}, tags: {{box: "search", pass: 3}}}}]);
      const s = b.find(x => x.key === "search"); return [s.state, s.notes.length, s.runs.length]; }}""")
    assert got == ["ok", 1, 0]
    assert live_js.module("live/model.js", "m => m.notePass({name: 'orchestrate: scope list', t: 4, seq: 9, tags: {pass: 3}}, [])") == 3   # the pass it was written in


def test_a_box_whose_step_failed_says_so_though_it_finished():
    """D995: a measure that ended with its command failing (exit 1) is the Measure box's failure, not the search's that holds it."""
    got = run(f"""m => {{ {TREE} sim.failed = true; const b = m.flowBoxes(rows, 6, false); return b.map(x => x.state); }}""")
    assert got == ["ok", "ok", "ok", "bad"]


def test_a_design_after_a_check_is_a_repair_counted_on_the_box():
    got = run(f"""m => {{ {TREE} const again = mk(9, "tool:gen.py", "generate lut-chunk=4", null, 5, 6, false, {{box: "design", design: "lut-chunk=4", try: 2}});
      const b = m.flowBoxes([...rows, {{node: again}}], 6, false); return [b.find(x => x.key === "design").count, m.repaired(b)]; }}""")
    assert got == [2, True]


def test_a_boxs_count_is_how_many_times_its_work_ran_not_how_many_tasks_it_holds():
    """D1038: a gate of four tests is one Check; a decision's own steps are one Choose; four library reads in a row are one Library."""
    got = run("""m => {
      const mk = (id, name, parent, t0, tags) => ({id, name, parent, t0, t1: t0 + 1, kids: [], fmt: 2, tags});
      const gen = mk(1, "generation: improve x#1", null, 0, {box: "design"}), turn = mk(2, "agent: claude", gen, 0, {box: "design", worker: "agent:claude", try: 1});
      const gate = mk(3, "test: gate", null, 2, {box: "check"}), tests = [4, 5, 6, 7].map(i => mk(i, "tool:check.py", gate, 2 + i / 10, {box: "check"}));
      const sim = mk(8, "simulation: esl", null, 4, {box: "measure"}), climb = mk(12, "simulation: esl", null, 7, {box: "measure"});
      const dec = mk(13, "decide", null, 9, {box: "choose"}), crit = mk(16, "critique: decision", dec, 9.8, {box: "choose"});
      gen.kids = [turn]; gate.kids = tests; dec.kids = [crit];
      const all = [gen, turn, gate, ...tests, sim, climb, dec, crit];
      const b = m.flowBoxes(all.map(node => ({node})), 1, false);
      const lib = [1, 2, 3, 4].map(i => mk(20 + i, "knowledge: mentor", null, i, {box: "setup"})), doc = mk(30, "gate: tools", null, 0, {box: "setup"});
      const s = m.flowBoxes([doc, ...lib].map(node => ({node})), null, false);
      return [Object.fromEntries(b.map(x => [x.key, x.count])), Object.fromEntries(s.map(x => [x.key, x.count])), [dec, crit].map(m.taskLabel)]; }""")
    assert got == [{"design": 1, "check": 1, "measure": 1, "choose": 1}, {"Document": 1, "Library": 1}, ["Choose", "Choose · critique"]]


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
    return live_js.module("live/inspector/parts.js", js)


def test_the_inspector_reads_a_check_and_a_measurement_from_what_they_printed():
    """D982: a check's "N failing of M" and a measurement's name=value are the result shown first."""
    got = run_inspector("""m => [m.failingOf("FAIL a=1\\nFAIL a=2\\n2 failing of 204"), m.failingOf("0 failing"), m.failingOf("no count"),
      m.metricsOf("fmax_mhz=3162.34 area_um2=4.47606 cells=45 note=x power_w=7.5e-05")]""")
    assert got[0]["failing"] == 2 and got[0]["of"] == 204 and got[1] == {"failing": 0, "of": None, "index": 0} and got[2] is None
    assert got[3] == {"fmax_mhz": 3162.34, "area_um2": 4.47606, "cells": 45, "power_w": 7.5e-05}


def test_a_measurement_names_its_design_as_the_engine_says_it():
    got = run("""m => { const t = (design, kids = []) => ({fmt: 2, tags: design ? {design} : {}, kids});
      return [m.designOf(t("lut-chunk=4")), m.designOf(t("", [t(""), t("tree-chunk=2")])), m.designOf(t(""))]; }""")
    assert got == ["lut-chunk=4", "tree-chunk=2", ""]


def test_a_critique_is_in_the_box_it_judges_with_a_badge_not_a_box_of_its_own():
    """D1034: no Critic box -- a critique of a design is the Check box's, of the decision the Choose box's;
    the box says a critic judged it."""
    got = run(f"""m => {{ {TREE} const crit = mk(20, "critique: d#3", "d#3", null, 32, 33, false, {{box: "check"}});
      const pick = mk(21, "decide", "", null, 34, 35, false, {{box: "choose"}}), judge = mk(22, "critique: decision", "d#3", null, 35, 36, false, {{box: "choose"}});
      const b = m.flowBoxes([...rows, {{node: crit}}, {{node: pick}}, {{node: judge}}], 6, false);
      return b.map(x => [x.key, !!x.critic]); }}""")
    assert ["critic", True] not in got and ["critic", False] not in got
    assert dict(got)["check"] is True and dict(got)["choose"] is True and dict(got)["measure"] is False


def test_a_command_is_named_by_its_check_or_stage_its_program_beside():
    """D1037: `build.sh` run for the check named build is "Check · build", its program a note; a stage's
    command "Measure · esl"; a generator's command its design."""
    got = run("""m => { const mk = (name, why, tags) => ({name, why, kids: [], fmt: 2, tags, parent: {name: "test: check d#1", kids: [], fmt: 2, tags: {box: "check"}}});
      const xs = [mk("tool:build.sh", "build d#43", {box: "check", step: "build", design: "d#43"}), mk("tool:sim.py", "stage esl d#43", {box: "measure", step: "esl", design: "d#43"}),
                  mk("tool:gen.py", "generate x=1", {box: "design", design: "x=1"})];
      return xs.map(x => [m.taskLabel(x), m.taskNote(x)]); }""")
    assert got == [["Check · build", "build.sh"], ["Measure · esl · d#43", "sim.py"], ["Design · x=1", "gen.py"]]


def test_a_box_whose_result_a_critic_rejected_is_partial_not_failed():
    """D1065: the box did its work -- "!"; the critique is its red ✗."""
    got = run("""m => { const st = {id: 1, name: "simulation: esl", why: "1 design", parent: null, t0: 0, t1: 1, kids: [], fmt: 2, tags: {box: "measure"}};
      const cr = {id: 3, name: "critique: measurement", why: "d#3@esl", parent: null, t0: 1, t2: 2, t1: 2, failed: true, kids: [], fmt: 2, tags: {box: "measure"}};
      return m.flowBoxes([st, cr].map(node => ({node})), 2, false).find(b => b.key === "measure").state; }""")
    assert got == "part"


def test_a_passs_own_setup_is_its_first_box():
    """D1138: a pass's setup (the tools' gate, the parts, the library read) is the first box, and setup work inside
    another box's work stays there."""
    got = run("""m => {
      const mk = (id, name, parent, t0, t1, box) => ({id, name, parent, t0, t1: t1 === undefined ? t0 + 1 : t1, kids: [], fmt: 2, tags: {box}});
      const gate = mk(1, "gate: tools", null, 0, undefined, "setup"), parts = mk(2, "propose: decompose", null, 1, undefined, "setup"), lib = mk(3, "knowledge: workbench", null, 2, null, "setup");
      const gen = mk(4, "generation: x draft 1", null, 3, undefined, "design"), inner = mk(5, "knowledge: mentor", gen, 3.5, undefined, "setup");
      gen.kids = [inner];
      const b = m.flowBoxes([gate, parts, lib, gen, inner].map(node => ({node})), 2, true);
      const s = m.flowBoxes([gate, parts].map(node => ({node})), null, false);
      const setup = b.find(x => x.key === "setup");
      return [b.map(x => x.key), setup.kind, setup.title, setup.state, setup.runs.map(n => n.id), s.map(x => x.key)]; }""")
    assert got == [["setup", "search", "design", "check", "measure", "choose"], "setup", "Setup", "run", [1, 2, 3], ["Document", "Parts"]]


def views(tmp_path, events, js, tests=None):
    """The views as the page draws them -- the journal on stdin replayed (events.js), the tree's and the graph's
    lines worked out (rows.js) -- `js` given the modules and the model."""
    shutil.copytree(STATIC / "live", tmp_path / "live")
    shutil.copy(STATIC / "format.js", tmp_path / "format.js")
    (tmp_path / "package.json").write_text('{"type": "module"}')
    (tmp_path / "run.js").write_text("""import * as LT from "./live/events.js"; import * as M from "./live/model.js";
import * as V from "./live/views.js"; import * as R from "./live/rules.js"; import { lines } from "./live/rows.js";
import * as W from "./live/work_style.js"; import * as LM from "./live/logmodel.js"; import * as P from "./live/inspector/parts.js"; import * as F from "./live/facts.js";
import { readFileSync } from "fs";
const m = LT.model();
for (const l of readFileSync(0, "utf8").split("\\n")) if (l.trim()) LT.apply(m, JSON.parse(l));
const sc = M.taskScope(m, "all");
const v = { selected: null, model: m, membership: sc.membership, J: R.judge(m, sc.membership), N: R.namer(["x"]), goals: [],
  isFolded: () => false, opened: new Set(), allTries: new Set(), pass: "all", passView: null, passNotes: () => [],
  noteShort: n => n.name, scopeRows: sc.rows, tests: [] };
const said = l => l.type !== "row" ? l.type : [l.name.map(s => typeof s === "string" ? s : s.b || s.text || "").join("").trim(),
  l.pills.map(p => p.text).join(","), l.act.task ? l.act.task.id : null];
process.stdout.write(JSON.stringify((__BODY__)({ LT, M, V, R, W, LM, P, F, lines, m, sc, v, said })));""".replace("__BODY__", js))
    events, _ = migrate_events(events, tests)       # D1236: a journal of an earlier form, as the converter brings it up
    r = subprocess.run(["node", str(tmp_path / "run.js")], input="".join(json.dumps(e) + "\n" for e in events),
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _digest(old, marked=False):
    st = lambda i, t, name, why, parent=None: {"ev": "start", "id": i, "t": t, "parent": parent, "name": name, "why": why}  # noqa: E731
    en = lambda i, t: {"ev": "end", "id": i, "t": t, "seconds": 1, "failed": False, "output": {}}  # noqa: E731
    head = [{"t": 0, "ev": "hello", "pid": 1}, st(1, 1, "validate", "the document"), en(1, 2)]
    head += [{"ev": "mark", "name": "pass", "t": 2.5, "why": '{"n": 1}'}] if marked else []
    body = ([st(3, 3.1, "agent: claude", "digest workbench"), en(3, 9), st(2, 3.0, "knowledge: workbench", "its README"), en(2, 9.1)] if old
            else [st(2, 3.0, "knowledge: workbench", "its README"), st(3, 3.1, "agent: claude", "digest workbench", 2), en(3, 9), en(2, 9.1)])
    return head + body + [st(4, 10, "generation: x", "drafts"), st(5, 10.1, "agent: claude", "x#1", 4), en(5, 20), en(4, 20)]


@pytest.mark.parametrize("old", [False, True])
def test_the_workbench_digest_is_one_setup_row_its_agent_its_pill(tmp_path, old):
    """D1148: Setup listed the workbench twice -- "workbench" and "claude" (AI) -- in the tree's Setup, the Library
    box's runs and list, a pass's Setup line: a journal written before D1143 had the turn beside its step. One row,
    the step, its agent its pill (D1050: a wrapper is named as itself), opening the agent's turn."""
    start = views(tmp_path / "s", _digest(old), """({ lines, m, sc, v, said, V }) => {
      const rows = sc.rows.filter(r => (sc.membership.get(r.node.id) ?? null) === null);
      const lib = V.flowBoxes(rows, null, false).find(b => b.key === "Library");
      return { tree: lines(sc.rows, v).tree().map(said).filter(l => l[0] !== "Pass 1" && !/^Design/.test(l[0])),
        runs: lib.runs.map(n => n.id), who: lib.who, list: lines(rows.filter(r => lib.ids.has(r.node.id)), v).boxList(lib.runs, "k", "setup").map(said) }; }""")
    # (D1247) the turn is the step's own -- the row opens the step, which holds its conversation
    assert start == {"tree": [["Setup", "", None], ["Setup workbench", "AI", 2], ["validate", "", 1], ["workbench", "AI", 2]],
                     "runs": [2], "who": "AI", "list": [["workbench", "AI", 2]]}
    passed = views(tmp_path / "p", _digest(old, marked=True), """({ lines, sc, v, said, V }) => {
      const rows = sc.rows.filter(r => sc.membership.get(r.node.id) === 1), setup = V.flowBoxes(rows, 1, false).find(b => b.key === "setup");
      return { tree: lines(sc.rows, v).tree().map(said).filter(l => /^Setup ·/.test(l[0])), runs: setup.runs.map(n => n.id) }; }""")
    assert passed == {"tree": [["Setup · workbench", "AI", 2]], "runs": [2]}


def _unbuilt_journal(failed_flag):
    """A generation whose builds all failed (D1046) and whose patches were refused before a build: the live run of
    popcount_live, cut down. `failed_flag`: the build check's own `failed` (False: a journal from before it was set)."""
    st = lambda i, t, name, why, parent=None: {"ev": "start", "id": i, "t": t, "parent": parent, "name": name, "why": why, "params": {}}  # noqa: E731
    en = lambda i, t, failed=False, output=None: {"ev": "end", "id": i, "t": t, "seconds": 1, "failed": failed, "output": output or {}}  # noqa: E731
    err = {"error": "no `module <name>` in the source"}
    build = lambda i, t: [st(i, t, "test: build x", "x#1", 1), st(i + 1, t + 0.1, "tool:fluxpreset golden", "test x#1", i),  # noqa: E731
                          en(i + 1, t + 0.5, output={"exit": 3, "failing": 0}), en(i, t + 0.6, failed_flag, err)]
    return ([{"t": 0, "ev": "hello", "pid": 1}, {"t": 0.5, "ev": "mark", "name": "pass", "why": '{"n": 1}'},
             st(1, 1, "generation: x", "drafts, each checked"), st(2, 1.1, "generate: x", "attempt 1", 1), en(2, 2)]
            + build(3, 2.1) + [st(5, 3, "repair: x", "attempt 2", 1), en(5, 4), st(6, 4.1, "repair: x", "attempt 3", 1), en(6, 5)]
            + build(7, 5.1) + [st(9, 6, "repair: x", "attempt 4", 1), en(9, 7), en(1, 7.5, True)])


@pytest.mark.parametrize("failed_flag", [True, False])
def test_a_try_not_built_or_refused_is_no_tick(tmp_path, failed_flag):
    """Live showed tries 11-14 and Check with ✓ while every build failed: the build check ended without `failed`
    (its `error` said it), and a patch refused before a build left its try with nothing to fail. A build that
    failed is the try's ✗; a draft its run never built, while it builds the others, is "!" (not built)."""
    got = views(tmp_path, _unbuilt_journal(failed_flag), """({ lines, sc, v }) => lines(sc.rows, v).tree()
      .filter(l => l.type === "row" && /^(try|Check|Design)/.test(l.name.map(s => typeof s === "string" ? s : s.b || s.text || "").join("").trim()))
      .map(l => [l.name.map(s => typeof s === "string" ? s : s.b || s.text || "").join("").trim(), l.mark.tone])""")
    assert got == [["Design whole", "bad"], ["try 4 · not built", "warn"], ["try 3 #1 · build x: golden failed", "bad"],
                   ["try 2 · not built", "warn"], ["try 1 #1 · build x: golden failed", "bad"]], got
    assert not any(n.startswith("Check") for n, _t in got), "no check passed: none is the Check box's"


# ---- the review of Oct 17 (D1181, D1182, D1183)
_st = lambda i, t, name, why, parent=None, **params: {"ev": "start", "id": i, "t": t, "parent": parent, "name": name, "why": why, "params": params}  # noqa: E731
_en = lambda i, t, failed=False, **output: {"ev": "end", "id": i, "t": t, "seconds": 1, "failed": failed, "output": output}  # noqa: E731
_mk = lambda n, t: {"ev": "mark", "name": "pass", "t": t, "why": json.dumps({"n": n})}  # noqa: E731
_TEXT = "(l => l.name.map(s => typeof s === 'string' ? s : s.b || s.text || '').join('').trim())"
_TREE = f"""({{ lines, sc, v }}) => lines(sc.rows, v).tree().filter(l => l.type === "row")
  .map(l => [{_TEXT}(l), typeof l.mark === "string" ? l.mark : l.mark.tone, l.depth])"""


def _one_draft_run(n):
    """A design run that is its one draft, the draft's one command a check that passed (the adder8 journal)."""
    i = 10 * n
    return [_st(i, i, "generation: x drafts", "each checked"), _st(i + 1, i + 0.1, "generation: agent:claude draft 1", f"x#{n}", i, turn="agent: claude"),
            _st(i + 2, i + 1, "tool:check.py", f"test x#{n}", i + 1), _en(i + 2, i + 2), _en(i + 1, i + 3), _en(i, i + 3)]


@pytest.mark.parametrize("scope", ["all", "1"])
def test_a_run_that_is_its_one_draft_unfolds_without_failing(tmp_path, scope):
    """Unfolding Pass 1 (or picking it in the Pass menu) threw `kids.get(...) is undefined`: the run merged with its
    one draft (D1012) lists the draft's check, which was then taken from the draft's list -- which no longer exists."""
    ev = [{"t": 0, "ev": "hello", "pid": 1}, _mk(1, 1), *_one_draft_run(1), _mk(2, 50), *_one_draft_run(6)]
    got = views(tmp_path, ev, f"""({{ lines, M, m, v }}) => {{ const sc = M.taskScope(m, "{scope}");
      return lines(sc.rows, {{ ...v, membership: sc.membership, pass: "{scope}", scopeRows: sc.rows }}).tree().filter(l => l.type === "row").map({_TEXT}); }}""")
    assert [x for x in got if x.startswith(("Design", "Check"))][:2] == ["Design #1", "Check check.py"], got


def test_passes_side_by_side_are_one_heading_each_in_pass_order(tmp_path):
    """codec_parts: passes 4, 5 and 6 ran side by side, their tasks interleaved in time -- the tree said
    "Pass 5, Pass 6, Pass 5, Pass 4 ..." (a heading per stretch) and the graph's chain ran 6, 4, 5, 7."""
    ev = [json.loads(x) for x in (Path(__file__).parent / "fixtures/looptree/parallel.jsonl").read_text().splitlines() if x.strip()]
    got = views(tmp_path, ev, f"""({{ lines, sc, v, M }}) => [lines(sc.rows, v).tree().filter(l => l.type === "row" && l.depth === 0).map({_TEXT}),
      [...new Set(sc.rows.map(r => sc.membership.get(r.node.id) ?? null))].sort(M.byPass)]""")
    assert got[0] == [f"Pass {n}" for n in range(1, 8)] and got[1] == list(range(1, 8)), got


def test_a_search_whose_one_point_failed_shows_the_failed_design(tmp_path):
    """broken_gen: the sweep's only generator exited 2; the tree merged it into its search -- "✓ Search algorithm=a ·
    nope.py" -- while the inspector of the same line said "Design · algorithm=a ✗"."""
    ev = [{"t": 0, "ev": "hello", "pid": 1}, _st(1, 1, "DSE: batch", "the points this pass tries"),
          _st(2, 1.1, "tool:nope.py", "generate algorithm=a", 1), _en(2, 1.2, exit=2, stderr="can't open file"), _en(1, 1.3, candidates=0)]
    got = views(tmp_path, ev, _TREE)
    assert got == [["Pass 1", "bad", 0], ["Search algorithm=a", "ok", 1], ["Design algorithm=a · nope.py", "bad", 1]], got


def _agent_runs(error=None):
    """primes_agents: three runs, each its one draft of two agent turns that ended well, each run failed (no design)."""
    ev = [{"t": 0, "ev": "hello", "pid": 1}]
    for k, d in enumerate(["x#1", "x#1.2", "x#1.3"]):
        i = 10 * k + 1
        ev += [_st(i, i, "generation: x", "drafts, each checked"), _st(i + 1, i + 0.1, "generation: agent:codex draft 1", "x", i),
               _st(i + 2, i + 0.2, "agent: codex", d, i + 1), _en(i + 2, i + 0.3, agent="codex codex 1.0", exit="0"),
               _st(i + 3, i + 0.4, "agent: codex", d, i + 1), _en(i + 3, i + 0.5, agent="codex codex 1.0", exit="0"),
               _en(i + 1, i + 0.6), _en(i, i + 0.7, True, **({"error": error} if error else {}))]
    return ev


@pytest.mark.parametrize("error", [None, "the agent wrote no file"])
def test_a_try_of_a_run_that_failed_is_a_failed_try_counted_as_the_graph_counts(tmp_path, error):
    """The Design line was ✗ (×6) over six ticked tries -- each agent turn a try -- while the graph said ×3 and
    named the document's agent. A draft is a try; a run that failed with no step of its try failing failed that
    try (its reason, else "no design"); the graph names the agent its turns name. (D1247) The agent's turns are
    the try's own: Design › try 3, try 2, try 1, nothing deeper."""
    got = views(tmp_path, _agent_runs(error), f"""({{ lines, sc, v, V }}) => {{
      const rows = sc.rows.filter(r => sc.membership.get(r.node.id) === 1), d = V.flowBoxes(rows, 1, false).find(b => b.key === "design");
      return [lines(sc.rows, v).tree().filter(l => l.type === "row" && l.depth <= 4).map(l => [{_TEXT}(l), typeof l.mark === "string" ? l.mark : l.mark.tone]).slice(1, 5),
              d.count, V.agentsOf(d.runs)]; }}""")
    said = error or "no design"
    assert got[0] == [["Design #1.3", "bad"], [f"try 3 #1.3 · {said}", "bad"], [f"try 2 #1.2 · {said}", "bad"],
                      [f"try 1 #1 · {said}", "bad"]], got   # D1236: the design each draft was asked for
    assert got[1] == 3 and got[2] == ["codex"]


def test_a_check_the_model_ran_in_its_turn_is_its_trys_not_a_passing_check_box(tmp_path):
    """popcount_live: the prototype's check, made inside the model's turn, was a green "Check" box after a Design
    that failed 36 times; the inspector said "attempt 1" where the tree said "try"."""
    ev = [{"t": 0, "ev": "hello", "pid": 1}, _st(1, 1, "generation: x", "drafts"), _st(2, 1.1, "generate: prototype x", "attempt 1", 1), _en(2, 2),
          _st(3, 2.1, "test: prototype x", "attempt 1", 1), _en(3, 2.1, measured="inside the turn, by the model's own check call", verdict="PASSES, score 0"),
          _st(4, 3, "repair: x", "attempt 2", 1), _en(4, 4), _en(1, 5, True)]
    got = views(tmp_path, ev, f"""({{ lines, sc, v, V, m, P }}) => {{ const t = lines(sc.rows, v).tree();
      return [t.filter(l => l.type === "row").map({_TEXT}), V.flowBoxes(sc.rows, 1, false).map(b => b.key), F.tryOf(m.nodes.get(3))]; }}""")
    assert not any(x.startswith("Check") for x in got[0]) and "check" not in got[1], got
    assert got[2] == 1, "its try, as the tree numbers it (D1233: the engine says it)"


def test_a_choice_shows_the_metric_it_was_made_on_and_no_empty_column():
    """codec_parts: "the smallest time_ms", its table fmax_mhz and area_um2 (the document's goals), both empty."""
    got = run_inspector("""m => { const goals = [{metric: "fmax_mhz", direction: "maximize"}, {metric: "area_um2", direction: "minimize"}];
      return [m.chooseColumns([["a", {time_ms: 3, cells: 4}], ["b", {time_ms: 4}]], goals, "the smallest time_ms"),
              m.chooseColumns([["a", {}]], goals, "the least area_um2 at fmax_mhz >= 2000"),
              m.chooseColumns([["a", {area_um2: 3, fmax_mhz: 2100, power_w: 1}]], goals, "the least area_um2 at fmax_mhz >= 2000")]; }""")
    assert got == [["time_ms", "cells"], [], ["area_um2", "fmax_mhz", "power_w"]]


def test_a_passs_time_adds_up_and_its_row_is_the_picked_one(tmp_path):
    """codec_parts Pass 3: "4.3s" over a breakdown of 0.9s ("all under a second") -- the passes beside it ran
    between its tasks; the time none of them ran is said. A pass shown is its row's pick, not the task's."""
    ev = [{"t": 0, "ev": "hello", "pid": 1}, _st(1, 0, "DSE: batch", "", pass_=3), _en(1, 0.8),
          _st(2, 4, "decide", "", None), _en(2, 4.3)]
    ev[1]["params"] = {"pass": 3}; ev[3]["params"] = {"pass": 3}
    got = views(tmp_path, ev, """({ lines, sc, v, W, m }) => { const t = W.passTime([...m.nodes.values()], n => n.t1);
      const picked = lines(sc.rows, { ...v, selected: 2, passView: 3, passShown: true }).tree().filter(l => l.picked);
      return [Math.round((t.t1 - t.t0) * 10), Math.round((Object.values(t.kinds).reduce((a, b) => a + b, 0) + t.idle) * 10), Math.round(t.idle * 10),
              picked.map(l => [l.key, !!l.k, l.time])]; }""")
    assert got == [43, 43, 32, [["pass:3", True, "4.3s"]]], got


def test_a_log_line_of_a_failed_command_is_a_problem(tmp_path):
    """broken_gen's only failure, "the generator exited 2", was not red, and "problems only" hid it."""
    got = views(tmp_path, [], """({ LM }) => ["algorithm=a: the generator exited 2: can't open file", "  x: no design -- nothing built in 5 tries",
      "exit code 1", "it exited 0", "exit 0 · 5 checks"].map(t => [LM.lineClass(t), LM.isProblem(t)])""")
    assert got == [["bad", True], ["bad", True], ["bad", True], ["", False], ["", False]]


def test_the_same_failure_twice_is_one_toast_counted():
    """Nine identical "The page failed" toasts stacked over the inspector; one says it, ×9."""
    stub = lambda js: "data:text/javascript;base64," + base64.b64encode(js.encode()).decode()  # noqa: E731
    view = stub("export const html = () => null; export const Glyph = 0; export const useLayoutEffect = () => {}; export const useMemo = f => f();"
                " export const useChanges = () => {}; export const changes = () => ({ emit() {} });")
    src = (STATIC / "notices.js").read_text().replace('"./view.js"', json.dumps(view)).replace('"./components/Head.js"', json.dumps(stub("export const Close = 0;")))
    src += "\nexport const _list = () => list;"
    js = (f"globalThis.window = {{ addEventListener() {{}} }}; import({json.dumps(stub(src))}).then(m => {{ for (let i = 0; i < 9; i++) m.toast('The page failed: x', 'bad');"
          " m.toast('other', 'bad'); process.stdout.write(JSON.stringify(m._list().map(t => [t.text, t.count || 1]))); process.exit(0); });")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [["The page failed: x", 9], ["other", 1]]


def test_a_check_that_exited_non_zero_is_no_tick_though_it_says_0_failing():
    """popcount_live: a failed build's check read "✓ 0 failing of 0 · exit 3" -- a tick inside a failed check."""
    got = run_inspector("""m => [[{output: {exit: 3}}, "0 failing of 0"], [{output: {exit: 0}}, "0 failing of 4"], [{output: {exit: "0"}}, "0 failing"],
      [{output: {}, failed: true}, "0 failing"], [{output: {exit: 1}}, "2 failing of 4"], [{output: {}}, "built"]]
      .map(([n, out]) => { const r = m.checkResult({ t1: 1, ...n }, out); return r && [r.ok, r.exited, r.failing]; })""")
    assert got == [[False, True, 0], [True, False, 0], [True, False, 0], [False, False, 0], [False, True, 2], None]


def test_older_words_tokens_and_the_model_are_said_as_now():
    """"the smallest time_ms" beside "smallest time_ms"; "1 candidate(s)"; "7405 in, 181 out, finish_reason=stop";
    "codex codex 1.0" -- each said one way."""
    got = run_inspector("""m => [m.tidyWords("the smallest time_ms"), m.tidyWords("the least area_um2 at fmax_mhz >= 3000"), m.tidyWords("1 candidate(s)"),
      m.tidyWords("3 candidate(s) on screen"), m.tidyWords("the generator"), m.tokensSaid("7405 in, 181 out, finish_reason=stop"),
      m.tokensSaid("7405 in, 181 out, finish_reason=length"), m.tokensSaid("7,405 in, 18 out"), m.tokensSaid("about 300 tokens"),
      m.modelSaid("codex codex 1.0"), m.modelSaid("claude-opus, Claude Code 2.1")]""")
    assert got == ["smallest time_ms", "smallest area_um2 at fmax_mhz >= 3000", "1 candidate", "3 candidates on screen", "the generator",
                   "7,405 in · 181 out", "7,405 in · 181 out · cut at its length limit", "7,405 in · 18 out", "about 300 tokens",
                   "codex 1.0", "claude-opus, Claude Code 2.1"]


def test_a_choice_says_when_its_table_is_not_the_pool():
    """popsweep: "4 in the pool" over 5 designs listed; sw: "6 in the pool" over 1."""
    got = run_inspector("""m => [m.poolSaid(4, 5), m.poolSaid(6, 1), m.poolSaid(8, 8), m.poolSaid(3, 3), m.poolSaid(undefined, 2), m.poolSaid(6, 0)]""")
    assert got == ["5 shown: every design measured, 4 of them in the pool", "1 of 6 shown: the rest measured in passes not loaded", "", "", "", ""]


def test_a_box_not_run_is_waiting_or_not_recorded_alike_everywhere(tmp_path):
    """adder8: Design at work, Search before it never ran -- the tree and the graph drew both as a bare circle, the
    inspector said "waiting". Each says the inspector's word: "skipped" (the pass went past it, live-28), else "waiting";
    the tree lists them in the flow's order, between the boxes that ran (live-10)."""
    ev = [{"t": 0, "ev": "hello", "pid": 1}, {**_st(1, 1, "generation: x", "drafts"), "params": {"pass": 2}}]
    got = views(tmp_path, ev, """({ lines, sc, v, V }) => {
      const boxes = V.flowBoxes(sc.rows, 2, true), word = k => V.waitWord(boxes, boxes.find(b => b.key === k));
      return [word("search"), word("check"), lines(sc.rows, v).tree().filter(l => l.type === "wait").map(l => [l.title, l.said])]; }""")
    assert got == ["skipped", "waiting", [["Search", "skipped"], ["Check", "waiting"], ["Measure", "waiting"], ["Choose", "waiting"]]], got
