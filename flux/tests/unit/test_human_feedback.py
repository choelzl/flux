"""Typed operator guidance reaches the proposer prompt labelled as advisory, is persisted as
campaign events, and is acknowledged even with no model role (docs/decisions.md D388).

Real stores in tmp dirs, an injected stream instead of a terminal, no mocks. The claim under
test is the whole channel: typed -> queued (once) -> drained -> persisted as `human_note` ->
rendered under the HUMAN GUIDANCE label -> spliced into `build_prompt` -> reloaded by a
resumed campaign as earlier-run guidance. The inert-without-a-TTY posture matters as much as
the happy path: a piped run must be byte-identical with the channel on.
"""

from __future__ import annotations

import io
import random
import threading
import time

from flux_feedback import FeedbackChannel, Note, render_guidance
from flux_feedback.channel import _LABEL


class _FakeTerminal:
    """A stream that says it is a TTY and serves the given lines, then blocks like a quiet
    terminal (until close) instead of returning EOF."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self._served = threading.Event()
        self._closed = threading.Event()

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        if self._lines:
            line = self._lines.pop(0)
            if not self._lines:
                self._served.set()
            return line
        self._closed.wait(timeout=5.0)
        return ""

    def wait_served(self) -> None:
        assert self._served.wait(timeout=5.0), "the reader thread never consumed the lines"

    def close(self) -> None:
        self._closed.set()


def _drained(channel: FeedbackChannel, n: int) -> list[Note]:
    """Drain until `n` notes arrived; the reader thread hands them over asynchronously."""
    notes: list[Note] = []
    deadline = time.time() + 5.0
    while len(notes) < n and time.time() < deadline:
        notes.extend(channel.drain())
        time.sleep(0.01)
    return notes


def test_typed_lines_are_collected_acknowledged_and_drained_once():
    fake = _FakeTerminal(["prefer smaller tables\n", "\n", "storage matters most\n"])
    said: list[str] = []
    channel = FeedbackChannel(stream=fake, say=said.append)
    assert channel.active
    channel.start()
    fake.wait_served()

    notes = _drained(channel, 2)
    assert [n.text for n in notes] == ["prefer smaller tables", "storage matters most"]
    assert all(n.origin == "this-run" for n in notes)
    assert channel.drain() == []                      # a note is delivered exactly once
    assert any("feedback:" in line for line in said)  # the hint printed
    acks = [line for line in said if line.startswith("feedback noted")]
    assert len(acks) == 2                             # the blank line got no ack
    channel.close()
    fake.close()


def test_without_a_terminal_the_channel_is_inert():
    said: list[str] = []
    channel = FeedbackChannel(stream=io.StringIO("this is a pipe, not a person\n"),
                              say=said.append)
    assert not channel.active
    channel.start()
    assert said == []                                 # no hint: nothing invites typing
    assert channel.drain() == []
    channel.close()


def test_the_rendered_block_always_carries_the_advisory_label():
    assert render_guidance([]) == ""
    block = render_guidance([Note("go smaller", time.time())])
    assert block.startswith(_LABEL)
    assert "advisory" in block and "not" in block and "measurements" in block
    assert "go smaller" in block


def test_truncation_keeps_the_newest_notes_and_says_so():
    notes = [Note(f"note number {i} with some padding text", float(i)) for i in range(30)]
    block = render_guidance(notes, max_chars=len(_LABEL) + 120)
    assert "note number 29" in block                  # newest survives
    assert "note number 0" not in block               # oldest went first
    assert "omitted to fit" in block                  # and the model is told


def test_earlier_run_notes_render_with_their_origin_not_a_bogus_time():
    block = render_guidance([Note("from yesterday", 0.0, origin="earlier-run")])
    assert "[earlier run] from yesterday" in block


def test_notes_persist_as_campaign_events_and_reload_on_resume(tmp_path):
    from flux_prefetcher.measure import Recorder

    db = str(tmp_path / "campaign.db")
    objective = {"study": "bingo-l2-prefetcher", "seed": 0}
    first = Recorder(db, objective, log=lambda _msg: None)
    assert first.store is not None
    first.note("hold the region size at 2 KB")
    first.close("completed")

    second = Recorder(db, objective, log=lambda _msg: None)
    assert second.resumed
    assert second.notes() == ["hold the region size at 2 KB"]
    events = second.store.events(second.campaign_id)
    assert any(e["kind"] == "human_note" for e in events)
    second.close("completed")


def test_build_prompt_splices_the_human_block_only_when_given():
    from flux_prefetcher.propose import build_prompt

    baseline = {"a": 1.0}
    block = render_guidance([Note("try fewer ways", time.time())])
    with_it = build_prompt(baseline, 3, human=block)
    without = build_prompt(baseline, 3)
    assert _LABEL in with_it and "try fewer ways" in with_it
    assert _LABEL not in without
    # Guidance is advisory: the rules still follow it in the prompt, so the gates keep the
    # last word textually too.
    assert with_it.index(_LABEL) < with_it.index("Reply with ONLY a JSON array")


def test_with_no_model_role_guidance_is_still_recorded_and_reported(tmp_path):
    from flux_prefetcher.flow import Study, _drain_human
    from flux_prefetcher.measure import Recorder
    from flux_prefetcher.study import PrefetcherRequest

    fake = _FakeTerminal(["stop growing the pht\n"])
    channel = FeedbackChannel(stream=fake, say=lambda _msg: None)
    channel.start()
    fake.wait_served()

    db = str(tmp_path / "campaign.db")
    s = Study(request=PrefetcherRequest(db=db), say=lambda _msg: None,
              started=0.0, rng=random.Random(0), propose=None, feedback=channel)
    s.recorder = Recorder(db, {"study": "bingo-l2-prefetcher"}, log=lambda _msg: None)

    deadline = time.time() + 5.0
    block = None
    while block is None and time.time() < deadline:
        block = _drain_human(s)
        time.sleep(0.01)

    assert block is not None and "stop growing the pht" in block
    assert any(line.startswith("[human]") and "reached no prompt" in line for line in s.lessons)
    assert s.recorder.notes() == ["stop growing the pht"]
    channel.close()
    fake.close()
    s.recorder.close("completed")
