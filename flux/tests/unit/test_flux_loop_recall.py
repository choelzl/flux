"""`flux loop ...` (flux_loop.recall): a loop's record and library read back -- by a person at a
shell (the loop folder) and by an agent inside its turn (the turn's probe context)."""

from __future__ import annotations

import json

import pytest

from flux_cli.main import build_parser
from flux_loop import recall

DOC = """\
statement: A signed 8x8 multiplier.
contract: One module named exactly `mul8`.
language: systemverilog
flow:
  test: "{python} {home}/check.py {artifact}"
  measure:
    screen: {command: "{python} {home}/size.py {artifact}", metrics: [fmax_mhz, area_um2]}
objectives:
  - {metric: fmax_mhz, direction: maximize, goal: 1000}
  - {metric: area_um2, direction: minimize}
"""
CHECK = "import sys\ntext = open(sys.argv[1]).read()\nprint('1 failing' if 'wrong' in text else '0 failing')\nsys.exit(1 if 'wrong' in text else 0)\n"
SIZE = "import sys\nn = len(open(sys.argv[1]).read())\nprint(f'fmax_mhz={2000 - n}')\nprint(f'area_um2={n}')\n"


@pytest.fixture
def loop(tmp_path, monkeypatch):
    """A loop folder `mul8` with a record: three designs measured, one refused, one idea, one digest."""
    monkeypatch.delenv("FLUX_PROBE", raising=False)
    monkeypatch.setenv("FLUX_LIBRARY", str(tmp_path / "shared"))
    (tmp_path / "shared").mkdir()
    home = tmp_path / "mul8"
    (home / "library").mkdir(parents=True)
    (home / "problem.yaml").write_text(DOC)
    (home / "check.py").write_text(CHECK)
    (home / "size.py").write_text(SIZE)
    (home / "library" / "booth.md").write_text("# Booth radix-4 recoding\n\nRadix-4 Booth recoding halves the partial "
                                               "products of a signed multiplier; a Wallace tree then sums them.\n")
    from flux_records import Records

    rec = Records(str(home / "out" / "mul8.db"), objective={"study": "mul8"}, name="mul8")
    store, cid = rec.store, rec.campaign_id
    store.append_event(cid, "idea", {"id": "idea-booth", "part": "", "title": "Booth radix-4",
                                     "hypothesis": "halves the partial products", "test": "fmax_mhz at screen"})

    def design(name: str, text: str, metrics=None, error=None, ideas=()):
        cand = {"name": name, "artifact": text, "meta": {"idea_ids": list(ideas)}}
        rec.trial(cand, name, stage="gate", strategy="loop", metrics=None if error else {"score": 0.0}, error=error)
        if metrics:
            rec.trial(cand, name, stage="screen", strategy="loop", metrics=metrics)

    design("mul8#1", "module mul8; assign p = a * w; endmodule\n", {"fmax_mhz": 800.0, "area_um2": 90.0})
    design("mul8#2", "module mul8; // booth\nassign p = booth(a, w); endmodule\n", {"fmax_mhz": 1100.0, "area_um2": 120.0}, ideas=["idea-booth"])
    design("mul8#3", "module mul8; // wallace\nassign p = wal(a, w); endmodule\n", {"fmax_mhz": 1050.0, "area_um2": 100.0})
    design("mul8#4", "module mul8; wrong; endmodule\n", error="1 failing: p != a*w at a=-128, w=-128")
    store.results.put_document("digest", {"source": "library/booth.md", "recipe": __import__("flux_knowledge.digest", fromlist=["RECIPE"]).RECIPE,
                                          "digest": "Booth radix-4: halves partial products; pair it with a Wallace or Dadda tree.",
                                          "hash": "h", "chars": 90, "model": "test"})
    store.close()
    return home


def run(*argv: str) -> tuple[int, str]:
    import contextlib
    import io

    args = build_parser().parse_args(["loop", *argv])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = args.func(args)
    return code, out.getvalue()


def test_results_rank_by_the_loops_objectives(loop):
    code, text = run("results", "--loop", str(loop))
    assert code == 0
    lines = [ln for ln in text.splitlines() if ln.strip()[:2].rstrip(".").isdigit()]
    # both meet 1000 MHz, then the smaller area decides; the one missing the goal comes last
    assert [ln.split()[1] for ln in lines] == ["mul8#3", "mul8#2", "mul8#1"]
    assert "misses a goal" in lines[-1] and "meets the goals" in lines[0]
    got = json.loads(run("results", "--loop", str(loop), "--json")[1])
    assert got["stage"] == "screen" and got["designs"][0]["metrics"]["area_um2"] == 100.0


def test_tried_says_how_far_each_design_got_and_why_it_stopped(loop):
    code, text = run("tried", "--loop", str(loop))
    assert code == 0 and "4 design(s) tried" in text
    assert "mul8#4  gate refused" in text and "p != a*w" in text
    assert "screen: fmax_mhz=1100" in text and "ideas: idea-booth" in text
    failed = json.loads(run("tried", "--loop", str(loop), "--failed", "--json")[1])
    assert [d["name"] for d in failed["designs"]] == ["mul8#4"]


def test_ideas_carry_their_evidence(loop):
    got = json.loads(run("ideas", "--loop", str(loop), "--json")[1])
    (idea,) = got["ideas"]
    assert idea["id"] == "idea-booth" and idea["status"] == "measured"
    assert {e["design"] for e in idea["evaluations"]} == {"mul8#2"}


