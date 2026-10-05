"""The inbox channel (D684): notes another process appends reach a run's next drain."""

from __future__ import annotations

import json

from flux_feedback import InboxChannel, Joined, scripted_channel


def test_the_inbox_drains_what_was_appended_since(tmp_path):
    p = tmp_path / "inbox.jsonl"
    p.write_text(json.dumps({"text": "before the run"}) + "\n")
    said = []
    ch = InboxChannel(str(p), say=said.append)
    assert ch.drain() == [], "what was there before the run is not this run's"
    with open(p, "a") as fh:
        fh.write(json.dumps({"text": "use a Kogge-Stone", "by": "bob", "t": 5.0}) + "\n")
        fh.write('{"text": "half')
    notes = ch.drain()
    assert [n.text for n in notes] == ["use a Kogge-Stone"] and any("from bob" in s for s in said)
    with open(p, "a") as fh:
        fh.write(' written"}\n')
    assert [n.text for n in ch.drain()] == ["half written"]
    joined = Joined([scripted_channel("typed"), ch])
    with open(p, "a") as fh:
        fh.write(json.dumps({"text": "from the page", "t": 1.0}) + "\n")
    assert sorted(n.text for n in joined.drain()) == ["from the page", "typed"]


def test_a_plain_run_listens_to_the_inbox_when_named(tmp_path, monkeypatch):
    from flux_tui import demo_run

    monkeypatch.setenv("FLUX_FEEDBACK_INBOX", str(tmp_path / "in.jsonl"))
    seen = {}
    demo_run(lambda ch: seen.setdefault("ch", ch), tui=False, title="t")
    assert isinstance(seen["ch"], Joined) and any(isinstance(c, InboxChannel) for c in seen["ch"].channels)


def test_a_note_removed_on_the_page_is_skipped_unread_and_listed_no_more(tmp_path):
    """D808: removing a note appends a line saying so -- the reader's place in the file never
    moves; a note it has not read yet is skipped; the page lists it no more."""
    import types

    from flux_web.runs import RunManager

    rm = RunManager.__new__(RunManager)
    bob = types.SimpleNamespace(name="bob")
    runs = tmp_path
    (runs / "inbox.jsonl").write_text(json.dumps({"text": "an old one", "t": 3.0}) + "\n")
    ch = InboxChannel(str(runs / "inbox.jsonl"), say=lambda s: None)
    rm.note(runs, bob, "keep this")
    rm.note(runs, bob, "no, not this")
    ids = {n["text"]: n["id"] for n in rm.notes(runs)}
    assert ids["an old one"] == "3.0", "an old note's id is its time"
    assert rm.forget_note(runs, bob, ids["no, not this"]) and not rm.forget_note(runs, bob, "nope")
    assert [n["text"] for n in rm.notes(runs)] == ["an old one", "keep this"]
    assert [n.text for n in ch.drain()] == ["keep this"], "removed before it was read: skipped"
    rm.note(runs, bob, "after")
    assert [n.text for n in ch.drain()] == ["after"], "its place in the file holds"
