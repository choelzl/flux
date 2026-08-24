"""Tools a model may call inside one turn (D505): the OpenAI `tools` protocol, as data.

A `Tool` is a name, the line the model reads, the JSON schema of its arguments and the callable
that performs it. A proposer that supports tools sends them, runs each call, returns the result
as a `tool` message and asks again, until the model answers in `content` or the hop budget is
spent. The hops are kept in `Reply.hops` for the record and the task pane.

A `response_format` beside tools makes the model skip the tools and guess, so a schema is
applied only on the last hop, with `tool_choice: "none"`.
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
    compact: str = "rules"              # D550: "rules" (results digested) or "llm" (a model-written note
                                        # replaces the older rounds)
    hop_share: float | None = 0.5       # D544: share of the context window a tool-calling round may write;
                                        # the answering round, or an unknown window, keeps the turn's cap


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


# A call is a tag that begins a line (after `<tool_call>` at most); a `<function=...>` quoted
# mid-line or in a code fence is text, not a call (D535)
_TEXT_CALL = re.compile(r"^[ \t]*(?:<tool_call>\s*)?<function=([A-Za-z_][\w-]*)>(.*?)</function>", re.S | re.M)
_TEXT_PARAM = re.compile(r"<parameter=([A-Za-z_]\w*)>\s*(.*?)\s*</parameter>", re.S)


def text_tool_calls(content: str) -> list[dict[str, Any]]:
    """Tool calls the model wrote as text in `content`, in the chat template's syntax
    (`<tool_call><function=check><parameter=...>`) (D506). Returned in the server's
    `tool_calls` shape so the same loop performs them; an unterminated call (output cap hit)
    is closed by the end of the text."""
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
    """Perform one `tool_calls` entry as the server shaped it. An unknown tool, non-JSON
    arguments or a raising tool come back to the model as text; the turn does not fail."""
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
