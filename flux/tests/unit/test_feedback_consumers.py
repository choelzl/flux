"""D397 phase 2: every loop with a model role consumes operator feedback (D388).

The contract under test: a typed note is drained at a round boundary, reaches the
next proposal prompt under the HUMAN GUIDANCE label, is persisted where the loop
keeps its record, and never vanishes silently on a model-free run. The channel here
is a fake -- the TUI's TuiFeedback and the stdin FeedbackChannel both satisfy the
same duck-typed drain() contract.
"""

from __future__ import annotations


def FakeChannel(*texts: str):
    from flux_feedback import scripted_channel

    return scripted_channel(*texts)


def test_drain_guidance_labels_accumulates_and_survives_on_note_failure():
    from flux_feedback import drain_guidance

    ch = FakeChannel("prefer low pipeline depth")
    seen: list[str] = []
    acc: list = []
    block = drain_guidance(ch, acc, on_note=lambda n: seen.append(n.text))
    assert block is not None and "HUMAN GUIDANCE" in block
    assert "prefer low pipeline depth" in block and seen == ["prefer low pipeline depth"]
    # nothing new: the block still carries ALL notes so far (guidance persists)
    again = drain_guidance(ch, acc)
    assert again is not None and "prefer low pipeline depth" in again
    # an exploding on_note must not kill the run
    ch2 = FakeChannel("x")
    boom = drain_guidance(ch2, acc, on_note=lambda n: 1 / 0)
    assert boom is not None and len(acc) == 2
    # no channel, no notes: None, so callers thread it as an optional block
    assert drain_guidance(None, []) is None


def test_imapping_note_reaches_prompt_and_record(tmp_path):
    from flux_imapping import run_study
    from flux_records import Records

    prompts: list[str] = []

    class Proposer:
        def propose(self, prompt: str) -> str:
            prompts.append(prompt)
            return "not json"       # refused by the gate, which is fine: gates hold

    db = str(tmp_path / "fb.db")
    study = run_study(seed=2, ops=2, climb_rounds=0, coordination_rounds=0,
                      llm_rounds=1, proposer=Proposer(), db_path=db,
                      feedback=FakeChannel("avoid deep pipelines"))
    assert prompts and "HUMAN GUIDANCE" in prompts[0]
    assert "avoid deep pipelines" in prompts[0]
    assert study.notes == ["avoid deep pipelines"]          # echoed on the study
    assert any("unparseable" in r for r in study.refused)   # advisory, gates unchanged
    r = Records(db, objective={"study": "interconnect_mapping", "seed": 2, "ops": 2,
                               "vu": 0.7, "dma": 0.6})
    events = [e for e in r.store.events(r.campaign_id) if e.get("kind") == "human_note"]
    assert [e["detail"]["text"] for e in events] == ["avoid deep pipelines"]
    # the resumed run reloads the note (D403): no channel this time, yet the prompt
    # still carries it, stamped as an earlier run's
    prompts.clear()
    run_study(seed=2, ops=2, climb_rounds=0, coordination_rounds=0,
              llm_rounds=1, proposer=Proposer(), db_path=db)
    assert "avoid deep pipelines" in prompts[0] and "[earlier run]" in prompts[0]


def test_imapping_model_free_run_still_records_the_note(tmp_path):
    from flux_imapping import run_study

    study = run_study(seed=2, ops=2, climb_rounds=0, coordination_rounds=0,
                      feedback=FakeChannel("try banked crossbars"))
    assert study.notes == ["try banked crossbars"]


def test_macarray_guidance_leads_the_invention_prompt():
    from flux_macarray.config import Shape
    from flux_macarray.invent import build_prompt

    shape = Shape(lanes=4, in_bits=8, w_bits=8)
    block = "HUMAN GUIDANCE (typed):\n  * try a Booth recoding"
    p = build_prompt("inv_a", shape, beat="900 MHz", tried=[], guidance=block)
    assert p.startswith(block)
    assert build_prompt("inv_a", shape, beat="900 MHz", tried=[]).startswith("Design ")


def test_interconnect_note_joins_the_knowledge_text():
    from flux_feedback import drain_guidance
    from flux_interconnect import flow

    flow.set_feedback(FakeChannel("favor arity-4 switches"))
    try:
        human = drain_guidance(flow._FEEDBACK, flow._HUMAN_NOTES)
        assert human is not None and "favor arity-4 switches" in human
    finally:
        flow.set_feedback(None)
