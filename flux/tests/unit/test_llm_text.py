"""`flux_llm` — the shared LLM-output helpers (docs/decisions.md D200).

Four packages had their own copies of this and they drifted: D191 fixed a prose-intolerance bug in
two of them without knowing the other two existed, so `flows/chia_nodes`' generators kept an
anchored `^```...```$` pattern that fails on any model that says "Here is the module:" first.

These cases are the real habits observed from this repo's own backends, not hypotheticals.
"""

from __future__ import annotations

import pytest
from flux_llm import InvalidLLMProposal, LLMProposer, strip_markdown_fence


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("```\n{\"w\": 8}\n```", '{"w": 8}'),
        ("```json\n{\"w\": 8}\n```", '{"w": 8}'),
        ("```JSON\n{\"w\": 8}\n```", '{"w": 8}'),
        ("```yaml\nid: x\n```", "id: x"),
        ("```cpp\nint x;\n```", "int x;"),
        ("Here is the module:\n```cpp\nint x;\n```", "int x;"),
        ("```cpp\nint x;\n```\nHope that helps!", "int x;"),
        ("no fence at all", "no fence at all"),
        ("  padded, unfenced  ", "padded, unfenced"),
    ],
    ids=[
        "bare", "json", "JSON-uppercase", "yaml", "cpp",
        "prose-before", "prose-after", "no-fence", "whitespace-only",
    ],
)
def test_real_observed_fence_shapes(raw, expected):
    assert strip_markdown_fence(raw) == expected


def test_the_first_fenced_block_wins():
    """A model that emits two blocks (an explanation snippet then the answer, or vice versa) must
    give a deterministic result rather than concatenating them."""
    assert strip_markdown_fence("```\nfirst\n```\ntext\n```\nsecond\n```") == "first"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('<think>weighing it up</think>{"action": "stop"}', '{"action": "stop"}'),
        ('<THINK>shouty</THINK>{"a": 1}', '{"a": 1}'),
        ('<think>hmm</think>\n```json\n{"a": 1}\n```', '{"a": 1}'),
        ("<think>cut off mid-thought", ""),
        ("plain answer, no trace", "plain answer, no trace"),
    ],
    ids=["trace-then-json", "uppercase-tag", "trace-then-fence", "unterminated", "no-trace"],
)
def test_reasoning_traces_are_removed(raw, expected):
    """qwen3-class models narrate before answering; the answer is what the caller parses."""
    assert strip_markdown_fence(raw) == expected


def test_a_fence_inside_the_reasoning_trace_is_not_the_answer():
    """The case that motivates stripping `<think>` FIRST rather than after the fence search.

    A model reasoning about JSON drafts JSON, fenced, inside its trace and then rejects it. Search
    for a fence first and the discarded draft is what the caller gets — parseable, plausible, and
    not the model's answer. Observed with a real qwen3 orchestrator call, not imagined.
    """
    raw = '<think>maybe\n```json\n{"action": "propose"}\n```\nno, too early\n</think>\n' \
          '```json\n{"action": "enumerate", "max_stages": 3}\n```'
    assert strip_markdown_fence(raw) == '{"action": "enumerate", "max_stages": 3}'


def test_any_object_with_propose_satisfies_the_protocol():
    """`LLMProposer` is structural on purpose — a caller adapts a real backend onto it without this
    package knowing about CHIA."""

    class _Stub:
        def propose(self, prompt: str) -> str:
            return "ok"

    assert isinstance(_Stub(), LLMProposer)


def test_invalid_proposal_is_an_exception_callers_can_catch():
    with pytest.raises(InvalidLLMProposal):
        raise InvalidLLMProposal("a caller's own specific reason")
