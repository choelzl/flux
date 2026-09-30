"""Size limits on what reaches an agent or a prompt (D671, D672): the presets send the brief on
stdin; a custom command naming `{prompt}` gets a brief too long for one argument through a file; a mined refusal quotes the head of its message, not a raw agent
output; the mined block has a budget."""

from __future__ import annotations

import sys

from flux_loop.agent import INLINE_MAX, AgentSpec, run_turn

ECHO = r'''import sys
arg = sys.argv[1]
print(f"arg={len(arg.encode())}")
if arg.startswith("Your instructions are in the file "):
    path = arg.split("the file ", 1)[1].split(" (", 1)[0]
    print(f"file={len(open(path).read())}")
'''


def test_a_brief_over_the_argument_limit_reaches_the_agent_through_a_file(tmp_path):
    fake = tmp_path / "echo.py"
    fake.write_text(ECHO)
    spec = AgentSpec("echo", (sys.executable, str(fake), "{prompt}"), None, "text", timeout_s=30)
    big = "x" * 200_000                                        # > 128 KiB: E2BIG as one argument
    turn = run_turn(spec, spec.argv, {"prompt": big}, workdir=tmp_path)
    assert turn.ok, turn.stderr
    assert "file=200000" in turn.text and int(turn.text.split("arg=")[1].split()[0]) < 1000
    small = run_turn(spec, spec.argv, {"prompt": "short"}, workdir=tmp_path)
    assert "arg=5" in small.text and "file=" not in small.text, "a short prompt stays inline"
    assert INLINE_MAX < 128 * 1024


STDIN = r'''import sys
print(f"argv={sys.argv[1:]} stdin={len(sys.stdin.read())}")
'''


def test_a_command_without_a_prompt_slot_reads_the_brief_on_stdin(tmp_path):
    """No argument carries the brief (no E2BIG at any size); a resume sends its answer."""
    from flux_loop.agent import PRESETS

    fake = tmp_path / "stdin.py"
    fake.write_text(STDIN)
    spec = AgentSpec("stdin", (sys.executable, str(fake), "--dir", "{workdir}"), None, "text", timeout_s=30)
    turn = run_turn(spec, spec.argv, {"prompt": "x" * 1_000_000, "workdir": "w"}, workdir=tmp_path)
    assert turn.ok and "stdin=1000000" in turn.text, turn.stderr
    again = run_turn(spec, spec.argv + ("{session}",), {"prompt": "brief", "workdir": "w", "session": "S1", "answer": "fix it"},
                     workdir=tmp_path)
    assert "'S1']" in again.text and "stdin=6" in again.text
    named = run_turn(spec, spec.argv + ("{prompt}",), {"prompt": "hi", "workdir": "w"}, workdir=tmp_path)
    assert "stdin=0" in named.text, "a command naming {prompt} gets it as an argument, stdin closed"
    for p in PRESETS.values():                                  # no preset puts the brief in argv
        for argv in (p["argv"], p["resume"] or ()):
            assert not {"{prompt}", "{answer}", "{prompt_file}"} & set(argv)


def test_a_refusal_fact_quotes_the_head_of_its_message(tmp_path):
    from flux_records import Records
    from flux_records.mining.mining import mine_refusal_patterns

    db = str(tmp_path / "r.db")
    rec = Records(db, objective={"study": "t"}, name="t")
    rec.phase("search")
    raw = "agent exited 1: " + "the model rambled on " * 400
    for i, tail in enumerate(("\nstack frame A", "\nstack frame B")):     # same head, different tails
        rec.trial({"name": f"c{i}", "artifact": "x"}, f"c{i}@gate", stage="gate", strategy="agent",
                  metrics={}, error=raw + tail, evaluator="gate")
    rec.close("paused")
    facts = [f for f in mine_refusal_patterns(db)]
    assert len(facts) == 1, "one group: the heads are the same"
    assert len(facts[0].statement) < 300 and facts[0].statement.endswith("...'")
    assert len(facts[0].evidence["full_messages"]) == 2


def test_the_mined_block_has_a_budget():
    from flux_knowledge import Mined

    assert Mined().max_facts == 12 and Mined().max_chars == 3000
