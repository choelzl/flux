"""`flux probe` (D678): an agent checks its file mid-turn with the loop's own gate and stages,
within a budget per turn; each probe is on the record as the agent's own check."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from flux_loop import PromptProblem, TaskSpec, request_for, run_loop
from flux_loop.agent import DENIED, agent_spec, agent_brief
from flux_loop.probe import probe, probe_context, probe_line, probes_done

DIGITS = Path(__file__).resolve().parents[2] / "core" / "loop" / "examples" / "digits" / "problem.json"
GOOD = "\n".join(str(i) for i in range(10)) + "\n"


def _task(tmp_path, **extra):
    doc = {"id": "digits", **json.loads(DIGITS.read_text())}
    if "stages" in extra:                       # D775: the stages are flow.measure, by name
        doc["flow"]["measure"] = {st["name"]: {k: v for k, v in st.items() if k != "name"} for st in extra.pop("stages")}
    return TaskSpec.from_dict({**doc, **extra}, base=tmp_path)


def test_the_gate_alone_and_each_stage_on_its_own_side_by_side(tmp_path):
    """D679: `measure` runs only the stages named, each on its own budget, concurrently, and says
    whether each meets its limits; the gate runs alone (`gate`) or first (`--gate`)."""
    # side by side, proved without timing: while `meet` exists, each stage says it started and
    # waits (up to 30 s) to see the other's start; run one after the other, the first would not
    meet = tmp_path / "meet"
    meet.mkdir()
    slow = (f"import sys, time, pathlib; d = pathlib.Path({str(meet)!r}); me = sys.argv[1].split('/')[-1] + str(time.time())\n"
            f"if d.is_dir():\n (d / (me + '.start')).touch(); t = time.time()\n"
            f" while len(list(d.glob('*.start'))) < 2 and time.time() - t < 30: time.sleep(0.05)\n"
            f" (d / (me + ('.saw' if len(list(d.glob('*.start'))) >= 2 else '.alone'))).touch()\n")
    count = {"name": "count", "command": ["{python}", "-c", slow + "print('lines=' + str(len(open(sys.argv[1]).read().split())))",
                                          "{artifact}"], "metrics_re": {"lines": r"lines=(\d+)"}, "cutoff": {"metric": "lines", "below": 9}}
    size = {"name": "size", "command": ["{python}", "-c", slow + "print('bytes=' + str(len(open(sys.argv[1]).read())))",
                                        "{artifact}"], "metrics_re": {"bytes": r"bytes=(\d+)"}}
    task = _task(tmp_path, stages=[count, size], objectives=[{"metric": "bytes", "direction": "minimize"}])
    work = tmp_path / "w"
    work.mkdir()
    ctx = probe_context(task, work, "", {"gate": 2, "stages": 2, "size": 1})
    good, bad = work / "good.txt", work / "bad.txt"
    good.write_text(GOOD)
    bad.write_text(GOOD.replace("3", "x"))
    code, out = probe("gate", str(bad), ctx_path=ctx)
    assert code == 1 and "GATE: 1 failures" in out and "FAIL line 4" in out and "[gate probe 1 of 2]" in out
    code, out = probe("measure", str(bad), ["count", "size"], ctx_path=ctx)      # no gate: a wrong file is measured
    assert len(list(meet.glob("*.saw"))) == 2 and not list(meet.glob("*.alone")), "side by side, not one after another"
    import shutil

    shutil.rmtree(meet)                                                          # the later probes run alone
    assert "GATE" not in out and "count: lines=10" in out and "size: bytes=20" in out
    assert "limits at count: lines <= 9 -- FAILS: lines 10 fails lines <= 9 (the cutoff)" in out and code == 1
    assert "[count probe 1 of 2]" in out and "[size probe 1 of 1]" in out and "least bytes" in out
    code, out = probe("measure", str(good), ["count", "size"], ctx_path=ctx, gate_first=True)
    assert "GATE: passed" in out and "size probe budget of this turn is spent" in out and "[count probe 2 of 2]" in out
    assert code == 2
    assert probe("measure", str(good), "place", ctx_path=ctx)[0] == 2
    done = probes_done(ctx)
    assert [d["key"] for d in done] == ["gate", "count", "size", "gate", "count"]
    assert done[2]["metrics"] == {"bytes": 20.0} and done[1]["ok"] is False
    code, out = probe("gate", str(good), ctx_path=ctx)
    assert code == 2 and "gate probe budget of this turn is spent" in out
    assert probe_context(task, work, "", {"gate": 2}) != ctx, "a new turn, a new log"
    assert probe("gate", str(good), ctx_path=str(tmp_path / "none.json"))[0] == 2
    broken = {"name": "count", "command": ["{python}", "-c", "print('ERROR: no liberty file')"], "metrics_re": {"lines": r"lines=(\d+)"}}
    ctx2 = probe_context(_task(tmp_path, stages=[broken]), work, "", {"gate": 2, "stages": 1})
    code, out = probe("measure", str(good), "count", ctx_path=ctx2)
    assert code == 1 and "count: not measured" in out and "ERROR: no liberty file" in out, out


def test_the_agent_options_and_the_brief():
    assert dict(agent_spec("opencode").probe) == {"gate": 20, "stages": 3}
    assert agent_spec({"preset": "opencode", "probe": False}).probe is None
    assert dict(agent_spec({"preset": "claude", "probe": {"place": 1}}).probe) == {"gate": 20, "stages": 3, "place": 1}
    with pytest.raises(ValueError, match="agent.probe"):
        agent_spec({"preset": "claude", "probe": {"gate": "many"}})
    a = agent_spec({"preset": "claude", "allow": ["verilator"]})
    assert "Bash(verilator:*)" not in a.argv and "Bash(yosys:*)" in a.argv and a.allowed == ("verilator",)
    o = agent_spec({"preset": "opencode", "allow": ["yosys"]})
    bash = json.loads(dict(o.config)["OPENCODE_CONFIG_CONTENT"])["permission"]["bash"]
    assert "yosys" not in bash and "yosys *" not in bash and bash["verilator"] == "deny"
    with pytest.raises(ValueError, match="is not denied"):
        agent_spec({"preset": "claude", "allow": ["python3"]})
    assert "yosys" in DENIED
    line = probe_line(["screen", "place"], {"gate": 20, "stages": 3, "place": 1}, allowed=("verilator",))
    assert "`flux probe gate FILE`" in line and "`screen` 3, `place` 1" in line and "except verilator" in line
    assert "each on its own" in line and "--gate" in line
    brief = agent_brief(body="b", prefix="", artifact=Path("/w/a.sv"), workdir=Path("/w"), language="sv", part="p",
                        prior=None, failure="", probes=line)
    assert "raw tools: they are denied" in brief and "flux probe measure FILE --stage S [--stage T" in brief
    assert probe_line([], None) == ""
    free = agent_spec({"preset": "claude", "allow": "all"})              # the restriction lifted
    assert not any(a.startswith("Bash(") for a in free.argv) and free.argv[free.argv.index("--allowedTools") + 1] == "Bash"
    oc = json.loads(dict(agent_spec({"preset": "opencode", "allow": "all"}).config)["OPENCODE_CONFIG_CONTENT"])
    assert oc["permission"]["bash"] == {"*": "allow"}
    open_brief = agent_brief(body="b", prefix="", artifact=Path("/w/a.sv"), workdir=Path("/w"), language="sv", part="p",
                             prior=None, failure="", probes=probe_line(["screen"], {"gate": 20}, allowed=free.allowed),
                             denied=False)
    assert "denied" not in open_brief and "flux probe gate FILE" in open_brief


PROBING_AGENT = r'''
import os, sys
from pathlib import Path
from flux_loop.probe import probe
artifact = Path(sys.argv[1])
artifact.write_text("\n".join(["0", "1", "2", "x", "4", "5", "6", "7", "8", "9"]) + "\n")
code, out = probe("gate", str(artifact))                  # FLUX_PROBE names the turn's context
print(out)
if code:
    artifact.write_text("\n".join(str(i) for i in range(10)) + "\n")
    print(probe("gate", str(artifact))[1])
print("written")
'''


def test_a_loop_turn_hands_the_agent_its_probe_and_records_what_it_probed(tmp_path):
    fake = tmp_path / "agent.py"
    fake.write_text(PROBING_AGENT)
    doc = {"id": "digits", **json.loads(DIGITS.read_text())}
    doc["flow"] = {**doc.get("flow", {}), "generate": {"by": {"command": ["{python}", str(fake), "{artifact}"], "timeout_s": 60}}}
    doc["budget"] = {"steps": 1, "repair_attempts": 1, "prototype": False}
    task = TaskSpec.from_dict(doc, base=tmp_path)
    said = []
    out = run_loop(PromptProblem(task), request_for(task, db=str(tmp_path / "p.db")), proposer=None, log=said.append)
    assert out.decision is not None, said
    assert any("the agent probed gate x2" in m for m in said), [m for m in said if "probe" in m]
    assert sys.executable
