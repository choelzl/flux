"""The one bounded ask-check-repair round every generator shares (docs/decisions.md D450).

Five modules wrote this loop: the RTL and SystemC generator nodes (the same thirty lines twice,
only the compiler in the failure text differing), the architecture generator against a schema and
an evaluator, and the prefetcher invention twice over. These pin what the shared round
guarantees, because each caller had its own convention and a wrong one is a silent waste of model
calls: the budget counts the FIRST attempt, every refusal reaches the next prompt, and an
unparsable reply is a refusal rather than the end of the round.
"""

from __future__ import annotations

from flux_llm import ask_until, refine


class Replies:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.replies[min(len(self.prompts), len(self.replies)) - 1]


def test_the_budget_counts_the_first_attempt():
    """Three attempts is three model calls, not four: the convention every caller used."""
    ask = Replies("no", "no", "no")
    got = ask_until("write it", ask=ask, parse=lambda r: r, check=lambda v: "still wrong",
                    repair=lambda v, why, reply: f"fix it: {why}", attempts=3)
    assert not got.ok and got.attempts == 3 and len(ask.prompts) == 3
    assert got.why == "still wrong"


def test_the_failure_reaches_the_next_prompt_and_the_transcript_names_its_kind():
    ask = Replies("first", "second")
    kinds = []
    got = ask_until("write it", ask=ask, parse=lambda r: r,
                    check=lambda v: "" if v == "second" else ("g++ said no", "compile error"),
                    repair=lambda v, why, reply: f"fix it: {why}", attempts=3,
                    on_attempt=lambda n, v, why: kinds.append((n, why)))
    assert got.ok and got.value == "second" and got.attempts == 2
    assert ask.prompts[1] == "fix it: g++ said no", "the real failure, not a summary"
    assert kinds == [(1, "g++ said no")]
    assert got.transcript[0].startswith("--- attempt 1 prompt ---")
    assert got.transcript[2] == "--- attempt 1 compile error ---\ng++ said no"
    assert len(ask.prompts) == 2, "it stopped when the check passed"


def test_an_unparsable_reply_is_a_refusal_not_the_end():
    """A truncated answer is the common case, and the next prompt can say so."""
    ask = Replies("", "", "good")
    got = ask_until("write it", ask=ask, parse=lambda r: r or None,
                    check=lambda v: "", repair=lambda v, why, reply: f"again ({why})",
                    attempts=3, unparsable=lambda reply: f"empty reply ({len(reply)} chars)")
    assert got.ok and got.value == "good" and got.attempts == 3
    assert ask.prompts[1] == "again (empty reply (0 chars))"
    assert "--- attempt 1 unparsable ---\nempty reply (0 chars)" in got.transcript


def test_a_check_that_passes_first_time_spends_one_call():
    ask = Replies("perfect")
    got = ask_until("write it", ask=ask, parse=lambda r: r, check=lambda v: "",
                    repair=lambda v, why, reply: "unused", attempts=5)
    assert got.ok and got.attempts == 1 and len(ask.prompts) == 1
    assert [t.split(" ---")[0] for t in got.transcript] == [
        "--- attempt 1 prompt", "--- attempt 1 response"]


def test_refine_starts_from_a_value_and_only_asks_to_fix_it():
    """The prefetcher's compile-repair shape: the design is in hand and the model is asked
    only when the compiler refuses it."""
    ask = Replies("v2", "v3")
    checked: list[str] = []

    def check(value: str) -> str:
        checked.append(value)
        return "" if value == "v3" else f"{value} does not compile"

    got = refine("v1", check=check, ask=ask, parse=lambda r: r,
                 repair=lambda v, why, reply: f"fix {v}: {why}", attempts=4)
    assert got.ok and got.value == "v3" and got.attempts == 3
    assert checked == ["v1", "v2", "v3"], "the value in hand is checked before anything is asked"
    assert ask.prompts == ["fix v1: v1 does not compile", "fix v2: v2 does not compile"]


def test_refine_stops_when_a_repair_comes_back_with_nothing():
    """A reply with no design in it is not a design to repair, and the round says which
    failure it stopped on."""
    ask = Replies("")
    got = refine("v1", check=lambda v: "does not compile", ask=ask,
                 parse=lambda r: r or None, repair=lambda v, why, reply: "fix it", attempts=4)
    assert not got.ok and got.value == "v1"
    assert "does not compile" in got.why and "returned nothing usable" in got.why


def test_one_attempt_asks_once_and_never_repairs():
    ask = Replies("bad")
    got = ask_until("write it", ask=ask, parse=lambda r: r, check=lambda v: "no",
                    repair=lambda v, why, reply: "should never be asked", attempts=1)
    assert not got.ok and got.attempts == 1 and ask.prompts == ["write it"]
