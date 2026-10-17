"""(D1236) An old task journal brought up to date: the rules Live read a journal before D1233 by, run once by
flux_loop.journal_migrate. The looptree fixtures are journals of that age."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import live_js
from flux_loop.journal_migrate import migrate_events

FIX = Path(__file__).parent / "fixtures" / "looptree"
OLD = ["agent.jsonl", "parallel.jsonl", "sweep.jsonl"]
BOXES = {"setup", "plan", "search", "design", "check", "measure", "choose"}


def _load(name: str) -> list[dict]:
    return [json.loads(x) for x in (FIX / name).read_text().splitlines() if x.strip()]


@pytest.mark.parametrize("name", OLD)
def test_an_old_journal_comes_out_in_todays_shape(name):
    new, said = migrate_events(_load(name))
    assert said and new[0]["ev"] == "hello" and new[0]["format"] == 2
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


@pytest.mark.parametrize("name", OLD)
def test_the_tries_and_how_they_ended_are_the_ones_live_read(name, tmp_path):
    """The converter's tries are Live's legacy reading of the same journal (live/legacy.js annotateTries), number for
    number, outcome for outcome."""
    new, _ = migrate_events(_load(name))
    said = sorted((n["tags"].get("pass"), n["tags"]["try"], n["tags"]["outcome"]) for n in new if n["ev"] == "note" and n["name"] == "try")
    shutil.copy(FIX / name, live_js.folder() / name)
    script = """import * as LT from "./live/events.js"; import * as M from "./live/model.js"; import { readFileSync } from "fs";
const m = LT.model();
for (const l of readFileSync(PATH, "utf8").split("\\n")) if (l.trim()) LT.apply(m, JSON.parse(l));
M.ready(m);
const out = [];
for (const n of [...m.nodes.values(), m]) for (const [k, t] of Object.entries(n.ltries || {})) out.push([Number(k), t.outcome]);
process.stdout.write(JSON.stringify(out));""".replace("PATH", json.dumps(str(live_js.folder() / name)))
    read = live_js.run(script)
    assert said and sorted(tuple(x) for x in read) == sorted(x[1:] for x in said), (said, read)
