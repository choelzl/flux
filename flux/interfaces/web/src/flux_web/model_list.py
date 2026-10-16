"""D1161: the models a settings group's endpoint lists, for the Models form's "From endpoint" -- the
endpoint and key the group's runs would use in that scope: the server's (Admin), or a user's own
endpoint with their own key, else the server's they inherit (none for an external user, D734).
The key stays here: the page gets names or a short reason, never the key."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

__all__ = ["endpoint_of", "listed"]

#: The machine's keys of Flux's own model, as the CLI reads them (flux_llm.remote_api_key).
_MACHINE_KEYS = ("OPENROUTER_API_KEY", "FLUX_REMOTE_API_KEY")


def _machine(external: bool) -> dict[str, str]:
    from .runs import _MODEL_VARS

    env = dict(os.environ)
    return {k: v for k, v in env.items() if not k.startswith(_MODEL_VARS)} if external else env


def _machine_key(machine: dict[str, str]) -> str:
    key = next((machine[k] for k in _MACHINE_KEYS if machine.get(k)), "")
    path = machine.get("FLUX_REMOTE_API_KEY_FILE")
    if not key and path:
        try:
            key = open(os.path.expanduser(path)).read().strip().splitlines()[0].strip()
        except (OSError, IndexError):
            key = ""
    return key


def _flux_model(server: dict[str, str], mine: dict[str, str], machine: dict[str, str], external: bool) -> dict[str, str]:
    """Flux's own model as a run of this scope gets it (runs.run_env): one's own endpoint with one's own
    key alone; else the server's (its settings over the machine's) under one's own key."""
    if mine.get("FLUX_REMOTE_BASE_URL"):
        return {"base": mine["FLUX_REMOTE_BASE_URL"], "key": mine.get("FLUX_REMOTE_API_KEY", "")}
    if external:
        return {"base": "", "key": ""}
    return {"base": server.get("FLUX_REMOTE_BASE_URL") or machine.get("FLUX_REMOTE_BASE_URL", ""),
            "key": mine.get("FLUX_REMOTE_API_KEY") or server.get("FLUX_REMOTE_API_KEY") or _machine_key(machine)}


def endpoint_of(store: Any, user: Any, group: str) -> dict[str, Any]:
    """{base, key, auth, anthropic} for `group` ("model" or an agent's name); `user` None: the server's."""
    from .agents import run_settings, visible
    from .runs import agent_scopes

    external = bool(user and user.external)
    server = {} if external else store.server_settings(reveal=True)
    mine = store.settings(user, reveal=True) if user else {}
    machine = _machine(external)
    if group == "model":
        flux = _flux_model(server, mine, machine, external)
        return {"base": flux["base"], "key": flux["key"], "auth": "", "anthropic": False}
    a = visible(store).get(group)
    if a is None:
        raise KeyError(group)
    scopes = {} if external else {"global": {n: x["value"] for n, x in store.env("global", reveal=True).items()}}
    if user:
        scopes[f"user:{user.id}"] = {n: x["value"] for n, x in store.env(f"user:{user.id}", reveal=True).items()}
    # D1170: an agent's own endpoint alone, never Flux's model's
    _own, eff = run_settings(a, *agent_scopes(store, user, a, machine, server, mine, scopes))
    val = lambda f: str(eff.fields.get(f, {}).get("value") or "")   # noqa: E731
    return {"base": val("endpoint"), "key": val("key"), "auth": val("auth"), "anthropic": a.kind == "claude"}


def _reachable(store: Any, base: str) -> str:
    """Why the server does not ask `base`, or "": the sandbox's allowlist when the admin limits the network
    and leaves the endpoints off it (D698) -- a run of this scope could not reach it either."""
    from flux_cli.sandbox_proxy import allowed

    from .runs import sandbox_config

    cfg = sandbox_config(store)
    if cfg.get("network") != "allowlist" or cfg.get("endpoints", True):
        return ""
    host = urlsplit(base).hostname or ""
    return "" if allowed(host, list(cfg.get("allow") or [])) else f"{host} is not on the sandbox's allowlist"


def listed(store: Any, user: Any, group: str, timeout_s: float = 10.0) -> dict[str, Any]:
    """{models, error}: the names the scope's endpoint for `group` lists, or why none."""
    from flux_llm.openai_compat import list_models

    ep = endpoint_of(store, user, group)
    if not ep["base"]:
        return {"models": [], "error": "No endpoint set"}
    why = _reachable(store, ep["base"])
    if why:
        return {"models": [], "error": why}
    try:
        models = list_models(ep["base"], key=ep["key"] or None, auth=ep["auth"] or None, anthropic=ep["anthropic"], timeout_s=timeout_s)
    except ValueError as exc:
        err = str(exc)
        for secret in (ep["key"], ep["auth"]):
            if secret:
                err = err.replace(secret, "…")                # a server that echoes the key: never on the page
        return {"models": [], "error": err[:240]}
    return {"models": models, "error": "" if models else "Endpoint lists no models"}
