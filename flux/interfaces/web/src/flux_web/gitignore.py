"""A loop's `.gitignore` files, as git reads them (D703), for what the Files tab and the
configurator show: the patterns of each `.gitignore` apply below its folder, a deeper one after
a shallower one, the last matching pattern deciding; `!` re-includes, a trailing `/` matches
folders only, a `/` elsewhere anchors the pattern to its folder, `*` and `?` stay within one
name, `**` crosses folders. Nothing under an ignored folder is re-included, as in git."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

__all__ = ["Ignores"]


def _regex(pattern: str) -> str:
    out, i = [], 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("/.*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j < 0:
                out.append(re.escape(c))
                i += 1
            else:
                body = pattern[i + 1:j].replace("\\", "\\\\")
                out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
                i = j + 1
        elif c == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
        else:
            out.append(re.escape(c))
            i += 1
    return "".join(out)


@lru_cache(maxsize=512)
def _rules(text: str) -> tuple[tuple[re.Pattern[str], bool, bool], ...]:
    """(pattern, negated, folders only) per line of a .gitignore."""
    rules = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        neg = line.startswith("!")
        if neg:
            line = line[1:]
        if line.startswith("\\"):
            line = line[1:]
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        anchored = "/" in line
        line = line.lstrip("/")
        rx = ("" if anchored else "(?:.*/)?") + _regex(line)
        rules.append((re.compile(rx + r"\Z"), neg, dir_only))
    return tuple(rules)


class Ignores:
    """What the `.gitignore` files under `root` ignore; `.git` always."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._files: dict[str, tuple] = {}

    def _rules_of(self, folder: str) -> tuple:
        if folder not in self._files:
            p = self.root / folder / ".gitignore" if folder else self.root / ".gitignore"
            try:
                text = p.read_text(errors="replace") if p.is_file() else ""
            except OSError:
                text = ""
            self._files[folder] = _rules(text)
        return self._files[folder]

    def _decide(self, parts: list[str], is_dir: bool) -> bool:
        ignored = False
        for depth in range(len(parts)):                     # each .gitignore from the root down to the item's folder
            base = "/".join(parts[:depth])
            rel = "/".join(parts[depth:])
            for rx, neg, dir_only in self._rules_of(base):
                if dir_only and not is_dir:
                    continue
                if rx.match(rel):
                    ignored = not neg
        return ignored

    def ignored(self, rel: str, is_dir: bool = False) -> bool:
        parts = [p for p in str(rel).strip("/").split("/") if p]
        if not parts:
            return False
        if ".git" in parts:
            return True
        for k in range(1, len(parts)):                       # under an ignored folder: ignored, whatever follows
            if self._decide(parts[:k], True):
                return True
        return self._decide(parts, is_dir)

    @staticmethod
    def hidden(rel: str) -> bool:
        """Never shown, whatever is asked: a repository's own folder."""
        return ".git" in str(rel).strip("/").split("/")
