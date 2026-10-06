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

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

__all__ = ["BUILTIN", "KINDS", "Agent", "found", "registry", "run_prices", "run_settings", "setting_keys", "version", "visible"]

#: What each kind is: its label, its login command, where its login is kept under HOME, the
#: secret a login prints instead of keeping (D748), and what its settings mean.
KINDS: dict[str, dict[str, Any]] = {
    "opencode": {"label": "OpenCode", "login": "opencode auth login", "credentials": (".local/share/opencode/auth.json",),
                 "printed": None, "endpoint": "Endpoint URL",
                 "hint": "Empty: its own configuration; the built-in OpenCode uses Flux's model."},
    "claude": {"label": "Claude Code", "login": "claude setup-token", "credentials": (".claude/.credentials.json",),
               "printed": re.compile(r"sk-ant-oat\d+-[A-Za-z0-9_\-]{20,}"), "endpoint": "Endpoint URL",
               "hint": "Empty: its own login and model."},
    "codex": {"label": "Codex", "login": "codex login", "credentials": (".codex/auth.json",), "printed": None,
              "endpoint": "Endpoint URL", "hint": "Empty: its own login and model."},
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
        return {"public": (f"FLUX_{self.up}_BASE_URL", f"FLUX_{self.up}_MODEL", self.timeout(), *self.prices()), "secret": secret}

    def timeout(self) -> str:
        """Its turn's time limit's name (D893): seconds, the server's or a user's own; a document's
        `timeout_s` wins over both."""
        return f"FLUX_{self.up}_TIMEOUT_S"

    def prices(self) -> tuple[str, str]:
        """Its prices' names (D835): USD per million tokens in and out."""
        return f"FLUX_{self.up}_PRICE_IN", f"FLUX_{self.up}_PRICE_OUT"

    def labels(self) -> dict[str, str]:
        k = self.keys()
        pin, pout = self.prices()
        out = {k["public"][0]: KINDS[self.kind]["endpoint"], k["public"][1]: "Model", self.timeout(): "Seconds per turn",
               k["secret"][0]: "Key", pin: "Price in", pout: "Price out"}
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


def known_version(program: str) -> str | None:
    """D921: its `--version` when asked already (as it is on disk), else None -- nothing run."""
    if not program:
        return ""
    try:
        key = (program, os.stat(program).st_mtime)
    except OSError:
        key = (program, 0.0)
    return _VERSIONS.get(key)


def visible(store: Any) -> dict[str, Agent]:
    """The agents users are offered: those whose program is found and runnable."""
    return {n: a for n, a in registry(store).items() if found(a, store)}


def setting_keys(store: Any) -> dict[str, tuple[str, ...]]:
    """Every agent's settings' names, public and secret."""
    agents = registry(store).values()
    return {"public": tuple(k for a in agents for k in a.keys()["public"]),
            "secret": tuple(k for a in agents for k in a.keys()["secret"])}


def _resolved(agent: Agent, server: dict[str, str], mine: dict[str, str]) -> dict[str, str]:
    """The agent's settings for a run: a user who names their own endpoint gets only theirs, prices
    too; else the server's under theirs -- but the server's prices, which only an endpoint of one's
    own lets a user set (D835)."""
    k = agent.keys()
    names, base_key, prices = (*k["public"], *k["secret"]), k["public"][0], agent.prices()
    if mine.get(base_key):
        return {n: mine[n] for n in names if mine.get(n)}
    return {**{n: server[n] for n in names if server.get(n)}, **{n: mine[n] for n in names if mine.get(n) and n not in prices}}


def run_prices(agent: Agent, server: dict[str, str], mine: dict[str, str], flux: dict[str, str]) -> dict[str, str]:
    """`FLUX_<NAME>_PRICE_IN`/`_OUT` as a run gets them (D835): the agent's own, and the built-in
    OpenCode on Flux's own model (no endpoint of its own) Flux's model's prices, unless it has its own."""
    vals = _resolved(agent, server, mine)
    pin, pout = agent.prices()
    if agent.name == "opencode" and not vals.get(agent.keys()["public"][0]) and not (vals.get(pin) or vals.get(pout)):
        vals = {pin: flux.get("FLUX_REMOTE_PRICE_IN", ""), pout: flux.get("FLUX_REMOTE_PRICE_OUT", "")}
    return {n: vals[n] for n in (pin, pout) if vals.get(n)}


def run_timeout(agent: Agent, server: dict[str, str], mine: dict[str, str]) -> str:
    """`FLUX_<NAME>_TIMEOUT_S` as a run gets it (D893): the user's own, else the server's, whoever's
    endpoint the agent uses -- a time limit is not the endpoint's. "" when neither is a number over 0."""
    for v in (mine.get(agent.timeout()), server.get(agent.timeout())):
        try:
            if v and float(v) > 0:
                return str(float(v)).removesuffix(".0")
        except ValueError:
            continue
    return ""


#: D922/D925: a layer's word -- what the Account and Admin pages show as a field's source.
FROM_SERVER = "From Server"


def agent_layers(agent: Agent, *, machine: dict[str, str], server: dict[str, str], mine: dict[str, str],
                 server_vars: dict[str, str], my_vars: dict[str, str], loop_vars: dict[str, str] | None = None,
                 flux: dict[str, str] | None = None) -> list[tuple[str, dict[str, str]]]:
    """The scopes an agent's configuration comes from (D922), lowest first: the server's process
    (its environment, flux.env included), the server's settings and variables, the user's, the
    loop's. `server_vars`/`my_vars`: the variables for every agent, then this agent's own, merged.
    A Claude login's token is only ever the user's own (D748). `flux`: Flux's own model, the
    built-in OpenCode's when nothing names its endpoint (D696) -- the lowest layer."""
    names = (*agent.keys()["public"][:2], *agent.keys()["secret"])
    token = f"FLUX_{agent.up}_OAUTH_TOKEN"
    out = [(f"{FROM_SERVER} (its environment)", dict(machine)),
           (f"{FROM_SERVER} (its settings)", {**server_vars, **{n: server[n] for n in names if server.get(n) and n != token}}),
           ("yours", {**my_vars, **{n: mine[n] for n in names if mine.get(n)}})]
    if loop_vars:
        out.append(("this loop's", dict(loop_vars)))
    if agent.name == "opencode" and flux and flux.get("FLUX_REMOTE_BASE_URL"):
        from flux_loop.agent_env import resolve

        if "endpoint" not in resolve(agent.name, agent.kind, out).fields:
            out.insert(0, ("Flux's model", {"FLUX_OPENCODE_BASE_URL": flux["FLUX_REMOTE_BASE_URL"],
                                            "FLUX_OPENCODE_MODEL": flux.get("FLUX_REMOTE_MODEL", ""),
                                            "FLUX_OPENCODE_API_KEY": flux.get("FLUX_REMOTE_API_KEY", "")}))
    return out


def run_settings(agent: Agent, layers: list[tuple[str, dict[str, str]]], variables: dict[str, str],
                 agents: tuple[str, ...] = ()) -> tuple[dict[str, str], Any]:
    """What a run hands `agent` (D807): (its own variables, the resolved configuration). D922: the
    one core resolver (`flux_loop.agent_env`) over `layers` -- its fields as its kind reads them,
    the model as FLUX_<NAME>_MODEL (the turn makes it `--model`); `variables`: the agent's own, the
    server's then the user's, under the resolved fields; a field's native not used goes."""
    from flux_loop.agent_env import aliases, resolve

    eff = resolve(agent.name, agent.kind, layers, agents=agents)
    own = {**variables}
    for n in eff.unset:
        own.pop(n, None)
    own.update(eff.env)
    if eff.args:
        own[aliases(agent.name, agent.kind)["model"]] = eff.fields["model"]["value"]
    return own, eff
