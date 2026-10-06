"""A coding agent's endpoint, key, model and login token, resolved once (D922) -- for the CLI, the
web's runs, authoring, Check/Test and the agent's turn alike.

Flux's names for them are aliases, per agent NAME, mapped by the agent's KIND to what the agent
itself reads; the agent's own (native) names stay a transparent interface beside them:

    FLUX_<NAME>_BASE_URL     claude: ANTHROPIC_BASE_URL   codex: OPENAI_BASE_URL   opencode: a provider
    FLUX_<NAME>_API_KEY      claude: ANTHROPIC_API_KEY    codex: OPENAI_API_KEY    in OPENCODE_CONFIG_CONTENT
    FLUX_<NAME>_MODEL        claude, codex: --model <it>                           (endpoint + model, key optional)
    FLUX_<NAME>_OAUTH_TOKEN  claude: CLAUDE_CODE_OAUTH_TOKEN (a Claude-kind agent's alone)

whatever the source: a web setting, flux.env, the shell, the server's inherited environment.

Scopes, lowest first, are LAYERS: [(label, {name: value})]; the highest that sets a field wins.
In one layer, an alias and its native name that differ are a conflict, said, and the alias (the
dedicated field) wins. An endpoint and its credentials go together (A8): a key, token or model of
a layer below the endpoint's is not used with it -- a personal endpoint never pairs with an
inherited machine key."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["ALIASES", "CREDENTIALS", "Effective", "aliases", "config_warnings", "identity", "resolve"]

#: A field's alias suffix (FLUX_<NAME>_<suffix>); None: a native name only.
ALIASES = {"endpoint": "BASE_URL", "key": "API_KEY", "model": "MODEL", "token": "OAUTH_TOKEN"}
#: Each kind's fields and the native name each is read by (None: no variable -- an argument or
#: a provider of its configuration).
NATIVE: dict[str, dict[str, str | None]] = {
    "claude": {"endpoint": "ANTHROPIC_BASE_URL", "key": "ANTHROPIC_API_KEY", "auth": "ANTHROPIC_AUTH_TOKEN",
               "token": "CLAUDE_CODE_OAUTH_TOKEN", "model": None},
    "codex": {"endpoint": "OPENAI_BASE_URL", "key": "OPENAI_API_KEY", "model": None},
    "opencode": {"endpoint": None, "key": None, "model": None},
}
#: What authenticates: never paired with an endpoint of a higher layer than its own (A8).
CREDENTIALS = ("key", "auth", "token")
_FIELD_ALIAS = re.compile(r"FLUX_([A-Z][A-Z0-9_]*?)_(BASE_URL|API_KEY|MODEL|OAUTH_TOKEN)")


def aliases(name: str, kind: str) -> dict[str, str]:
    """{field: FLUX_<NAME>_<suffix>} for the fields `kind` takes."""
    return {f: f"FLUX_{name.upper()}_{s}" for f, s in ALIASES.items() if f in NATIVE.get(kind, {})}


@dataclass
class Effective:
    """The resolved agent configuration: `env` (native variables to set), `unset` (native names a
    lower layer had that are not used), `args` (--model), and per field its winner's source --
    {field: {"source", "name", "layer", "value"}} (a credential's value never kept here)."""
    name: str
    kind: str
    env: dict[str, str] = field(default_factory=dict)
    unset: list[str] = field(default_factory=list)
    args: tuple[str, ...] = ()
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    conflicts: list[str] = field(default_factory=list)
    unused: list[str] = field(default_factory=list)
    digests: dict[str, str] = field(default_factory=dict)
    agents: tuple[str, ...] = ()

    def apply(self, env: dict[str, str]) -> dict[str, str]:
        """`env` as the agent gets it: every agent's aliases gone (its own translated, the others'
        not its business), its fields' natives as resolved."""
        ups = {a.upper() for a in (*self.agents, self.name)}
        out = {k: v for k, v in env.items() if not ((m := _FIELD_ALIAS.fullmatch(k)) and m.group(1) in ups)}
        for k in self.unset:
            out.pop(k, None)
        out.update(self.env)
        return out

    def public(self) -> dict[str, Any]:
        """What may be shown (D922): each field's source and spelling, an endpoint's or a model's value,
        a credential only as set; the conflicts and what is unused."""
        return {"fields": {f: {k: v for k, v in x.items() if k != "value" or f not in CREDENTIALS} for f, x in self.fields.items()},
                "conflicts": list(self.conflicts), "unused": list(self.unused), "model_args": bool(self.args)}


def resolve(name: str, kind: str, layers: list[tuple[str, dict[str, str]]], *, base_config: str | None = None,
            agents: tuple[str, ...] = ()) -> Effective:
    """`name` (an agent, of `kind`) resolved over `layers`, lowest first. `base_config`: the
    OPENCODE_CONFIG_CONTENT the provider is merged into (else the layers' own). `agents`: every
    agent's name, whose aliases the agent does not get."""
    eff = Effective(name, kind, agents=tuple(agents))
    natives = NATIVE.get(kind)
    if natives is None:
        return eff
    al = aliases(name, kind)
    won: dict[str, tuple[int, str, str, str]] = {}               # field -> (layer, label, spelling, value)
    for i, (label, vals) in enumerate(layers):
        for f in natives:
            a, n = al.get(f), natives[f]
            va, vn = (vals.get(a) or "") if a else "", (vals.get(n) or "") if n else ""
            if va and vn and va != vn:
                eff.conflicts.append(f"{label}: {a} and {n} differ; {a} is used")
            if va or vn:
                won[f] = (i, label, a if va else str(n), va or vn)
        for k in vals:                                           # an alias of this agent its kind does not take
            m = _FIELD_ALIAS.fullmatch(k)
            if m and m.group(1) == name.upper() and k not in al.values() and vals.get(k):
                eff.unused.append(f"{label}: {k} -- a {kind} agent takes no {ALIASES_WORD.get(m.group(2), m.group(2))}")
    ep = won.get("endpoint")
    if ep:                                                       # A8: what goes with an endpoint is of its layer or above
        for f in (*CREDENTIALS, "model"):
            if f in won and won[f][0] < ep[0]:
                eff.unused.append(f"{won[f][1]}: {won[f][2]} is not used with the endpoint from {ep[1]}")
                del won[f]
    for f, (i, label, spelling, value) in won.items():
        eff.fields[f] = {"source": label, "name": spelling, "layer": i, "value": value}
        if f in CREDENTIALS:
            eff.digests[f] = hashlib.sha256(f"{f}\0{value}".encode()).hexdigest()[:16]
    # every native of a field: unset, then set as resolved
    eff.unset = [n for n in natives.values() if n]
    for f, n in natives.items():
        if n and f in eff.fields:
            eff.env[n] = eff.fields[f]["value"]
    model = eff.fields.get("model", {}).get("value")
    if kind == "opencode":
        base, key = eff.fields.get("endpoint", {}).get("value"), eff.fields.get("key", {}).get("value")
        # its whole configuration set in a scope above the endpoint's wins, as any native over a lower field
        over = max((i for i, (_l, v) in enumerate(layers) if v.get("OPENCODE_CONFIG_CONTENT")), default=-1)
        if base and model and base_config is None and over > eff.fields["endpoint"]["layer"]:
            eff.unused.append(f"{eff.fields['endpoint']['source']}: {eff.fields['endpoint']['name']} -- OPENCODE_CONFIG_CONTENT "
                              f"of {layers[over][0]} wins")
        elif base and model:
            options: dict[str, Any] = {"baseURL": base.rstrip("/"), "timeout": 1800000}
            if key:
                eff.env["FLUX_AGENT_API_KEY"] = key
                options["apiKey"] = "{env:FLUX_AGENT_API_KEY}"
            have_text = base_config if base_config is not None else next(
                (v.get("OPENCODE_CONFIG_CONTENT") for _l, v in reversed(layers) if v.get("OPENCODE_CONFIG_CONTENT")), "")
            try:
                have = json.loads(have_text or "{}")
            except ValueError:
                have = {}
            have = have if isinstance(have, dict) else {}
            have.setdefault("provider", {})["flux"] = {"npm": "@ai-sdk/openai-compatible", "name": "Flux (web settings)",
                                                       "options": options, "models": {model: {"name": model}}}
            have["model"] = f"flux/{model}"
            eff.env["OPENCODE_CONFIG_CONTENT"] = json.dumps(have)
        elif (base or key or model) and not (base and model):
            said = ", ".join(al[f] for f in ("endpoint", "key", "model") if f in eff.fields)
            eff.unused.append(f"{said}: OpenCode's provider needs an endpoint and a model -- its own configuration is used")
    elif model:
        eff.args = ("--model", model)
    return eff


ALIASES_WORD = {"OAUTH_TOKEN": "login token", "BASE_URL": "endpoint", "API_KEY": "key", "MODEL": "model"}


def identity(eff: Effective, program: str = "") -> str:
    """What a Test tested (D923): a hash of the agent, its program, its endpoint and model, each
    credential's source and a digest of it -- never the secret -- and OpenCode's provider."""
    stuff = {"name": eff.name, "kind": eff.kind, "program": program,
             "fields": {f: [x["source"], x["name"], eff.digests.get(f) or x["value"]] for f, x in sorted(eff.fields.items())}}
    oc = eff.env.get("OPENCODE_CONFIG_CONTENT")
    if oc:
        stuff["opencode"] = hashlib.sha256(oc.encode()).hexdigest()
    return hashlib.sha256(json.dumps(stuff, sort_keys=True).encode()).hexdigest()[:16]


def config_warnings(names: dict[str, str], agents: dict[str, str]) -> list[str]:
    """flux.env's lines (`names`: name -> value) Flux recognizes but will not use (D922): an alias
    of no agent here, a field the agent's kind does not take, an OpenCode provider half set."""
    out = []
    for k in sorted(names):
        m = _FIELD_ALIAS.fullmatch(k) or re.fullmatch(r"FLUX_([A-Z][A-Z0-9_]*?)_(TIMEOUT_S|BIN|ARGS|PRICE_IN|PRICE_OUT)", k)
        if not m or m.group(1) in ("REMOTE", "LLM", "SANDBOX", "LOCAL"):
            continue
        agent = m.group(1).lower()
        if agent not in agents:
            out.append(f"{k}: {agent} is not an agent here (agents: {', '.join(agents)}) -- not used")
            continue
        if m.group(2) in ("BASE_URL", "API_KEY", "MODEL", "OAUTH_TOKEN") and f"FLUX_{m.group(1)}_{m.group(2)}" not in aliases(agent, agents[agent]).values():
            out.append(f"{k}: a {agents[agent]} agent takes no {ALIASES_WORD[m.group(2)]} -- not used")
    for agent, kind in agents.items():
        if kind == "opencode":
            up = agent.upper()
            have = [s for s in ("BASE_URL", "MODEL") if names.get(f"FLUX_{up}_{s}")]
            if len(have) == 1:
                out.append(f"FLUX_{up}_{have[0]}: OpenCode's provider needs both FLUX_{up}_BASE_URL and FLUX_{up}_MODEL -- not used")
    return out
