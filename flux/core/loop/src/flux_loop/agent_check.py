"""Is a coding agent ready for whoever runs it (D751): its program, a login or a key, its own
status, and -- asked for -- one short answer through the very command a loop runs it with.

`flux agent test <agent> [--live]` (a preset, or an agent a server adds, D807) runs this in the sandbox with the user's own
home and settings; the web's Account › My agents and models runs it from a Test button, and a loop that
needs an agent starts only for a user whose test of it passed. `task check` says each agent a
document uses and whether it is set up (the free part: no model is asked)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

__all__ = ["AGENTS", "LOGIN_FILES", "agents_used", "check_agent", "logged_in"]

AGENTS = ("opencode", "claude", "codex")      # the kinds; a server adds agents of these kinds (D807)
#: Where each kind keeps what a login gives it, under HOME (not `.claude.json`: Claude Code
#: writes it on any start, logged in or not -- D748).
LOGIN_FILES = {"opencode": (".local/share/opencode/auth.json",), "claude": (".claude/.credentials.json",),
               "codex": (".codex/auth.json",)}
#: Or a key in the environment the web's settings give a run.
LOGIN_KEYS = {"opencode": ("OPENCODE_CONFIG_CONTENT",), "claude": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
              "codex": ("OPENAI_API_KEY",)}
#: The agent's own word on its login, free (no model asked).
STATUS = {"codex": ("login", "status"), "opencode": ("auth", "list")}
SAY = "FLUX-OK"
PROMPT = (f"This is a connection test. Reply with exactly {SAY} and nothing else. "
          "Do not run any command, do not read or write any file.")


def logged_in(home: Path) -> dict[str, bool]:
    return {a: any((home / p).is_file() and (home / p).stat().st_size > 0 for p in ps) for a, ps in LOGIN_FILES.items()}


def agents_used(task: Any) -> list[str]:
    """The coding agents a document hands work to -- `generate: {agent: …}`, a box's
    `{agent: …}`, an orchestrator's `coding` agent, the papers' digest -- by preset, in the order first named."""
    from .agent import agent_kinds, agent_spec

    found: list[str] = []

    def take(value: Any) -> None:
        try:
            tool = agent_spec(value).tool
        except (ValueError, TypeError):
            return
        if tool in agent_kinds() and tool not in found:
            found.append(tool)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "agent":
                    if isinstance(v, dict) and "coding" in v:
                        take(v["coding"])
                    else:
                        take(v)
                walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                walk(v)

    for part in (getattr(task, "generator", None), getattr(task, "flow", None), getattr(task, "roles", None)):
        walk(part or {})
    if getattr(task, "digest_by", None) is not None:          # D771: the papers' digest by an agent
        take(task.digest_by)
    return found


def _program(name: str, env: dict[str, str], kind: str = "") -> str:
    exe = os.path.expanduser(env.get(f"FLUX_{name.upper()}_BIN") or kind or name)
    return exe if "/" in exe and Path(exe).exists() else (shutil.which(exe, path=env.get("PATH")) or "")


def check_agent(name: str, *, live: bool = False, env: dict[str, str] | None = None, timeout_s: float = 180.0) -> dict[str, Any]:
    """{"agent", "ok", "steps": [{"step", "ok", "said"}], "version", "seconds"}: each step said,
    the first that fails ends it (a live answer is asked only of an agent that is there and
    logged in)."""
    env = dict(os.environ if env is None else env)
    t0 = time.monotonic()
    steps: list[dict[str, Any]] = []
    out: dict[str, Any] = {"agent": name, "ok": False, "steps": steps, "version": ""}

    def step(what: str, ok: bool, said: str) -> bool:
        steps.append({"step": what, "ok": bool(ok), "said": said[:600]})
        return ok

    def done() -> dict[str, Any]:
        out["ok"] = all(s["ok"] for s in steps)
        out["seconds"] = round(time.monotonic() - t0, 1)
        return out

    from .agent import agent_kinds

    kinds = agent_kinds(env)
    if name not in kinds:
        step("agent", False, f"one of {', '.join(kinds)}")
        return done()
    kind = kinds[name]
    exe = _program(name, env, kind)
    if not step("program", bool(exe), exe or f"{name} is not on PATH (an admin sets FLUX_{name.upper()}_BIN)"):
        return done()
    try:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)
        out["version"] = (v.stdout or v.stderr).strip().splitlines()[-1][:120] if (v.stdout or v.stderr).strip() else ""
    except (OSError, subprocess.TimeoutExpired):
        pass
    home = Path(env.get("HOME") or Path.home())
    known = [*LOGIN_FILES[kind], *[f.strip() for f in env.get(f"FLUX_{name.upper()}_LOGIN_FILES", "").split(",") if f.strip()]]
    files = [p for p in known if (home / p).is_file() and (home / p).stat().st_size > 0]   # D760: a build of its own keeps its own
    try:                                                     # D807: its own set's keys too
        own = json.loads(env.get(f"FLUX_{name.upper()}_ENV") or "{}")
    except ValueError:
        own = {}
    mine = {**env, **(own if isinstance(own, dict) else {})}
    keys = [k for k in LOGIN_KEYS[kind] if mine.get(k)]
    if not step("login", bool(files or keys),
                (f"logged in ({', '.join(files)})" if files else f"a key in the settings ({', '.join(keys)})") if files or keys
                else "not logged in: Account › My agents and models (its tab), or a key in the settings"):
        return done()
    if kind in STATUS:
        try:
            r = subprocess.run([exe, *STATUS[kind]], capture_output=True, text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)
            said = " ".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\[[0-9;]*m", "", r.stdout + r.stderr).split())[:300]
            # OpenCode's list is informative only: a provider Flux configures is not in it
            if not step("status", r.returncode == 0 or kind == "opencode", said or f"exit {r.returncode}"):
                return done()
        except (OSError, subprocess.TimeoutExpired) as exc:
            step("status", kind == "opencode", f"{exc}"[:300])
    if live:
        from .agent import agent_spec, run_turn

        spec = agent_spec({"preset": name, "timeout_s": timeout_s, "probe": False})
        work = Path(tempfile.mkdtemp(prefix=f"flux-agent-test-{name}-"))
        subs = {"prompt": PROMPT, "prompt_file": str(work / "BRIEF.md"), "artifact": str(work / "answer.txt"),
                "workdir": str(work), "part": "test", "name": "test", "home": str(work)}
        (work / "BRIEF.md").write_text(PROMPT + "\n")
        t1 = time.monotonic()
        turn = run_turn(spec, spec.argv, subs, workdir=work)
        said = (turn.text or "").strip()
        if not said:
            said = f"exit {turn.rc}: " + " ".join(((turn.stderr or "") or (turn.stdout or ""))[-400:].split())
        step("answer", turn.ok and SAY in (turn.text or ""), f"{said[:300]} ({time.monotonic() - t1:.0f} s"
             + (f", {turn.about}" if turn.about else "") + ")")
        shutil.rmtree(work, ignore_errors=True)
    return done()
