"""Every loop with a model role consumes operator feedback (D388, D397).

A typed note is drained at a round boundary, reaches the next proposal prompt under the HUMAN
GUIDANCE label, is persisted in the record, and never vanishes silently on a model-free run. The
channel is a fake satisfying the same drain() contract as the TUI and stdin channels.
"""

from __future__ import annotations
from flux_llm import Reply


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


def test_imapping_model_free_run_still_records_the_note(tmp_path):
    from imapping_fixtures import run_study

    study = run_study(seed=2, ops=2, climb_rounds=0, coordination_rounds=0,
                      feedback=FakeChannel("try banked crossbars"))
    assert study.notes == ["try banked crossbars"]


def test_imapping_model_round_reads_the_operators_note(monkeypatch):
    """D800: the model round runs inside the study's search command; the operator's notes reach
    it through the search's history."""
    import flux_llm
    from flux_imapping import steps
    from flux_imapping.model import Memory

    prompts: list[str] = []

    class Fake:
        def propose(self, prompt, **kw):
            prompts.append(prompt)
            return Reply.of("not json")

    monkeypatch.setattr(flux_llm, "OpenAIChatProposer", lambda *a, **k: Fake())
    sol, why = steps.propose(1, {"measured": [], "guidance": "HUMAN GUIDANCE: avoid deep pipelines"},
                             Memory(m=5), [], [])
    assert sol is None and "unparseable" in why
    assert prompts and "avoid deep pipelines" in prompts[0]

