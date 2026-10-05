"""What the admin hides of the agents' and tools' stderr (D850): lines a pattern matches -- a piece of
text, or a `/regular expression/` -- left out of what the pages show (a turn, the live tree,
Insights), with a count in their place. The record keeps every line; only the showing is masked."""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["Masks", "check"]


def check(patterns: list[str]) -> list[str]:
    """The patterns as kept: stripped, empty ones dropped; a regular expression that does not compile refused."""
    out = []
    for p in patterns:
        p = str(p).strip()
        if not p:
            continue
        if len(p) > 2 and p.startswith("/") and p.endswith("/"):
            try:
                re.compile(p[1:-1])
            except re.error as exc:
                raise ValueError(f"{p}: not a regular expression ({exc})") from exc
        out.append(p)
    return out[:100]


class Masks:
    def __init__(self, patterns: list[str] | None) -> None:
        self.res = [re.compile(p[1:-1]) if len(p) > 2 and p.startswith("/") and p.endswith("/") else re.compile(re.escape(p))
                    for p in (patterns or [])]

    def __bool__(self) -> bool:
        return bool(self.res)

    def text(self, s: Any) -> Any:
        """`s` without the lines a pattern matches, and how many went."""
        if not self.res or not isinstance(s, str) or not s:
            return s
        lines = s.split("\n")
        kept = [ln for ln in lines if not any(r.search(ln) for r in self.res)]
        gone = len(lines) - len(kept)
        return s if not gone else "\n".join(kept) + f"\n[{gone} line(s) hidden by the admin's masks]"

    def fields(self, doc: Any) -> Any:
        """A document with every field named like stderr (or an error) masked, at any depth."""
        if not self.res:
            return doc
        if isinstance(doc, dict):
            return {k: (self.text(v) if isinstance(v, str) and ("stderr" in k or k in ("error", "last_error", "why"))
                        else self.fields(v)) for k, v in doc.items()}
        if isinstance(doc, list):
            return [self.fields(x) for x in doc]
        return doc

    def body(self, text: str) -> str:
        """A JSON body masked the same way (the live tree)."""
        if not self.res:
            return text
        try:
            return json.dumps(self.fields(json.loads(text)))
        except ValueError:
            return text