def test_a_design_and_a_diff(loop, tmp_path):
    assert run("design", "mul8#3", "--loop", str(loop))[1].startswith("module mul8; // wallace")
    code, text = run("diff", "mul8#2", "mul8#3", "--loop", str(loop))
    assert code == 1 and "-module mul8; // booth" in text and "+module mul8; // wallace" in text
    mine = tmp_path / "mine.sv"
    mine.write_text("module mul8; // wallace\nassign p = wal(a, w); endmodule\n")
    assert run("diff", "mul8#3", str(mine), "--loop", str(loop))[0] == 0
    code, text = run("design", "mul8#9", "--loop", str(loop))
    assert code == 2 and "flux loop tried" in text


def test_library_and_digest(loop):
    code, text = run("library", "--loop", str(loop))
    assert code == 0 and "[booth.md]" in text
    code, text = run("library", "booth wallace", "--loop", str(loop))
    assert code == 0 and "booth.md" in text
    code, text = run("digest", "booth.md", "--loop", str(loop))
    assert code == 0 and "Dadda" in text
    assert run("digest", "nope.pdf", "--loop", str(loop))[0] == 2


def test_the_gate_and_a_stage_on_a_file_outside_a_turn(loop, tmp_path):
    good, bad = tmp_path / "good.sv", tmp_path / "bad.sv"
    good.write_text("module mul8; endmodule\n")
    bad.write_text("module mul8; wrong; endmodule\n")
    assert run("gate", str(good), "--loop", str(loop))[0] == 0
    code, text = run("gate", str(bad), "--loop", str(loop))
    assert code == 1 and "GATE" in text
    code, text = run("measure", str(good), "--loop", str(loop))
    assert code == 0 and "screen: fmax_mhz=" in text


def test_inside_a_turn_the_turns_loop_is_read(loop, tmp_path, monkeypatch):
    from flux_loop.document import load_task
    from flux_loop.probe import probe_context

    task = load_task(loop)
    work = tmp_path / "turn"
    work.mkdir()
    ctx = probe_context(task, work, "", None, record=str(loop / "out" / "mul8.db"), campaign="mul8")
    monkeypatch.setenv("FLUX_PROBE", ctx)
    monkeypatch.chdir(work)                      # the agent's work directory: no loop folder here
    code, text = run("results")
    assert code == 0 and "mul8#3" in text
    # probes off for this turn: the reads still work, the gate is refused
    good = work / "d.sv"
    good.write_text("module mul8; endmodule\n")
    code, text = run("gate", str(good))
    assert code == 2 and "budget" in text


def test_no_record_yet_is_said_not_made(tmp_path, monkeypatch):
    monkeypatch.delenv("FLUX_PROBE", raising=False)
    home = tmp_path / "fresh"
    home.mkdir()
    (home / "problem.yaml").write_text(DOC)
    code, text = run("tried", "--loop", str(home))
    assert code == 2 and "not run" in text and not (home / "out" / "fresh.db").exists()
    assert recall.LOOKUP_LINE.startswith("WHAT THE LOOP KNOWS is not pasted here")


BOX_AGENT = """import json, os, subprocess, sys
from pathlib import Path
brief, out = Path(sys.argv[1]).read_text(), Path(sys.argv[2])
got = subprocess.run([sys.executable, "-c", "import sys; from flux_cli.main import main; sys.exit(main())", "loop", "results", "--json"],
                     capture_output=True, text=True)
(out.parent / "seen.json").write_text(json.dumps({"brief": brief, "probe": os.environ.get("FLUX_PROBE", ""), "results": got.stdout}))
out.write_text(json.dumps({"ok": True}))
print("written")
"""


def test_a_box_agent_reads_the_record_through_flux_loop_not_the_database(loop, tmp_path):
    """D1115: a box's agent (the orchestrator, a critic) is told `flux loop` and its turn has the loop's context, so
    `flux loop results` in its turn reads this loop's record; the brief says not to open the database itself."""
    from types import SimpleNamespace

    from flux_loop import PromptProblem
    from flux_loop.boxes import box_turn
    from flux_loop.document import load_task

    fake = tmp_path / "box.py"
    fake.write_text(BOX_AGENT)
    spec = {"command": [__import__("sys").executable, str(fake), "{prompt_file}", "{artifact}"], "output": "text", "timeout_s": 60}
    said: list[str] = []
    state = SimpleNamespace(workdir=str(tmp_path / "pass"), say=said.append, records=SimpleNamespace(campaign_id="mul8", store=None, remember=lambda *_a, **_k: None),
                            request=SimpleNamespace(db=str(loop / "out" / "mul8.db")), proposer=None, lessons=[])
    problem = PromptProblem(load_task(loop))
    assert box_turn("orchestrate", spec, "Which design next?", {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                    state, problem=problem) == {"ok": True}, said
    seen = json.loads(next((tmp_path / "pass").rglob("seen.json")).read_text())
    assert "flux loop results" in seen["brief"] and "Never open the record database" in seen["brief"]
    assert "your skill `flux-loop-commands`" in seen["brief"], "D1152: the built-in skill says the rest"
    assert seen["probe"] and json.loads(seen["results"])["designs"][0]["name"] == "mul8#3"
