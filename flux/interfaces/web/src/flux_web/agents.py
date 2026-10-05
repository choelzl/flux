"""The coding agents a server runs (D807): OpenCode, Claude Code and Codex, and any an admin adds
-- an OpenCode of a company's own beside the plain one -- each a NAME of a KIND (the preset it
runs as), with its own program, login, arguments and settings.

An agent is offered to users only where its program is found and runnable; Admin › Agents lists
them all. Each agent has its own settings -- its kind's endpoint, model and key, and variables of
its own -- the admin's for the server and a user's own, which a run hands to that agent alone
(`FLUX_<NAME>_ENV`, read by `flux_loop.agent`); a variable for every agent at once is an ordinary
variable (Models and variables, Account, a loop's Settings).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

__all__ = ["BUILTIN", "KINDS", "Agent", "found", "registry", "run_settings", "setting_keys", "version", "visible"]

#: What each kind is: its label, its login command, where its login is kept under HOME, the
#: secret a login prints instead of keeping (D748), and what its settings mean.
KINDS: dict[str, dict[str, Any]] = {
    "opencode": {"label": "OpenCode", "login": "opencode auth login", "credentials": (".local/share/opencode/auth.json",),
                 "printed": None, "endpoint": "Endpoint URL (OpenAI-compatible)",
                 "hint": "A provider of its own for this agent; empty: its own configuration "
                         "(the built-in OpenCode: Flux's own model's endpoint, model and key)."},
    "claude": {"label": "Claude Code", "login": "claude setup-token", "credentials": (".claude/.credentials.json",),
               "printed": re.compile(r"sk-ant-oat\d+-[A-Za-z0-9_\-]{20,}"), "endpoint": "Endpoint URL (ANTHROPIC_BASE_URL)",
               "hint": "Empty: its own login and model."},
    "codex": {"label": "Codex", "login": "codex login", "credentials": (".codex/auth.json",), "printed": None,
              "endpoint": "Endpoint URL (OPENAI_BASE_URL)", "hint": "Empty: its own login and model."},
}
BUILTIN = tuple(KINDS)
#: An added agent's name: lower case, as a document names it (`generate: nga`).
NAME = r"[a-z][a-z0-9_]{0,23}"
#: Words a document's box takes that an agent's name cannot be.
_WORDS = {"model", "rules", "off", "on", "tools", "given", "objectives", "human", "mined", "none", "llm", "agent", "flux",
          "sweep", "command", "catalog"}


@dataclass
class Agent:
    name: str
    kind: str
    label: str = ""
    bin: str = ""
    login: str = ""
    args: str = ""
    home: list[str] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    login_files: list[str] = field(default_factory=list)

    @property
    def builtin(self) -> bool:
        return self.name in BUILTIN

    @property
    def up(self) -> str:
        return self.name.upper()

    def keys(self) -> dict[str, tuple[str, ...]]:
        """Its settings' names: endpoint and model public, the key (and a Claude login's token) secret."""
        secret = (f"FLUX_{self.up}_API_KEY", *((f"FLUX_{self.up}_OAUTH_TOKEN",) if self.kind == "claude" else ()))
        return {"public": (f"FLUX_{self.up}_BASE_URL", f"FLUX_{self.up}_MODEL"), "secret": secret}

    def labels(self) -> dict[str, str]:
        k = self.keys()
        out = {k["public"][0]: KINDS[self.kind]["endpoint"], k["public"][1]: "Model", k["secret"][0]: "Key"}
        if self.kind == "claude":
            out[k["secret"][1]] = "Login token (its login saves it)"
        return out

    def login_command(self) -> list[str]:
        cmd = shlex.split(self.login or KINDS[self.kind]["login"])
        if self.bin and cmd and cmd[0] == self.kind:          # the admin's program for it (D705)
            cmd[0] = self.bin
        return cmd

    def stored(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "bin": self.bin, "login": self.login, "args": self.args,
                "home": self.home, "hosts": self.hosts, "login_files": self.login_files}


def registry(store: Any) -> dict[str, Agent]:
    """Every agent: the three built-in ones first, then those an admin added, by name."""
    raw = store.server_get("agents") or {}
    out: dict[str, Agent] = {}
    for name in (*BUILTIN, *sorted(n for n in raw if n not in BUILTIN)):
        r = raw.get(name) or {}
        kind = name if name in BUILTIN else r.get("kind")
        if kind not in KINDS:
            continue
        out[name] = Agent(name, kind, str(r.get("label") or (KINDS[name]["label"] if name in BUILTIN else name)),
                          str(r.get("bin") or ""), str(r.get("login") or ""), str(r.get("args") or ""),
                          list(r.get("home") or []), list(r.get("hosts") or []), list(r.get("login_files") or []))
    return out


def check_new(store: Any, name: str, kind: str) -> str:
    name = str(name or "").strip()
    if not re.fullmatch(NAME, name):
        raise ValueError(f"{name!r}: a name is lower-case letters, digits and _, starting with a letter (at most 24)")
    if name in _WORDS:
        raise ValueError(f"{name} is a word a document's box takes; name the agent otherwise")
    if name in registry(store):
        raise ValueError(f"there is an agent named {name} already")
    if kind not in KINDS:
        raise ValueError(f"its kind is one of {', '.join(KINDS)}")
    return name


def _path_dirs(store: Any) -> list[str]:
    from .runs import login_path

    cfg = store.server_get("sandbox") or {}
    return [d for d in (*(cfg.get("path") or []), *(login_path() if cfg.get("login_path") else []),
                        *os.environ.get("PATH", "").split(os.pathsep)) if d]


def found(agent: Agent, store: Any) -> str:
    """The program the server runs for `agent`, where it is found and runnable, else "": the admin's,
    else this machine's FLUX_<NAME>_BIN, else (a built-in agent) its name on the runs' PATH. An
    added agent is its program: without one it is not offered."""
    exe = os.path.expanduser(agent.bin or os.environ.get(f"FLUX_{agent.up}_BIN") or (agent.kind if agent.builtin else ""))
    if not exe:
        return ""
    if "/" in exe:
        return exe if os.path.isfile(exe) and os.access(exe, os.X_OK) else ""
    return shutil.which(exe, path=os.pathsep.join(_path_dirs(store))) or ""


_VERSIONS: dict[tuple[str, float], str] = {}


def version(program: str) -> str:
    """Its `--version`, asked once per program as it is on disk."""
    if not program:
        return ""
    try:
        key = (program, os.stat(program).st_mtime)
    except OSError:
        key = (program, 0.0)
    if key not in _VERSIONS:
        try:
            r = subprocess.run([program, "--version"], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
            lines = (r.stdout or r.stderr).strip().splitlines()
            _VERSIONS[key] = lines[-1][:80] if lines else ""
        except (OSError, subprocess.TimeoutExpired):
            _VERSIONS[key] = ""
    return _VERSIONS[key]


def visible(store: Any) -> dict[str, Agent]:
    """The agents users are offered: those whose program is found and runnable."""
    return {n: a for n, a in registry(store).items() if found(a, store)}


def setting_keys(store: Any) -> dict[str, tuple[str, ...]]:
    """Every agent's settings' names, public and secret."""
    agents = registry(store).values()
    return {"public": tuple(k for a in agents for k in a.keys()["public"]),
            "secret": tuple(k for a in agents for k in a.keys()["secret"])}


def run_settings(agent: Agent, server: dict[str, str], mine: dict[str, str], flux: dict[str, str],
                 variables: dict[str, str], base_env: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """What a run hands `agent` (D807): (its own variables, extra arguments). `server` and `mine`
    are the settings (revealed); a user who names their own endpoint gets none of the server's
    settings of this agent; a Claude login's token is only ever the user's own (D748). `flux`:
    Flux's own model settings, the built-in OpenCode's when it has none of its own (D696).
    `variables`: the agent's own, the server's then the user's."""
    k = agent.keys()
    names = (*k["public"], *k["secret"])
    base_key, model_key, api_key = k["public"][0], k["public"][1], k["secret"][0]
    if mine.get(base_key):
        vals = {n: mine[n] for n in names if mine.get(n)}
    else:
        vals = {**{n: server[n] for n in names if server.get(n)}, **{n: mine[n] for n in names if mine.get(n)}}
    token = mine.get(f"FLUX_{agent.up}_OAUTH_TOKEN")
    own: dict[str, str] = {}
    args: list[str] = []
    base, model, key = vals.get(base_key), vals.get(model_key), vals.get(api_key)
    if agent.kind == "opencode":
        if agent.name == "opencode" and not base:
            base, model, key = flux.get("FLUX_REMOTE_BASE_URL"), model or flux.get("FLUX_REMOTE_MODEL"), key or flux.get("FLUX_REMOTE_API_KEY")
        if base and model:
            options: dict[str, Any] = {"baseURL": base.rstrip("/"), "timeout": 1800000}
            if key:
                own["FLUX_AGENT_API_KEY"] = key
                options["apiKey"] = "{env:FLUX_AGENT_API_KEY}"
            try:
                have = json.loads(base_env.get("OPENCODE_CONFIG_CONTENT") or "{}")
            except ValueError:
                have = {}
            have.setdefault("provider", {})["flux"] = {"npm": "@ai-sdk/openai-compatible", "name": "Flux (web settings)",
                                                       "options": options, "models": {model: {"name": model}}}
            have["model"] = f"flux/{model}"
            own["OPENCODE_CONFIG_CONTENT"] = json.dumps(have)
    else:
        pre = "ANTHROPIC" if agent.kind == "claude" else "OPENAI"
        if base:
            own[f"{pre}_BASE_URL"] = base
        if key:
            own[f"{pre}_API_KEY"] = key
        if token and agent.kind == "claude":
            own["CLAUDE_CODE_OAUTH_TOKEN"] = token
        if model:
            args += ["--model", model]
    own.update(variables)
    return own, args
