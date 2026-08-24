"""Compacting what a turn carries (D549, D550). A turn with tools grows by a round's thinking
and its results every hop, and the reply's room shrinks by as much (D473). Past a share of
the window the OLDER rounds are compacted in place, two ways:

- the rules half, `compact_conversation`: their thinking dropped (the model reads its past
  reasoning nowhere) and each result reduced to what carries information -- its first lines,
  the lines with a number or a verdict word, its last lines (`digest_result`; an answer or an
  error sits at the END of a result, a head alone lost it, D550);
- the model half, `older_rounds_prompt` + `replace_older_rounds`: the proposer writes what
  the older rounds ESTABLISHED, numbers exact, and that note replaces them at the end of the
  turn's prompt (after the cached prefix, so the server's cache still holds).

The last round stays whole either way: it is what the model asked for last.
"""

from __future__ import annotations

import json
import re

__all__ = ["MARK", "compact_conversation", "digest_result", "older_rounds_prompt", "replace_older_rounds"]

MARK = "…(compacted: "
NOTE = "\n\nEARLIER ROUNDS OF THIS TURN, CONDENSED (their calls and results are not repeated below):\n"
_TELLING = re.compile(r"\d|PASS|FAIL|refus|error|score|over\b|Traceback|exception", re.I)


def digest_result(text: str, chars: int = 300) -> str:
    """`text` within `chars`: its first two lines, then the lines that tell something (a
    number, a verdict word) in order while they fit, then its last two lines, gaps marked.
    The whole text when it fits already."""
    if len(text) <= chars:
        return text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 4:
        head = text[:max(40, chars // 2)]
        return head + f"\n{MARK}{len(text) - len(head)} more chars were read in this round)"
    keep = {0, 1, len(lines) - 2, len(lines) - 1}
    size = sum(len(lines[i]) + 1 for i in keep)
    for i, ln in enumerate(lines):
        if i in keep or not _TELLING.search(ln):
            continue
        if size + len(ln) + 1 > chars:
            break
        keep.add(i)
        size += len(ln) + 1
    out: list[str] = []
    gap = 0
    for i, ln in enumerate(lines):
        if i in keep:
            if gap:
                out.append(f"  … ({gap} line(s) not shown)")
                gap = 0
            out.append(ln)
        else:
            gap += 1
    out.append(f"{MARK}{len(text)} chars were read in this round, {len(keep)} of {len(lines)} lines kept)")
    return "\n".join(out)


def compact_conversation(messages: list[dict], *, keep_rounds: int = 1, digest_chars: int = 300) -> int:
    """The rules half, in place. Rounds before the last `keep_rounds` lose their
    `reasoning_content`, and their tool results are digested to `digest_chars`. Returns how
    many characters went; 0 when there is nothing older than the kept rounds."""
    cutoff = _cutoff(messages, keep_rounds)
    if cutoff is None:
        return 0
    removed = 0
    for m in messages[:cutoff]:
        if m.get("role") == "assistant" and m.get("reasoning_content"):
            removed += len(m["reasoning_content"])
            del m["reasoning_content"]
        elif m.get("role") == "tool":
            text = str(m.get("content") or "")
            if len(text) > digest_chars and MARK not in text:
                m["content"] = digest_result(text, digest_chars)
                removed += len(text) - len(m["content"])
    return removed


def older_rounds_prompt(messages: list[dict], *, keep_rounds: int = 1, limit: int = 24000) -> str | None:
    """What to ask the model when the older rounds are to be condensed: the rounds before the
    last `keep_rounds`, each call with its arguments and its result, and the question. None
    when there is nothing older."""
    cutoff = _cutoff(messages, keep_rounds)
    if cutoff is None:
        return None
    parts: list[str] = []
    n = 0
    for m in messages[1:cutoff]:
        if m.get("role") == "assistant":
            for c in m.get("tool_calls") or []:
                fn = c.get("function") or {}
                args = fn.get("arguments") or "{}"
                n += 1
                parts.append(f"CALL {n}: {fn.get('name', '?')}({args[:1500]})")
            if m.get("content"):
                parts.append(f"SAID: {str(m['content'])[:1500]}")
        elif m.get("role") == "tool":
            parts.append(f"RESULT: {str(m.get('content') or '')[:4000]}")
    body = "\n".join(parts)
    if len(body) > limit:
        body = body[:limit] + f"\n…({len(body) - limit} more chars)"
    return ("Below are the earlier rounds of a tool-using turn: the calls made and what came back. "
            "Write what they ESTABLISHED, as facts a designer continues from: every number, name, "
            "verdict and error message exact, nothing that was not measured, no advice. Under 1500 "
            "characters. Reply with the facts only.\n\n" + body)


def replace_older_rounds(messages: list[dict], digest: str, *, keep_rounds: int = 1) -> int:
    """The model half's replacement, in place: the older rounds go, and `digest` is appended
    to the turn's prompt (messages[0]) under a heading -- after the cached prefix, so the
    server's prefix cache still holds. Returns how many characters went."""
    cutoff = _cutoff(messages, keep_rounds)
    if cutoff is None or not digest.strip():
        return 0
    before = sum(len(str(m.get("content") or "")) + len(json.dumps(m.get("tool_calls") or []))
                 for m in messages[1:cutoff])
    head = str(messages[0].get("content") or "")
    marker = head.find(NOTE)
    head = head[:marker] if marker >= 0 else head          # a second condensation replaces the first
    messages[0]["content"] = head + NOTE + digest.strip()
    del messages[1:cutoff]
    return before


def _cutoff(messages: list[dict], keep_rounds: int) -> int | None:
    rounds = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if len(rounds) <= keep_rounds:
        return None
    return rounds[-keep_rounds]
