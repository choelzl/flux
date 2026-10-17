"""(D1236) An old task journal brought up to date: the rules Live read a journal before D1233 by, run once by
flux_loop.journal_migrate. The looptree fixtures are journals of that age."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flux_loop.journal_migrate import FORMAT, migrate_events

FIX = Path(__file__).parent / "fixtures" / "looptree"
OLD = ["agent.jsonl", "parallel.jsonl", "sweep.jsonl", "scripted.jsonl"]
BOXES = {"setup", "plan", "search", "design", "check", "measure", "choose"}


def _load(name: str) -> list[dict]:
    return [json.loads(x) for x in (FIX / name).read_text().splitlines() if x.strip()]


@pytest.mark.parametrize("name", OLD)
def test_an_old_journal_comes_out_in_todays_shape(name):
    new, said = migrate_events(_load(name))
    assert said and new[0]["ev"] == "hello" and new[0]["format"] == FORMAT
    starts = {e["id"]: e for e in new if e["ev"] == "start"}
    assert all(e["tags"].get("box") in BOXES for e in starts.values()), "every task says its box"
    assert not any(e["name"] == "evaluation" for e in starts.values()), "the climb is no task"
    assert not any(e["name"] == "decide" and starts.get(e["parent"], {}).get("name", "").startswith("decide") for e in starts.values())
    tool_under = {}
    for e in starts.values():
        if e["parent"] in starts:
            tool_under.setdefault(e["parent"], []).append(e)
    assert not any(len(k) == 1 and k[0]["name"].startswith("tool:") and starts[p]["name"].split(":")[0] in ("test", "simulation")
                   for p, k in tool_under.items()), "a check's one command is its own"
    ids = set(starts)
    assert all(e["parent"] in ids or e["parent"] is None for e in new if e["ev"] in ("start", "note")), "nothing points at a task gone"
    assert all(e["id"] in ids for e in new if e["ev"] in ("end", "update", "params")), "no end without its start"


@pytest.mark.parametrize("name", OLD)
def test_a_journal_up_to_date_is_left_as_it_is(name):
    once, _ = migrate_events(_load(name))
    twice, said = migrate_events(once)
    assert twice == once and not said


def test_the_tries_and_how_they_ended_are_the_ones_the_engine_says():
    """scripted.jsonl: a python loop recorded before D1233 with scripted replies (a draft refused by its check, then
    repaired; patches refused, designs measured already). Converted, its tries and how each ended are the ones the
    engine writes for the same run since D1235, try for try."""
    new, _ = migrate_events(_load("scripted.jsonl"))
    got = [(n["tags"].get("pass"), n["tags"]["try"], n["tags"]["outcome"]) for n in new if n["ev"] == "note" and n["name"] == "try"]
    p3 = ["check failed", "not built", "not built", "check failed", "not built"] * 3
    assert got == [(1, 1, "check failed"), (1, 2, "passed"), (2, 1, "not built"), (2, 2, "passed"),
                   *[(3, i + 1, o) for i, o in enumerate(p3)]], got


def test_a_draft_that_took_in_its_turn_keeps_its_command_a_task():
    """adder8: a draft that took in its agent's turn (D1050) and then ran the generator is the agent's draft, its
    command a task under it -- as the engine writes it (the first inline turn takes the draft; a command after it is
    a task of its own). Folding the command in made the draft a command and lost the agent's conversation."""
    st = lambda i, t, name, why, parent=None, **params: {"ev": "start", "id": i, "t": t, "parent": parent, "name": name, "why": why, "params": params}  # noqa: E731
    en = lambda i, t: {"ev": "end", "id": i, "t": t, "seconds": 1, "failed": False, "output": {}}  # noqa: E731
    old = [{"ev": "hello", "t": 0}, st(1, 1, "generation: x drafts", "each checked"),
           st(2, 2, "generation: claude draft 1", "x", 1, turn="agent: claude"), st(3, 3, "tool:gen.py", "generate x#2", 2), en(3, 4), en(2, 5), en(1, 6)]
    new, _ = migrate_events(old)
    starts = {e["id"]: e for e in new if e["ev"] == "start"}
    assert 3 in starts and starts[3]["parent"] == 2 and starts[2]["params"]["turn"] == "agent: claude"
    assert starts[2]["tags"].get("worker") == "agent:claude"


def _format2(events: list[dict]) -> list[dict]:
    return [{"ev": "hello", "t": 0, "format": 2}, *events]


