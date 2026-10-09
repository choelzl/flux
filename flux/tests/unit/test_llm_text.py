"""`flux_llm`, the shared LLM-output helpers (D200), one copy so fixes reach every caller.

The cases are habits observed from this repo's own backends.
"""

from __future__ import annotations

import pytest
from flux_llm import strip_markdown_fence


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
    """Two fenced blocks give a deterministic result, not a concatenation."""
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
    """`<think>` is stripped before the fence search, so a draft rejected inside the trace is not returned."""
    raw = '<think>maybe\n```json\n{"action": "propose"}\n```\nno, too early\n</think>\n' \
          '```json\n{"action": "enumerate", "max_stages": 3}\n```'
    assert strip_markdown_fence(raw) == '{"action": "enumerate", "max_stages": 3}'


def test_any_object_with_propose_satisfies_the_protocol():
    """`Proposer` is structural (D508): a stub or scripted proposer stands in without this package knowing it."""
    from flux_llm import Proposer, Reply, ScriptedProposer

    class _Stub:
        def propose(self, prompt, *, schema=None, tools=None, budget=None):
            return Reply("ok")

    assert isinstance(_Stub(), Proposer) and isinstance(ScriptedProposer(["a"]), Proposer)
    assert ScriptedProposer(["a"]).propose("q").text == "a" and str(Reply("b")) == "b"
