"""Operator notes typed during a macarray study are persisted into the campaign record
(D388/D400) -- since D446 by the loop's own drain, which every application on the loop
shares: the note lands in the record, in the lessons tagged `[human]`, and a resume re-shows
what the operator had said."""


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
