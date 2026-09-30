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
