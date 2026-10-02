"""D752: the loop's task tree (looptree.js) built from recorded journals -- a sweep pass by pass, three
passes at once then three more and the Conclusion, an agent writing the design, a loop in parts --
under node, as the page builds it."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
LOOPTREE = HERE.parents[1] / "interfaces/web/src/flux_web/static/looptree.js"
FIX = HERE / "fixtures/looptree"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")

JS = r"""
const LT = require(process.argv[1]);
const m = LT.model();
const said = [];
for (const line of require("fs").readFileSync(0, "utf8").split("\n")) if (line.trim()) { const r = LT.apply(m, JSON.parse(line)); if (r.question) said.push(r.question); }
const show = (it) => it.leaf ? { leaf: it.title, line: LT.leafLine(it), n: it.tasks.length, full: LT.boxName(it, null) }
                             : { title: it.title, why: it.why, kids: it.kids.map(show) };
process.stdout.write(JSON.stringify({ tree: LT.build(m).map(show), questions: said }));
"""


def tree(events: str) -> list[dict]:
    r = subprocess.run(["node", "-e", JS, str(LOOPTREE)], input=events, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["tree"]


def titles(items: list[dict]) -> list[str]:
    return [it.get("title") or it.get("leaf") for it in items]


def branch(items: list[dict], title: str) -> dict:
    return next(it for it in items if it.get("title") == title)


def test_a_sweep_pass_by_pass_one_design_each_then_the_end():
    t = tree((FIX / "sweep.jsonl").read_text())
    assert titles(t) == ["Setup", "Pass 1", "Pass 2", "Pass 3", "End"]
    designs = [branch(t, f"Pass {i}")["why"] for i in (1, 2, 3)]
    assert len(set(designs)) == 3 and all(d and "," not in d for d in designs), designs
    assert titles(branch(t, "Pass 2")["kids"]) == ["Setup", "Search", "Design", "Check", "Measure", "Choose", "Critic"]
    design = next(k for k in branch(t, "Pass 2")["kids"] if k.get("leaf") == "Design")
    assert design["line"] == designs[1] and design["full"] == "Design", "a leaf's line: what it made"
    end = branch(t, "End")
    assert titles(end["kids"])[:2] == ["Reason", "Decision"] and end["why"] == end["kids"][1]["line"].split(" · ")[0]
    assert "with" not in " ".join(designs), "one at a time: no pass ran with another"


def test_passes_at_once_each_their_own_branch_and_the_conclusion():
    t = tree((FIX / "parallel.jsonl").read_text())
    assert titles(t) == ["Setup", "Pass 1", "Pass 2", "Pass 3", "Pass 4", "Pass 5", "Pass 6", "Conclusion", "End"]
    whys = {i: branch(t, f"Pass {i}")["why"] for i in range(1, 7)}
    assert whys[1].endswith("with 2, 3") and whys[2].endswith("with 1, 3") and whys[5].endswith("with 4, 6"), whys
    made = [w.split(" · ")[0] for w in whys.values()]
    assert len(set(made)) == 6 and not any("," in d for d in made), "six passes, six designs, one each"
    assert branch(t, "Conclusion")["why"] == "over every pass" and "Choose" in titles(branch(t, "Conclusion")["kids"])


def test_an_agent_writing_the_design_is_said_on_its_leaf():
    t = tree((FIX / "agent.jsonl").read_text())
    p1 = branch(t, "Pass 1")
    design = next(k for k in p1["kids"] if k.get("leaf") == "Design")
    assert design["line"].startswith("claude") and "add8cl#1" in design["line"]
    assert p1["why"] == "add8cl#1"
    check = next(k for k in p1["kids"] if k.get("leaf") == "Check")
    assert check["n"] >= 1 and "add8cl#1" in check["line"]
    assert [k["leaf"] for k in branch(t, "End")["kids"]][:2] == ["Reason", "Decision"]


def _parts_journal() -> str:
    """Two passes over two parts and the whole: what a loop in parts writes (D739)."""
    ev, n, t = [{"t": 0.0, "ev": "hello", "pid": 1}], 0, 0.0

    def start(name, why="", parent=None, **params):
        nonlocal n, t
        n, t = n + 1, t + 1
        ev.append({"t": t, "ev": "start", "id": n, "parent": parent, "name": name, "why": why, "params": params})
        return n

    def end(i, **out):
        nonlocal t
        t += 1
        ev.append({"t": t, "ev": "end", "id": i, "name": "", "seconds": 1.0, "failed": False, "output": out})

    def mark(name, **w):
        nonlocal t
        t += 1
        ev.append({"t": t, "ev": "mark", "name": name, "why": json.dumps(w)})

    for p in (1, 2):
        mark("pass", n=p, explore=0)
        for name in ("gate: tools", "propose: decompose", "knowledge: prepare nlu"):
            end(start(name))
        for part in ("exp", "recip"):
            end(start("propose: what next", "pick"), picked=part)
            g = start(f"generation: {part}", "LLM-gen", part=part)
            end(start("agent: opencode", f"draft {part}#{p}", g, part=part))
            b = start(f"generation: build {part}#{p}", "", g, part=part)
            end(start("tool:python3", f"test {part}#{p}", b, part=part)); end(b); end(g)
            end(start(f"critique: {part}", f"{part}#{p}", part=part), verdict="no objection")
        e = start("evaluation", "compose, chain")
        end(start("template-fill: compose", "2 part(s)", e)); end(start("simulation: confirm", "1 candidate(s)", e)); end(e)
        d = start("decide", "frontier, decision")
        end(start("critique: decision", "nlu#1", d), verdict="no objection"); end(d, decision="nlu#1")
    mark("ended", why="2 passes done")
    mark("outputs", decision={"name": "nlu#1", "metrics": {"area_um2": 5600}}, decided_by="the least area", lessons=["x"],
         established=["- nlu#1"], not_established=[], design="out/nlu.sv", answer=None)
    ev.append({"t": t + 1, "ev": "publish", "key": "standings", "payload": {"parts": [{"part": "exp", "state": "proven"}]}})
    return "".join(json.dumps(e) + "\n" for e in ev)


def test_a_loop_in_parts_has_a_branch_per_part_then_the_whole():
    t = tree(_parts_journal())
    assert titles(t) == ["Setup", "Part exp", "Part recip", "Whole", "End"]
    exp = branch(t, "Part exp")
    assert exp["why"] == "proven" and titles(exp["kids"]) == ["Pass 1", "Pass 2"]
    assert titles(exp["kids"][1]["kids"]) == ["Pick", "Design", "Check", "Critic"], "the pick of a part is under that part"
    assert next(k for k in exp["kids"][1]["kids"] if k["leaf"] == "Design")["line"] == "opencode · exp#2"
    whole = branch(t, "Whole")
    assert titles(whole["kids"][1]["kids"]) == ["Setup", "Compose", "Measure", "Choose", "Critic"]
    assert next(k for k in whole["kids"][1]["kids"] if k["leaf"] == "Choose")["line"] == "nlu#1"
    assert titles(branch(t, "Setup")["kids"]) == ["Checks", "Divide", "Reading"]


def test_a_new_start_begins_the_tree_afresh_and_a_question_is_handed_on():
    events = _parts_journal() + json.dumps({"t": 99.0, "ev": "hello", "pid": 2}) + "\n" \
        + json.dumps({"t": 100.0, "ev": "mark", "name": "question", "why": json.dumps({"q": "which width?"})}) + "\n"
    r = subprocess.run(["node", "-e", JS, str(LOOPTREE)], input=events, capture_output=True, text=True, timeout=60)
    got = json.loads(r.stdout)
    assert got["tree"] == [] and got["questions"] == [{"q": "which width?"}]