def test_a_part_of_a_current_journal_is_left_as_it_is():
    """(D1246) A stream's message or a window is events with no hello: their tasks say their facts, so they are
    left as they are. Read as an old start, a later pass's measurement was said to be pass 1 (the docs' Preview
    lost pass 6's Measure)."""
    part = [{"ev": "start", "id": 28, "t": 5, "parent": 25, "name": "simulation: screen", "why": "1 design", "params": {},
             "tags": {"box": "measure", "pass": 6, "design": "lut-chunk=4", "step": "screen"}},
            {"ev": "end", "id": 28, "t": 6, "name": "simulation: screen", "seconds": 1, "failed": False, "output": {}}]
    new, said = migrate_events(part)
    assert new == part and not said


def test_composing_the_parts_is_designs():
    old = _format2([{"ev": "note", "t": 1, "parent": None, "name": "template-fill: compose", "why": "0 parts",
                     "tags": {"box": "measure", "pass": 2}}])
    new, said = migrate_events(old)
    assert new[0]["format"] == FORMAT and new[1]["tags"] == {"box": "design", "pass": 2} and said


def test_an_agents_later_turns_go_into_its_try(tmp_path):
    """(D1247) A format-2 draft that took in its first turn, its nudge a task under it: one task, its outputs
    the turns' (the last words, tokens summed, turns and their times), its conversation one -- the steps files
    joined when they are there, what Flux said between the turns a step."""
    f1, f2 = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    f1.write_text("".join(json.dumps({"k": "text", "text": f"one {i}"}) + "\n" for i in range(50)))
    f2.write_text(json.dumps({"k": "text", "text": "written"}) + "\n")
    tags = {"box": "design", "pass": 1, "try": 1}
    old = _format2([
        {"ev": "start", "id": 3, "t": 1, "parent": None, "name": "generation: agent:claude draft 1", "why": "x", "params": {}, "tags": tags},
        {"ev": "params", "id": 3, "t": 1, "params": {"prompt": "the brief", "turn": "agent: claude"}, "tags": {"worker": "agent:claude"}},
        {"ev": "start", "id": 4, "t": 5, "parent": 3, "name": "agent: claude", "why": "x#1", "params": {"prompt": "You have not written it."},
         "tags": {**tags, "worker": "agent:claude"}},
        {"ev": "end", "id": 4, "t": 7, "name": "agent: claude", "seconds": 2.0, "failed": False,
         "output": {"exit": 0, "steps": [{"k": "text", "text": "written"}], "steps total": 1, "steps file": str(f2),
                    "tokens (reported)": "1,000 in, 10 out", "reply (live tail)": "written"}},
        {"ev": "end", "id": 3, "t": 7, "name": "generation: agent:claude draft 1", "seconds": 6.0, "failed": False,
         "output": {"exit": 0, "steps": [{"k": "text", "text": f"one {i}"} for i in range(10, 50)], "steps total": 50,
                    "steps file": str(f1), "tokens (reported)": "2,000 in, 20 out", "reply (live tail)": "one 49"}},
    ])
    new, said = migrate_events(old, files=True)
    assert not [e for e in new if e.get("id") == 4], "the turn is no task"
    out = next(e for e in new if e["ev"] == "end" and e["id"] == 3)["output"]
    assert out["turns"] == 2 and [t["seconds"] for t in out["turn times"]] == [4.0, 2.0]
    assert out["tokens (reported)"] == "3,000 in, 30 out" and out["reply (live tail)"] == "written"
    joined = [json.loads(x) for x in Path(out["steps file"]).read_text().splitlines()]
    assert out["steps total"] == len(joined) == 52 and joined[50] == {"k": "said", "text": "You have not written it."}
    assert joined[-1]["text"] == "written" and out["steps"] == joined[-40:]
    assert any("D1247" in s for s in said)
    again, said2 = migrate_events(new)
    assert again == new and not said2


def test_the_looptree_agent_journal_has_no_turn_under_a_try():
    """agent.jsonl: an agent's draft before D1050, its turn a task under it -- the try's own now."""
    new, _ = migrate_events(_load("agent.jsonl"))
    starts = {e["id"]: e for e in new if e["ev"] == "start"}
    assert not [e for e in starts.values() if e["name"].startswith("agent:") and e["parent"] in starts
                and starts[e["parent"]]["name"].startswith("generation:")]
