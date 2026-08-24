"""Skills (D588): the folder format coding agents already know -- `SKILL.md` (YAML front matter
with `name` and `description`, then the instructions) plus any files it brings -- so one skill
serves every half of the loop:

    a CODING AGENT (generating, or authoring the problem) finds it where it looks for skills:
        the loop copies it into the agent's work directory under `.claude/skills/<name>/`
        (Claude Code; OpenCode reads it too), `.opencode/skills/<name>/` and
        `.agents/skills/<name>/` (Codex) -- the agent loads it itself when it applies;
    the loop's MODEL reads an index in every prompt's static part (name: when to use it) and
    loads a skill's instructions, or one of its files, with the `skill` tool inside its turn
    -- progressive, as the agents do; with tools off, the instructions are in the prompt.

A document says `skills: [path, ...]` (beside the document; a folder of skill folders is all
of them); `flux task run --skill DIR` and `flux ask --skill DIR` add more.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

__all__ = ["AGENT_DIRS", "Skill", "SkillError", "install", "load_skills", "skill_index", "skill_text"]

#: Where each agent looks for project skills, relative to its work directory.
AGENT_DIRS = (".claude/skills", ".opencode/skills", ".agents/skills")

_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)


class SkillError(ValueError):
    """A folder that is not a skill: no SKILL.md, no name, no description."""


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path                      # the skill's folder
    body: str                       # SKILL.md after the front matter

    def files(self) -> list[str]:
        """The files the skill brings besides SKILL.md, relative to its folder."""
        return sorted(str(p.relative_to(self.path)) for p in self.path.rglob("*")
                      if p.is_file() and p.name != "SKILL.md" and "__pycache__" not in p.parts)


def _read(folder: Path) -> Skill:
    md = folder / "SKILL.md"
    if not md.is_file():
        raise SkillError(f"{folder}: no SKILL.md -- a skill is a folder with SKILL.md (name, description, instructions)")
    text = md.read_text()
    m = _FRONT.match(text)
    meta: dict = {}
    if m:
        import yaml

        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as exc:
            raise SkillError(f"{md}: the front matter is not YAML: {exc}") from exc
    name = str(meta.get("name") or folder.name).strip()
    description = str(meta.get("description") or "").strip()
    if not description:
        raise SkillError(f"{md}: no `description` in the front matter -- it is what says when the skill applies")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise SkillError(f"{md}: the name {name!r} is not a folder name (letters, digits, - _ .)")
    return Skill(name, description, folder.resolve(), (m.group(2) if m else text).strip())


def load_skills(paths: list[str | Path], base: Path | None = None) -> list[Skill]:
    """The skills at these paths: a skill folder, or a folder of skill folders. Relative
    paths are read beside `base`; two skills of one name is an error."""
    out: dict[str, Skill] = {}
    for raw in paths:
        p = Path(raw).expanduser()
        if not p.is_absolute() and base is not None:
            p = Path(base) / p
        if not p.is_dir():
            raise SkillError(f"{raw}: not a folder (a skill is a folder with SKILL.md)")
        found = [p] if (p / "SKILL.md").is_file() else sorted(d for d in p.iterdir() if (d / "SKILL.md").is_file())
        if not found:
            raise SkillError(f"{raw}: no SKILL.md in it or in any folder directly under it")
        for folder in found:
            s = _read(folder)
            if s.name in out and out[s.name].path != s.path:
                raise SkillError(f"two skills are named {s.name!r}: {out[s.name].path} and {s.path}")
            out[s.name] = s
    return list(out.values())


#: Skills whose instructions together fit in this many characters go in the prompt whole: a
#: model offered only an index tends not to load the skill that applies (D588). Larger
#: libraries keep the index.
INLINE_CHARS = 12000


def skill_index(skills: list[Skill], *, tools: bool = True, inline_chars: int = INLINE_CHARS) -> str:
    """The static prompt block: every skill's instructions whole when they are small (or when
    there is no `skill` tool), else every skill's name and when it applies, for the tool."""
    if not skills:
        return ""
    whole = sum(len(s.body) for s in skills) <= inline_chars
    if tools and not whole:
        head = ("SKILLS -- instructions for particular jobs, loaded on demand. You do NOT know what a skill says "
                "until you load it: when one applies to what you are doing, call the `skill` tool with its name "
                "FIRST, before you write anything, and follow what it says.")
        return head + "\n" + "\n".join(f"  - {s.name}: {s.description}" for s in skills)
    parts = ["SKILLS -- instructions for particular jobs; follow the one that applies:"]
    for s in skills:
        files = s.files()
        parts.append(f"### {s.name} -- {s.description}\n{s.body}"
                     + (f"\n(its files, read with the `skill` tool and `file`: {', '.join(files)})" if files and tools else ""))
    return "\n\n".join(parts)


def skill_text(skills: list[Skill], name: str, file: str = "") -> str:
    """What the `skill` tool returns: a skill's instructions and the files it brings, or one
    of those files' text."""
    s = next((k for k in skills if k.name == name), None)
    if s is None:
        return f"no skill named {name!r}; the skills are: {', '.join(k.name for k in skills) or 'none'}"
    if file:
        target = (s.path / file).resolve()
        if s.path not in target.parents or not target.is_file():
            return f"{name} has no file {file!r}; its files: {', '.join(s.files()) or 'none'}"
        from .document import read_input

        return read_input(target)
    files = s.files()
    return (f"SKILL {s.name}: {s.description}\n\n{s.body}"
            + (f"\n\nFILES it brings (read one with the `skill` tool and `file`): {', '.join(files)}" if files else ""))


def install(skills: list[Skill], workdir: Path) -> list[Path]:
    """The skills copied where every coding agent looks for project skills in `workdir`."""
    placed: list[Path] = []
    for rel in AGENT_DIRS:
        for s in skills:
            dst = Path(workdir) / rel / s.name
            if dst.resolve() == s.path:
                continue
            shutil.copytree(s.path, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
            placed.append(dst)
    return placed
