"""Locating module headers in SystemVerilog source (docs/decisions.md D179).

One implementation, two callers: `synth.py`'s unpacked-array guard and
`flux_protocols.conform`'s port checker. It exists as a shared module because both previously used
the same regex and therefore had the same bug — a parameter block matched as `#\\s*\\([^)]*\\)`
stops at the first `)`, so an ordinary parameter default like `parameter KEEP = (WIDTH>8)`
truncates the match and takes the port list with it. Both callers then saw *no ports at all* and
reported the confident, structured, wrong answer that follows from an empty list.

Scanning balanced parentheses instead is barely more code and cannot be fooled that way.
"""

from __future__ import annotations

import re

_MODULE_START_RE = re.compile(r"\bmodule\s+(\w+)")
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def strip_comments(source: str) -> str:
    return _BLOCK_COMMENT_RE.sub(" ", _LINE_COMMENT_RE.sub(" ", source))


def module_headers(source: str) -> list[tuple[str, str]]:
    """`(module_name, port_header_text)` for every module in `source`.

    The port header is the text between the module's own parentheses, with any `#(...)` parameter
    block skipped. Comments are stripped first, so a commented-out port is not seen.
    """
    text = strip_comments(source)
    found: list[tuple[str, str]] = []
    for match in _MODULE_START_RE.finditer(text):
        position = match.end()
        block = _balanced_group(text, position)
        if block is None:
            continue
        # A `#(...)` parameter block, when present, comes first; the port list is the next group.
        if text[position:block[0]].lstrip().startswith("#"):
            block = _balanced_group(text, block[1])
            if block is None:
                continue
        found.append((match.group(1), text[block[0] + 1:block[1] - 1]))
    return found


def _balanced_group(text: str, start: int) -> tuple[int, int] | None:
    """The next parenthesised group at or after `start`, as `(open_index, index_after_close)`.

    `None` when the next non-space character is not `(` (allowing one leading `#`), or when the
    parentheses never balance — an unterminated header is not silently treated as an empty one.
    """
    i = start
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == "#":
        i += 1
        while i < len(text) and text[i].isspace():
            i += 1
    if i >= len(text) or text[i] != "(":
        return None
    depth, open_at = 0, i
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return open_at, i + 1
        i += 1
    return None
