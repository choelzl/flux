"""Operator notes typed during a macarray study land in the campaign record, in the lessons
tagged `[human]`, and a resume re-shows them (D446: the loop's shared drain)."""


def test_macarray_drain_persists_operator_notes(tmp_path):
    from flux_feedback import Note, reload_notes
    from flux_loop import LoopRequest, LoopState
    from flux_records import Records

    objective = {"study": "macarray", "t": 1}
    records = Records(str(tmp_path / "m.db"), objective=objective)

    class Channel:
        def drain(self):
            return [Note(text="try a smaller tree", received_at=1.0)]

    state = LoopState(request=LoopRequest(db=str(tmp_path / "m.db")), say=lambda _m: None,
                      proposer=None, feedback=Channel(), records=records)
    block = state.drain()
    assert block and "smaller tree" in block
    assert state.lessons and state.lessons[0].startswith("[human] operator guidance")
    assert records.notes() == ["try a smaller tree"]

    again = Records(str(tmp_path / "m.db"), objective=objective)
    assert again.resumed
    assert [n.text for n in reload_notes(again)] == ["try a smaller tree"]
