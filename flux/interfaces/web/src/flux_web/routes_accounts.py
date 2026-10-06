"""Accounts (D683): logging in and out, users and their invitation links (D818), one's password,
the model settings -- a user's and the server's (D696) -- and the variables of the server and of a
user (D697); what happened for a user since they last looked (D702)."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from .models import Login, NewUser, UserChange, FileText, EnvVar, Settings
from .store import SESSION_DAYS, User


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """The accounts' routes, on the guards and the store `ctx` holds (D888)."""
    store, secure_cookie, COOKIE = ctx.store, ctx.secure_cookie, ctx.cookie
    user_of, admin_of, fail = ctx.user_of, ctx.admin_of, ctx.fail
    _env_list, _set_env = ctx.env_list, ctx.set_env

    # ---- accounts
    @app.post("/api/login")
    def login(body: Login, response: Response, request: Request) -> dict[str, Any]:
        # D702: the address a failure counts against -- behind the TLS proxy (--secure-cookie), its forward
        address = (request.headers.get("x-forwarded-for", "").split(",")[0].strip() if secure_cookie else "") \
            or (request.client.host if request.client else "")
        token = store.login(body.name, body.password, address)
        if token is None:
            store.audit(body.name.strip(), "login refused")
            time.sleep(0.5)
            raise HTTPException(401, "wrong name or password (five failures from one place lock the name there for ten minutes)")
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure_cookie,
                            max_age=SESSION_DAYS * 86400, path="/")
        u = store.user(name=body.name)
        store.audit(u.name, "login")
        return {"name": u.name, "role": u.role}

    @app.post("/api/logout")
    def logout(request: Request, response: Response) -> dict[str, str]:
        if request.cookies.get(COOKIE):
            store.logout(request.cookies[COOKIE])
        response.delete_cookie(COOKIE, path="/")
        return {"ok": "logged out"}

    @app.get("/api/me")
    def me(user: User = Depends(user_of)) -> dict[str, Any]:
        return {"name": user.name, "role": user.role}

    @app.get("/api/users")
    def users(_a: User = Depends(admin_of)) -> list[dict[str, Any]]:
        return [{"name": u.name, "role": u.role, "disabled": u.disabled, "pending": store.pending(u.name)} for u in store.users()]

    @app.post("/api/users")
    def add_user(body: NewUser, a: User = Depends(admin_of)) -> dict[str, Any]:
        """A user (D818: with no password, an invitation to set it -- the link's token, for the admin to send)."""
        try:
            u = store.add_user(body.name, body.password, body.role)
        except ValueError as exc:
            raise fail(exc) from exc
        store.audit(a.name, "add user", body.name)
        if body.password is not None:
            return {"ok": u.name}
        token, kind = store.invite(u.name)
        store.audit(a.name, "invite user", u.name)
        return {"ok": u.name, "token": token, "kind": kind}

    @app.post("/api/users/{name}/link")
    def user_link(name: str, a: User = Depends(admin_of)) -> dict[str, Any]:
        """A new link for the user (D818): an invitation while their password is not set, else a reset;
        the earlier one stops working. Their password stays as it is until the link is used."""
        try:
            token, kind = store.invite(name)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        store.audit(a.name, "invite user" if kind == "invite" else "password reset link", name)
        return {"token": token, "kind": kind}

    @app.get("/api/invite/{token}")
    def invite_info(token: str) -> dict[str, Any]:
        """For everyone (D818): whose link it is and what for, while it opens."""
        got = store.invite_of(token)
        if got is None:
            raise HTTPException(404, "this link has been used or has expired: ask an admin for a new one")
        return got

    @app.post("/api/invite/{token}")
    def invite_use(token: str, body: FileText, response: Response) -> dict[str, Any]:
        """The password set from a link (D818), the user logged in, every other session of theirs ended."""
        try:
            u, session = store.use_invite(token, body.text)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        response.set_cookie(COOKIE, session, httponly=True, samesite="strict", secure=secure_cookie,
                            max_age=SESSION_DAYS * 86400, path="/")
        store.audit(u.name, "password set from a link")
        return {"name": u.name, "role": u.role}

    @app.patch("/api/users/{name}")
    def change_user(name: str, body: UserChange, a: User = Depends(admin_of)) -> dict[str, str]:
        if store.user(name=name) is None:
            raise HTTPException(404, "no such user")
        if name == a.name and (body.disabled or (body.role and body.role != "admin")):
            raise HTTPException(400, "an admin does not disable or demote themselves")
        try:
            store.set_user(name, password=body.password, disabled=body.disabled, role=body.role)
        except ValueError as exc:
            raise fail(exc) from exc
        store.audit(a.name, "change user", f"{name}: " + ", ".join(k for k, v in body.model_dump().items() if v is not None))
        return {"ok": name}

    @app.post("/api/password")
    def own_password(body: FileText, user: User = Depends(user_of)) -> dict[str, str]:
        try:
            store.set_user(user.name, password=body.text)
        except ValueError as exc:
            raise fail(exc) from exc
        store.audit(user.name, "change password")
        return {"ok": "changed"}

    def _groups(agents: dict[str, Any]) -> list[dict[str, Any]]:
        """Flux's own settings, then a group per agent offered (D807: its kind's endpoint, model
        and key, each its own) -- a tab each (D817: no other providers' tab)."""
        from .agents import KINDS
        from .store import GROUPS

        def static(k: str, g: dict[str, Any]) -> dict[str, Any]:
            return {"id": k, "label": g["label"], "tab": g.get("tab") or g["label"], "public": list(g["public"]), "secret": list(g["secret"]),
                    "endpoint": g["endpoint"], "hint": g.get("hint", ""), "prices": list(g.get("prices", ()))}

        per = [{"id": a.name, "label": f"{a.label} ({a.kind})" if not a.builtin else a.label, "tab": a.label,
                "public": list(a.keys()["public"]), "secret": list(a.keys()["secret"]), "endpoint": a.keys()["public"][0],
                "hint": KINDS[a.kind]["hint"], "labels": a.labels(), "agent": a.name, "prices": list(a.prices())} for a in agents.values()]
        return [*(static(k, g) for k, g in GROUPS.items()), *per]

    def _keys_of(groups: list[dict[str, Any]]) -> dict[str, list[str]]:
        return {"public": [k for g in groups for k in g["public"]], "secret": [k for g in groups for k in g["secret"]]}

    def _inherited_list(scope: str) -> list[dict[str, Any]]:
        """D925: a scope's variables as a user is shown the server's -- each name, secret or not, its
        origin; never its value (the run gets it, the page says From Server)."""
        return [{"name": k, "secret": v["secret"], "origin": "server"} for k, v in sorted(store.env(scope).items())]

    ctx.inherited_list = _inherited_list

    def _from_server(user: User, agents: dict[str, Any]) -> dict[str, bool]:
        """D925: each setting a user's runs would take from the server -- its settings or its own
        environment -- while the user names no endpoint of their own in that group (the page gates
        on that as it changes): explicit origin, no value. An agent's fields by the core resolver
        over the server's scopes alone (D922)."""
        import os

        from flux_loop.agent_env import aliases

        from .agents import run_settings
        from .runs import agent_scopes
        from .store import GROUPS

        if user.external:                                  # D734: nothing of the server's
            return {}
        server = store.server_settings(reveal=True)
        machine = dict(os.environ)
        out: dict[str, bool] = {}
        model_keys = {"FLUX_REMOTE_API_KEY": ("FLUX_REMOTE_API_KEY", "FLUX_REMOTE_API_KEY_FILE", "OPENROUTER_API_KEY")}
        for g in GROUPS.values():
            for k in (*g["public"], *g["secret"]):
                if server.get(k) or any(machine.get(x) for x in model_keys.get(k, (k,))):
                    out[k] = True
        mine = store.settings(user)
        flux = {} if mine.get("FLUX_REMOTE_BASE_URL") else {k: server.get(k) or machine.get(k, "") for k in GROUPS["model"]["public"]
                                                              + GROUPS["model"]["secret"]}
        every = {"global": {n: x["value"] for n, x in store.env("global", reveal=True).items()}}
        for a in agents.values():
            _own, eff = run_settings(a, *agent_scopes(store, None, a, machine, server, {}, flux, every))
            names = aliases(a.name, a.kind)
            for f in eff.fields:
                k = names.get("key" if f == "auth" else f)
                if k and f != "token":                     # D748: a login's token is only ever the user's own
                    out[k] = True
            if server.get(a.timeout()):
                out[a.timeout()] = True
            pin, pout = a.prices()
            for k, fk in ((pin, "FLUX_REMOTE_PRICE_IN"), (pout, "FLUX_REMOTE_PRICE_OUT")):
                if server.get(k) or (a.name == "opencode" and "endpoint" in eff.fields and eff.fields["endpoint"]["source"] == "Flux's model"
                                     and (server.get(fk) or machine.get(fk))):
                    out[k] = True
        return out

    @app.get("/api/settings")
    def get_settings(user: User = Depends(user_of)) -> dict[str, Any]:
        """The user's model settings, and which of them their runs take from the server (D696, D925):
        only that -- `inherited` -- and `overridden` where their own value displaces one; never a
        server value. Each agent's variables, the user's and the server's names (D807)."""
        from .agents import visible

        agents = visible(store)
        groups = _groups(agents)
        inherited = _from_server(user, agents)
        values = store.settings(user)
        env = {n: {"mine": _env_list(f"agent:{n}:user:{user.id}"), "server": [] if user.external else _inherited_list(f"agent:{n}")}
               for n in agents}
        return {"values": values, "inherited": inherited, "overridden": {k: True for k in inherited if values.get(k)},
                "groups": groups, **_keys_of(groups), "agent_env": env, "external": user.external}

    @app.get("/api/admin/settings")
    def get_server_settings(_a: User = Depends(admin_of)) -> dict[str, Any]:
        from .agents import visible

        agents = visible(store)
        groups = _groups(agents)
        return {"values": store.server_settings(), "groups": groups, **_keys_of(groups),
                "agent_env": {n: _env_list(f"agent:{n}") for n in agents}}

    @app.put("/api/admin/settings")
    def put_server_settings(body: Settings, a: User = Depends(admin_of)) -> dict[str, Any]:
        try:
            for k, v in body.values.items():
                store.set_server_setting(k, v)
        except ValueError as exc:
            raise fail(exc) from exc
        store.audit(a.name, "server settings", ", ".join(sorted(body.values)))
        return {"values": store.server_settings()}

    @app.put("/api/settings")
    def put_settings(body: Settings, user: User = Depends(user_of)) -> dict[str, Any]:
        try:
            for k, v in body.values.items():
                store.set_setting(user, k, v)
        except ValueError as exc:
            raise fail(exc) from exc
        store.audit(user.name, "settings", ", ".join(sorted(body.values)))
        return {"values": store.settings(user)}

    # ---- environment variables (D697): the server's (admins), a user's, a loop's
    @app.get("/api/admin/env")
    def global_env(_a: User = Depends(admin_of)) -> list[dict[str, Any]]:
        return _env_list("global")

    @app.put("/api/admin/env")
    def put_global_env(body: EnvVar, a: User = Depends(admin_of)) -> list[dict[str, Any]]:
        return _set_env("global", body, a, "the server")

    @app.get("/api/env")
    def my_env(user: User = Depends(user_of)) -> dict[str, Any]:
        """The user's variables, and the server's they come after (names only, D925)."""
        return {"mine": _env_list(f"user:{user.id}"), "server": [] if user.external else _inherited_list("global")}   # D734; D925: names, From Server

    @app.put("/api/env")
    def put_my_env(body: EnvVar, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        return _set_env(f"user:{user.id}", body, user, user.name)

    @app.get("/api/notices")
    def notices(user: User = Depends(user_of)) -> list[dict[str, Any]]:
        """What happened for this user since they last looked (D702): a loop shared, unshared, left."""
        return store.take_notices(user.name)
