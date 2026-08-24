"""Every relative link in the repository's Markdown resolves (CONTRIBUTING: "the link checker
finds nothing broken"): the file exists and, into a Markdown file, the anchor is one of its
headings, slugged as GitHub does. The website is `mkdocs build --strict`'s to check."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _prose(text: str) -> list[str]:
    out, fence = [], False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            out.append(ln)
    return out


def _slug(heading: str) -> str:
    h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading.strip().lower()).replace("`", "").replace("*", "")
    return re.sub(r"[^\w\- ]", "", h).replace(" ", "-")


def _anchors(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path not in cache:
        seen: dict[str, int] = {}
        out: set[str] = set()
        for ln in path.read_text().splitlines():
            m = re.match(r"^#+\s+(.*)$", ln)
            if m:
                s = _slug(m.group(1))
                out.add(s if s not in seen else f"{s}-{seen[s]}")
                seen[s] = seen.get(s, 0) + 1
            out |= set(re.findall(r'<a (?:name|id)="([^"]+)"', ln))
        cache[path] = out
    return cache[path]


def test_every_relative_link_in_the_markdown_resolves():
    tracked = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    if not tracked:
        return                                      # not a git checkout (a pip install): nothing to check
    cache: dict[Path, set[str]] = {}
    broken = []
    for name in tracked:
        if name.startswith("website/"):
            continue
        src = ROOT / name
        for ln in _prose(src.read_text()):
            for m in re.finditer(r"\]\((<[^>]+>|[^)\s]+)\)", ln):
                url = m.group(1).strip("<>")
                if re.match(r"^[a-z]+:", url):
                    continue
                path, _, frag = url.partition("#")
                target = Path(os.path.normpath(src.parent / path)) if path else src
                if not target.exists():
                    broken.append(f"{name}: {url} (no such file)")
                elif frag and target.suffix == ".md" and frag not in _anchors(target, cache):
                    broken.append(f"{name}: {url} (no such heading)")
    assert not broken, "\n".join(broken)
