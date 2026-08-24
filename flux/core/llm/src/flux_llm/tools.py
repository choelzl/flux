"""Tools a model may CALL inside one turn (D505): the OpenAI `tools` protocol, as data.

A `Tool` is a name, the line the model reads, the JSON schema of its arguments and the callable
that performs it -- the shape the directed search's actions had (D446), at
the level of a single model turn. A proposer that supports tools sends them with the request,
runs each call the model makes, hands the result back as a `tool` message and asks again, until
the model answers in `content` or the hop budget is spent. What the hops did is kept beside the
reply (`Reply.hops`) so the record and the task pane can show them.

Measured on LocalAI + qwen3.6-35b-a3b-apex (2026-09-18): tool calls come back structured with
thinking on or off, streaming carries them as `delta.tool_calls` fragments by index -- and a
`response_format` on the same request makes the model SKIP the tool and guess (it answered
0x5d40 for exp(1.5); the tool says 0x4480). So a schema is applied only on the last hop, with
`tool_choice: "none"`, never beside the tools.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

__all__ = ["Hop", "Tool", "ToolBudget", "as_openai", "run_call", "text_tool_calls"]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]                 # a JSON schema object, `{"type": "object", ...}`
    run: Callable[[dict[str, Any]], str]       # arguments -> what the model reads back

    def spec(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


@dataclass
class ToolBudget:
    """How many calls a turn may make, and how long each may take. `hops` bounds the
    request/answer rounds (a round may carry several calls); the last round goes out
    without tools so the turn ends in an answer."""

    hops: int = 8
    seconds_per_call: float = 60.0
    result_chars: int = 40000           # D543: a tool's result is cut past this many characters
    compact_share: float = 0.6          # D549: past this share of the window the older rounds are compacted
    compact: str = "rules"              # D550: how -- "rules" (results digested) or "llm" (the model writes what
                                        # the older rounds established and that note replaces them)
    hop_share: float | None = 0.5       # D544: a round that may call tools may write this share of the
                                        # model's context window (D506 fixed it at 24,000 tokens: live,
                                        # a round of thinking ran to the 80k cap between two checks,
                                        # eight times a turn -- an hour an attempt). The answering round
                                        # keeps the turn's own cap, and so does a round on a window
                                        # the server does not state


@dataclass
class Hop:
    """One tool call the model made and what came back, for the record and the pane."""

    tool: str
    arguments: dict[str, Any]
    result: str
    seconds: float
    error: bool = False

    def line(self, width: int = 160) -> str:
        args = json.dumps(self.arguments, ensure_ascii=False)
        args = args if len(args) <= 80 else args[:77] + "..."
        res = self.result.replace("\n", " ")
        return f"{self.tool}({args}) -> {res[:width]}" + (f" ({self.seconds:.1f}s)" if self.seconds >= 0.5 else "")


def as_openai(tools: list[Tool]) -> list[dict[str, Any]]:
    return [t.spec() for t in tools]


# D535 (review §1.3.11): a call is a tag that BEGINS a line (after `<tool_call>` at most), so
# a `<function=...>` quoted inside a string literal, a comment or a code fence in the middle
# of a line is text the model wrote, not a call it made
_TEXT_CALL = re.compile(r"^[ \t]*(?:<tool_call>\s*)?<function=([A-Za-z_][\w-]*)>(.*?)</function>", re.S | re.M)
_TEXT_PARAM = re.compile(r"<parameter=([A-Za-z_]\w*)>\s*(.*?)\s*</parameter>", re.S)


def text_tool_calls(content: str) -> list[dict[str, Any]]:
    """Tool calls the model wrote as TEXT (D506, measured live on qwen3.6: 83 of 196 answers
    carried `<tool_call><function=check><parameter=prototype>...</parameter></function>`
    -- the chat template's own syntax, emitted into `content` when the round offered no
    tools, or when the arguments were long). Returned in the server's `tool_calls` shape so
    the same loop performs them; an unterminated call (the output cap hit inside it) keeps
    what came, closed by the end of the text."""
    if not content or "<function=" not in content:
        return []
    text = content if "</function>" in content else content + "</parameter></function>"
    fenced = _outside_fences(text)
    text = fenced if fenced is not None else text
    calls = []
    for i, m in enumerate(_TEXT_CALL.finditer(text)):
        params = {k: v for k, v in _TEXT_PARAM.findall(m.group(2))}
        calls.append({"id": f"text_{i}", "type": "function",
                      "function": {"name": m.group(1), "arguments": json.dumps(params)}})
    return calls


def _outside_fences(text: str) -> str | None:
    """`text` with every fenced code block (``` ... ```) blanked to the same length, so a tag
    quoted inside one is not a call; None when there is no fence."""
    if "```" not in text:
        return None
    out, i, inside = [], 0, False
    for m in re.finditer(r"```", text):
        piece = text[i:m.start()]
        out.append(" " * len(piece) if inside else piece)
        out.append("```")
        inside = not inside
        i = m.end()
    out.append(" " * len(text[i:]) if inside else text[i:])
    return "".join(out)


def run_call(tools: list[Tool], call: dict[str, Any], *, max_result_chars: int = 40000) -> Hop:
    """Perform one `tool_calls` entry as the server shaped it. A tool the turn does not have,
    arguments that are not JSON, or a tool that raises all come back to the model as text --
    the model reads the error and chooses again; the turn does not fail on it."""
    fn = call.get("function") or {}
    name = str(fn.get("name") or "")
    raw = fn.get("arguments")
    t0 = time.monotonic()
    try:
        args = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
    except Exception as exc:  # noqa: BLE001
        return Hop(name, {"raw": str(raw)[:200]}, f"error: the arguments were not a JSON object ({exc!s:.100})",
                   time.monotonic() - t0, error=True)
    tool = next((t for t in tools if t.name == name), None)
    if tool is None:
        return Hop(name, args, f"error: no tool named {name!r}; the tools are "
                   + ", ".join(t.name for t in tools), time.monotonic() - t0, error=True)
    try:
        out = tool.run(args)
    except Exception as exc:  # noqa: BLE001
        return Hop(name, args, f"error: {exc!s:.400}", time.monotonic() - t0, error=True)
    text = str(out if out is not None else "")
    if len(text) > max_result_chars:
        text = text[:max_result_chars] + f"\n...(+{len(text) - max_result_chars} chars cut)"
    return Hop(name, args, text or "(no output)", time.monotonic() - t0)
