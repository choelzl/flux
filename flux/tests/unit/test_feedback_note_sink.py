"""`flux_feedback.note_sink` / `guidance_lesson` and `flux_extract.read_back` (D429): the
five hand-written note handlers and the four "WHAT THE RECORD SHOWS" renderers now share
one definition each."""

from __future__ import annotations

from flux_extract import duels_text, laws_text, read_back
from flux_feedback import Note, drain_guidance, guidance_lesson, note_sink
from flux_records import Records


class _Channel:
    def __init__(self, *texts):
        self._notes = [Note(text=t, received_at=1.0) for t in texts]

    def drain(self):
        out, self._notes = self._notes, []
        return out


def test_note_sink_persists_then_acknowledges(tmp_path):
    records = Records(str(tmp_path / "r.db"), objective={"s": 1})
    seen: list[str] = []
    on_note = note_sink(records, seen.append)
    block = drain_guidance(_Channel("smaller tree", "try booth"), [], on_note=on_note)
    assert records.notes() == ["smaller tree", "try booth"] and seen == ["smaller tree", "try booth"]
    assert block and "smaller tree" in block


def test_note_sink_without_a_record_still_acknowledges():
    seen: list[str] = []
    drain_guidance(_Channel("x"), [], on_note=note_sink(None, seen.append))
    assert seen == ["x"]
    drain_guidance(_Channel("y"), [], on_note=note_sink(None))          # nothing to do, no error


def test_guidance_lesson_says_where_the_note_went():
    assert guidance_lesson("go wide", reaches="the next invention prompt") == \
        "[human] operator guidance: 'go wide' -- it goes into the next invention prompt"
    assert guidance_lesson("go wide", reaches=None).endswith(
        "-- no model role in this run; recorded and reported, it reached no prompt")


def test_read_back_is_the_one_record_block():
    assert read_back([]) == "" and read_back(["", None]) == ""
    text = read_back(["a beat b", "c refused"])
    assert text == ("WHAT THE RECORD SHOWS (this campaign's earlier runs; directions, not "
                    "instructions):\n  * a beat b\n  * c refused")
    assert read_back(["x"], framing="why").startswith("WHAT THE RECORD SHOWS (why):")
    assert duels_text([]) == "" and laws_text([]) == ""
