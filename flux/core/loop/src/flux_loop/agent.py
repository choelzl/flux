"""A CODING AGENT as the generator (D575, Cedric): Claude Code, Codex CLI, OpenCode or any
terminal tool that takes a brief and writes a file. The loop hands it a work directory, a
brief (`PROMPT.md`: the same design prompt the model half gets, the prior artifact and the
failure on a repair, and where to write) and a time limit; the agent uses its own model,
tools and skills; the loop reads the artifact it wrote -- or parses the one it printed --
and runs its own build, fast check and judge around it, exactly as around a model's reply.

    generate: {agent: claude}                                  # a preset
    generate: {agent: {command: [my-agent, "{prompt_file}", "{artifact}"], timeout_s: 900}}

Substitutions in a command: `{prompt}` (the brief's text), `{prompt_file}` (its path),
`{artifact}` (where to write), `{workdir}`, `{part}`, `{name}`, `{python}`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

__all__ = ["PRESETS", "agent_argv", "agent_brief", "run_agent"]

#: The agents this repository knows how to call headless. Each takes the brief as an
#: argument and may edit files in the working directory without asking.
PRESETS: dict[str, tuple[str, ...]] = {
    "claude": ("claude", "-p", "{prompt}", "--permission-mode", "acceptEdits"),
    "codex": ("codex", "exec", "--full-auto", "{prompt}"),
    "opencode": ("opencode", "run", "{prompt}"),
}


def agent_argv(spec: Any) -> tuple[str, tuple[str, ...], float]:
    """(tool, argv, timeout_s) from the document's `agent:` value: a preset's name, or an
    object with `preset` or `command` and an optional `timeout_s`."""
    if isinstance(spec, str):
        if spec not in PRESETS:
            raise ValueError(f"agent {spec!r} is not a preset; presets: {', '.join(PRESETS)}; or give `command: [...]`")
        return spec, PRESETS[spec], 1800.0
    if not isinstance(spec, dict):
        raise ValueError("agent: a preset's name or {preset|command, timeout_s}")
    timeout = float(spec.get("timeout_s") or 1800.0)
    if spec.get("command"):
        argv = tuple(str(t) for t in spec["command"])
        if not argv:
            raise ValueError("agent.command is a non-empty list of strings")
        first = next((t for t in argv if not t.startswith("{")), argv[0])       # `{python} my-agent.py`: the script
        return str(spec.get("name") or Path(first).name), argv, timeout
    preset = str(spec.get("preset") or "")
    if preset not in PRESETS:
        raise ValueError(f"agent needs `command: [...]` or a `preset` among {', '.join(PRESETS)}")
    return preset, PRESETS[preset], timeout


def agent_brief(*, body: str, prefix: str, artifact: Path, workdir: Path, language: str, part: str,
                prior: str | None, failure: str) -> str:
    """The brief an agent reads: the static prefix (contract, knowledge), the design or the
    repair prompt, then what the loop expects of a terminal tool."""
    # the model half's reply shape (JSON with the artifact) is not how an agent answers: it writes the file
    prefix = "\n\n".join(p for p in prefix.split("\n\n") if not p.lstrip().startswith("REPLY SHAPE"))
    parts = [p for p in (prefix.strip(), body.strip()) if p]
    if prior:
        parts.append(f"THE LAST DRAFT (refused: {failure.strip()[:2000] or 'see above'}):\n```\n{prior}\n```")
    parts.append(
        f"HOW TO ANSWER. You are a coding agent working in `{workdir}`. Write the complete {language} "
        f"artifact for `{part}` to `{artifact}` (create the file; that file is what gets built and tested, "
        f"nothing else is read). You may run any tool in this directory to check your work first. "
        f"When the file is written, reply with one line saying so.")
    return "\n\n".join(parts) + "\n"


def run_agent(argv: tuple[str, ...], subs: dict[str, str], *, workdir: Path, timeout_s: float
              ) -> tuple[bool, int, str, str]:
    """The agent run once: (ok, returncode, stdout, stderr). A missing binary or a timeout
    is a refusal with its own words, never a crash."""
    cmd = [t.format(**subs) for t in argv]
    if shutil.which(cmd[0]) is None and not Path(cmd[0]).is_file():
        return False, 127, "", f"{cmd[0]} is not on PATH (the coding agent named by the document)"
    try:
        # stdin closed on purpose: an agent that reads a piped prompt from stdin (OpenCode does)
        # would otherwise wait forever on the loop's inherited socket -- measured: ten minutes to
        # the timeout with the model never asked
        r = subprocess.run(cmd, cwd=str(workdir), capture_output=True, text=True, timeout=timeout_s,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return False, 124, "", f"the agent ran past {timeout_s:.0f}s and was stopped"
    return r.returncode == 0, r.returncode, r.stdout or "", r.stderr or ""


def missing_agent(spec: Any) -> list[str]:
    """The agent's binary when it is not on PATH, for `tools_missing`."""
    try:
        _tool, argv, _t = agent_argv(spec)
    except ValueError:
        return []
    head = argv[0].format(python=sys.executable, prompt="", prompt_file="", artifact="", workdir="", part="", name="")
    return [] if (shutil.which(head) or Path(head).is_file()) else [head]
