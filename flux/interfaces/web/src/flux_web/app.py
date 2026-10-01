"""The web API and pages (D683). Every `/api` route but login needs a session cookie; every request
that changes something also needs the `X-Flux: 1` header, which a page of another site cannot
send (CSRF). A user sees their own applications and runs; an admin also manages users and
reads every run."""

from __future__ import annotations

import asyncio
import json
import re
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .runs import ADVANCED, HOST_RULE, RunManager, advanced, login_path, loop_files, machine_env, run_env, sandbox_env
from .store import PUBLIC_SETTINGS, SECRET_SETTINGS, SESSION_DAYS, Store, User
from .workspace import Workspace, WorkspaceError

COOKIE = "flux_session"
STATIC = Path(__file__).parent / "static"
#: The loop crafter's script, style and tool catalog (website/docs/assets), for the configurator (D686)
CRAFTER = Path(os.environ.get("FLUX_CRAFTER_ASSETS") or Path(__file__).resolve().parents[5] / "website" / "docs" / "assets")


class Login(BaseModel):
    name: str
    password: str


class NewUser(BaseModel):
    name: str
    password: str
    role: str = "user"


class UserChange(BaseModel):
    password: str | None = None
    disabled: bool | None = None
    role: str | None = None


class DocText(BaseModel):
    name: str
    filename: str = "problem.problem.yaml"
    text: str


class FileText(BaseModel):
    text: str


class RunOptions(BaseModel):
    passes: int | None = Field(default=1, ge=1, le=1000)
    screen_only: bool = False
    allow: list[str] = Field(default_factory=list)


class Stop(BaseModel):
    now: bool = False


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class DocSave(BaseModel):
    text: str = Field(max_length=2_000_000)
    kept: list[str] = Field(default_factory=list)


class Clean(BaseModel):                   # D695: the admin's controls
    what: str


class Paused(BaseModel):
    reason: str | None = Field(default=None, max_length=300)


class Limit(BaseModel):
    max_running: int | None = None


class StopAll(BaseModel):
    now: bool = False


class ShareIn(BaseModel):                 # D701
    user: str
    perm: str | None = None


class EnvVar(BaseModel):                  # D697
    name: str
    value: str | None = None
    secret: bool = False


class Advanced(BaseModel):
    sandbox: bool = True
    memory: str | None = Field(default=None, max_length=16)
    cpus: str | None = Field(default=None, max_length=8)
    pids: int | None = None
    tmp_size: str | None = Field(default=None, max_length=16)
    allow: list[str] | None = None


class SandboxConfig(BaseModel):          # D698: what every sandbox gets
    path: list[str] = Field(default_factory=list)
    login_path: bool = False
    home_ro: list[str] = Field(default_factory=list)
    home_copy: list[str] = Field(default_factory=list)
    network: str = "open"
    allow: list[str] = Field(default_factory=list)
    users_add: bool = True
    endpoints: bool = True


class Settings(BaseModel):
    values: dict[str, str | None]


def create_app(data: str | Path, *, sandbox: bool = True, secure_cookie: bool = False, max_running: int = 4) -> FastAPI:
    store = Store(data)
    runs = RunManager(store, sandbox=sandbox, max_running=max_running)
    app = FastAPI(title="Flux", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.store, app.state.runs = store, runs
    from .history import History

    history = History(store.path)                    # D699: the machine over time, in the server's database
    app.state.history = history
    app.state.sample = lambda: _sample()

    # ---- guards
    @app.middleware("http")
    async def csrf(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("x-flux") != "1":
                return JSONResponse({"detail": "missing the X-Flux header"}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.endswith("/report"):
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return resp

    def user_of(request: Request) -> User:
        u = store.session_user(request.cookies.get(COOKIE))
        if u is None:
            raise HTTPException(401, "log in")
        return u

    def admin_of(user: User = Depends(user_of)) -> User:
        if not user.admin:
            raise HTTPException(403, "admins only")
        return user

    def ws(user: User) -> Workspace:
        return Workspace(store.data, user.name)

    def access(user: User, owner: str | None, name: str | None) -> tuple[Workspace, User, str]:
        """Whose loop a call names, and what this user may do with it (D701): "owner"; "edit" or
        "watch" when the owner shared it with them; "admin" for an admin (watch, and stop)."""
        if not owner or owner.strip().lower() == user.name.lower():
            return ws(user), user, "owner"
        other = store.user(name=owner)
        if other is None:
            raise HTTPException(404, "no such user")
        perm = store.shares(other.name, name).get(user.name) if name else None
        if perm:
            return Workspace(store.data, other.name), other, perm
        if user.admin:
            return Workspace(store.data, other.name), other, "admin"
        raise HTTPException(403, "this loop is not shared with you")

    def reader(user: User, owner: str | None, name: str | None = None) -> tuple[Workspace, User]:
        """Whose loop a read names: one's own, one shared with this user, or, for an admin, any (D684, D701)."""
        w, whose, _perm = access(user, owner, name)
        return w, whose

    def editor(user: User, owner: str | None, name: str) -> tuple[Workspace, User]:
        """Whose loop a change names: one's own, or one shared with this user to edit (D701)."""
        w, whose, perm = access(user, owner, name)
        if perm not in ("owner", "edit"):
            raise HTTPException(403, "you may watch this loop, not change it")
        return w, whose

    def fail(exc: Exception) -> HTTPException:
        return HTTPException(400, str(exc))

    # ---- accounts
    @app.post("/api/login")
    def login(body: Login, response: Response) -> dict[str, Any]:
        token = store.login(body.name, body.password)
        if token is None:
            store.audit(body.name.strip(), "login refused")
            time.sleep(0.5)
            raise HTTPException(401, "wrong name or password (five failures lock the name for ten minutes)")
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
        return [{"name": u.name, "role": u.role, "disabled": u.disabled} for u in store.users()]

    @app.post("/api/users")
    def add_user(body: NewUser, a: User = Depends(admin_of)) -> dict[str, str]:
        try:
            store.add_user(body.name, body.password, body.role)
        except ValueError as exc:
            raise fail(exc) from exc
        store.audit(a.name, "add user", body.name)
        return {"ok": body.name}

    @app.patch("/api/users/{name}")
    def change_user(name: str, body: UserChange, a: User = Depends(admin_of)) -> dict[str, str]:
        if store.user(name=name) is None:
            raise HTTPException(404, "no such user")
        if name == a.name and (body.disabled or body.role == "user"):
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

    def _groups() -> list[dict[str, Any]]:
        from .store import GROUPS

        return [{"id": k, "label": g["label"], "public": list(g["public"]), "secret": list(g["secret"]),
                 "endpoint": g["endpoint"], "hint": g.get("hint", "")} for k, g in GROUPS.items()]

    @app.get("/api/settings")
    def get_settings(user: User = Depends(user_of)) -> dict[str, Any]:
        """The user's model settings, and the server's they fall back to (D696): a server key is
        only said to be set, never shown."""
        return {"values": store.settings(user), "server": store.server_settings(), "groups": _groups(),
                "public": list(PUBLIC_SETTINGS), "secret": list(SECRET_SETTINGS)}

    @app.get("/api/admin/settings")
    def get_server_settings(_a: User = Depends(admin_of)) -> dict[str, Any]:
        return {"values": store.server_settings(), "groups": _groups(), "public": list(PUBLIC_SETTINGS), "secret": list(SECRET_SETTINGS)}

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

    @app.get("/api/admin/apps")
    def all_apps(_a: User = Depends(admin_of)) -> list[dict[str, Any]]:
        """Every user's loops and their state (D684, D689)."""
        out = []
        for u in store.users():
            for a in Workspace(store.data, u.name).apps():
                out.append({**a, "owner": u.name, **runs.state(u, a["name"]),
                            "summary": _summary(Workspace(store.data, u.name), u, a["name"])})
        return out

    # ---- the machine and its controls (D695)
    def _live_loops() -> dict[tuple[str, str], dict[str, Any]]:
        """(user, app) -> the live start, over every user."""
        out = {}
        for u in store.users():
            for r in store.runs(u):
                if (u.name, r["app"]) not in out and runs.live(r):
                    out[(u.name, r["app"])] = r
        return out

    def _sample() -> dict[str, Any]:
        """One minute's sample of the machine (D699)."""
        from flux_cli.sandbox import _local

        from . import admin as adm

        m = adm.machine({k: v for k, v in {"server data": str(store.data), "sandbox caches": str(adm.cache_root().parent),
                                               "sandbox storage": str(_local())}.items() if os.path.exists(v)})
        live = _live_loops()
        out: dict[str, Any] = {"load1": (m["load"] or [0])[0], "cpus": m["cpus"],
                               "mem_used": (m["memory"]["total"] or 0) - (m["memory"]["available"] or 0), "mem_total": m["memory"]["total"] or 0,
                               "disks": {d["label"]: d["used"] / d["total"] for d in m["disks"] if not d.get("same_as") and d["total"]},
                               "loops": len(live)}
        if live:                                     # the containers' own use, when there are any
            cs = [c for c in adm.containers()["containers"] if c.get("state") == "running"]
            out.update(containers=len(cs), cpu=sum(c.get("cpu") or 0 for c in cs), cmem=sum(c.get("mem") or 0 for c in cs))
        else:
            out.update(containers=0, cpu=0.0, cmem=0.0)
        return out

    @app.get("/api/admin/history")
    def resource_history(hours: float = 24, _a: User = Depends(admin_of)) -> dict[str, Any]:
        """The machine over the last `hours` (D699), thinned for a chart."""
        hours = max(0.25, min(hours, 168))
        return {"hours": hours, "samples": history.read(hours), "sampling": history._thread is not None}

    @app.get("/api/admin/resources")
    def resources(_a: User = Depends(admin_of)) -> dict[str, Any]:
        """The machine, the sandbox's containers and every loop's disk (D695)."""
        from flux_cli.sandbox import _local

        from . import admin as adm

        live = _live_loops()
        users = store.users()
        loops, pairs = [], set()
        for u in users:
            w = Workspace(store.data, u.name)
            for a in w.apps():
                pairs.add((u.name, a["name"]))
                try:
                    disk = adm.loop_disk(w.app(a["name"]), u.name, a["name"])
                except WorkspaceError:
                    continue
                st = runs.state(u, a["name"])
                loops.append({"user": u.name, "app": a["name"], "running": (u.name, a["name"]) in live,
                              "container": st.get("container"), "last_active": st.get("last_active"), **disk})
        by_key = {adm._key(x["user"], x["app"]): x for x in loops}
        cont = adm.containers()
        for c in cont["containers"]:
            owner = by_key.get(adm._key(*c["app"].split(".", 1))) if c.get("app") and "." in c["app"] else None
            owner = owner or next((x for x in loops if x.get("container") == c["name"]), None)
            c["user"], c["loop"] = (owner["user"], owner["app"]) if owner else (None, None)
            c["orphan"] = c["state"] != "running" or not (owner and owner["running"])
        caches = [c for c in adm.caches(pairs, {u.name for u in users}) if c["kind"] != "loop"]
        paths = {"server data": str(store.data), "sandbox caches": str(adm.cache_root().parent), "sandbox storage": str(_local())}
        return {"machine": adm.machine({k: v for k, v in paths.items() if os.path.exists(v)}), **cont, "loops": loops,
                "caches": caches, "paused": store.server_get("paused"), "max_running": runs.max_running,
                "limits": {u.name: store.server_get(f"max_running:{u.name}") for u in users},
                "running": [{"user": k[0], "app": k[1]} for k in live]}

    @app.post("/api/admin/containers/{cname}/kill")
    def kill_container(cname: str, a: User = Depends(admin_of)) -> dict[str, str]:
        from . import admin as adm

        for (_u, _app), r in _live_loops().items():
            if runs.state(store.user(name=_u), _app).get("container") == cname:
                raise HTTPException(409, f"{cname} is {_u}'s {_app}, running: stop the loop instead")
        try:
            said = adm.kill_container(cname)
        except ValueError as exc:
            raise fail(exc) from exc
        store.audit(a.name, "kill container", cname)
        return {"ok": said}

    @app.post("/api/admin/caches/{key}/clean")
    def clean_cache(key: str, body: Clean, a: User = Depends(admin_of)) -> dict[str, Any]:
        from . import admin as adm

        for (u, app_name) in _live_loops():
            if adm._key(u, app_name) == key:
                raise HTTPException(409, f"{u}'s {app_name} is running: its cache is in use")
        try:
            freed = adm.clean(key, body.what)
        except ValueError as exc:
            raise fail(exc) from exc
        store.audit(a.name, "clean cache", f"{key}: {body.what}, {freed} bytes")
        return {"freed": freed}

    @app.post("/api/admin/stop-all")
    def stop_all(body: StopAll, a: User = Depends(admin_of)) -> dict[str, Any]:
        said = {f"{u}/{app_name}": runs.stop(r, now=body.now, why=f"every loop stopped by {a.name}")
                for (u, app_name), r in _live_loops().items()}
        store.audit(a.name, "stop all", f"{len(said)} loop(s){' now' if body.now else ''}")
        return {"stopped": said}

    @app.put("/api/admin/paused")
    def set_paused(body: Paused, a: User = Depends(admin_of)) -> dict[str, Any]:
        reason = (body.reason or "").strip() or None
        store.server_set("paused", reason)
        store.audit(a.name, "starts paused" if reason else "starts resumed", reason or "")
        return {"paused": reason}

    @app.put("/api/admin/users/{uname}/limit")
    def set_limit(uname: str, body: Limit, a: User = Depends(admin_of)) -> dict[str, Any]:
        if store.user(name=uname) is None:
            raise HTTPException(404, "no such user")
        if body.max_running is not None and not 0 <= body.max_running <= 64:
            raise HTTPException(400, "a limit from 0 to 64")
        store.server_set(f"max_running:{uname}", body.max_running)
        store.audit(a.name, "running limit", f"{uname}: {body.max_running if body.max_running is not None else 'the default'}")
        return {"max_running": body.max_running}

    # ---- environment variables (D697): the server's (admins), a user's, a loop's
    def _env_list(scope: str) -> list[dict[str, Any]]:
        return [{"name": k, **v} for k, v in sorted(store.env(scope).items())]

    def _set_env(scope: str, body: EnvVar, who: User, what: str) -> list[dict[str, Any]]:
        try:
            store.set_env(scope, body.name, body.value, body.secret)
        except ValueError as exc:
            raise fail(exc) from exc
        store.audit(who.name, "variable" if body.value is not None else "variable removed", f"{what}: {body.name}")
        return _env_list(scope)

    @app.get("/api/admin/env")
    def global_env(_a: User = Depends(admin_of)) -> list[dict[str, Any]]:
        return _env_list("global")

    @app.put("/api/admin/env")
    def put_global_env(body: EnvVar, a: User = Depends(admin_of)) -> list[dict[str, Any]]:
        return _set_env("global", body, a, "the server")

    @app.get("/api/env")
    def my_env(user: User = Depends(user_of)) -> dict[str, Any]:
        """The user's variables, and the server's they come after (names only for a secret)."""
        return {"mine": _env_list(f"user:{user.id}"), "server": _env_list("global")}

    @app.put("/api/env")
    def put_my_env(body: EnvVar, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        return _set_env(f"user:{user.id}", body, user, user.name)

    @app.get("/api/apps/{name}/env")
    def loop_env(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """A loop's variables, with the user's and the server's under them, and its advanced settings."""
        _w, whose, _d, _run = loop_of(name, user, owner)
        return {"loop": _env_list(f"loop:{whose.name}:{name}"), "user": _env_list(f"user:{whose.id}"),
                "server": _env_list("global"), "advanced": advanced(store, whose.name, name), "advanced_said": ADVANCED,
                "sandboxed_server": sandbox, "can_advance": user.admin}

    @app.put("/api/apps/{name}/env")
    def put_loop_env(name: str, body: EnvVar, owner: str | None = None, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        _w, whose, _d, _run = loop_of(name, user, owner, edit=True)
        return _set_env(f"loop:{whose.name}:{name}", body, user, f"{whose.name}/{name}")

    @app.put("/api/apps/{name}/advanced")
    def put_advanced(name: str, body: Advanced, owner: str | None = None, a: User = Depends(admin_of)) -> dict[str, Any]:
        """A loop's settings only an admin sets (D697): off the sandbox, its limits."""
        _w, whose, _d, _run = loop_of(name, a, owner)
        for k, v in (("memory", body.memory), ("tmp_size", body.tmp_size)):
            if v and not re.fullmatch(r"\d+(\.\d+)?[kmgKMG]?", v):
                raise HTTPException(400, f"{k}: a size such as 16g")
        if body.cpus and not re.fullmatch(r"\d+(\.\d+)?", body.cpus):
            raise HTTPException(400, "cpus: a number such as 8")
        if body.pids is not None and not 64 <= body.pids <= 1_000_000:
            raise HTTPException(400, "pids: from 64 to 1000000")
        body.allow = _rules(body.allow or []) or None
        got = {k: v for k, v in body.model_dump().items() if v not in (None, "") and not (k == "sandbox" and v is True)}
        store.server_set(f"adv:{whose.name}:{name}", got or None)
        store.audit(a.name, "advanced settings", f"{whose.name}/{name}: {json.dumps(got) or 'defaults'}")
        return {"advanced": got}

    def _rules(items: list[str]) -> list[str]:
        out = []
        for x in items:
            x = str(x).strip()
            if not x:
                continue
            if not re.fullmatch(HOST_RULE, x) and x != "localhost":
                raise HTTPException(400, f"{x!r}: a host, *.domain, an IP or a CIDR")
            out.append(x)
        return list(dict.fromkeys(out))

    @app.get("/api/admin/sandbox")
    def get_sandbox(_a: User = Depends(admin_of)) -> dict[str, Any]:
        """What every sandbox gets (D698), and the server user's login PATH to choose from."""
        from flux_cli.sandbox import _HOME_COPY, _HOME_RO

        return {"config": store.server_get("sandbox") or SandboxConfig().model_dump(), "login_path": login_path(),
                "path": os.environ.get("PATH", "").split(os.pathsep), "home": os.path.expanduser("~"),
                "fixed_ro": list(_HOME_RO), "fixed_copy": list(_HOME_COPY), "sandboxed": sandbox}

    @app.put("/api/admin/sandbox")
    def put_sandbox(body: SandboxConfig, a: User = Depends(admin_of)) -> dict[str, Any]:
        if body.network not in ("open", "allowlist"):
            raise HTTPException(400, "network: open or allowlist")
        for d in body.path:
            if not d.startswith("/"):
                raise HTTPException(400, f"{d!r}: a PATH directory is absolute")
        for rel in [*body.home_ro, *body.home_copy]:
            r = rel.strip().removeprefix("~/").strip("/")
            if not r or r.startswith("/") or ".." in r.split("/"):
                raise HTTPException(400, f"{rel!r}: a path inside the home folder, such as .config/opencode")
        cfg = body.model_dump()
        cfg["allow"] = _rules(body.allow)
        cfg["home_ro"] = [r.strip().removeprefix("~/").strip("/") for r in body.home_ro if r.strip()]
        cfg["home_copy"] = [r.strip().removeprefix("~/").strip("/") for r in body.home_copy if r.strip()]
        cfg["path"] = [d.strip() for d in body.path if d.strip()]
        store.server_set("sandbox", cfg)
        store.audit(a.name, "sandbox settings", f"network {cfg['network']}: {', '.join(cfg['allow']) or '-'}; "
                    f"PATH +{len(cfg['path'])}{' +login' if cfg['login_path'] else ''}; home ro {len(cfg['home_ro'])}, copy {len(cfg['home_copy'])}")
        return {"config": cfg}

    # ---- sharing a loop (D701): watch sees its runs and outputs, edit also changes and runs it
    @app.get("/api/apps/{name}/shares")
    def get_shares(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        _w, whose, perm = access(user, owner, name)
        return {"shares": [{"user": u, "perm": p} for u, p in sorted(store.shares(whose.name, name).items())],
                "users": [u.name for u in store.users() if u.name != whose.name and not u.disabled], "can_share": perm == "owner"}

    @app.put("/api/apps/{name}/shares")
    def put_share(name: str, body: ShareIn, user: User = Depends(user_of)) -> dict[str, Any]:
        loop_of(name, user)                                   # the owner's own: only they share it
        other = store.user(name=body.user)
        if other is None or other.id == user.id:
            raise HTTPException(400, "share with another user of this server")
        try:
            got = store.set_share(user.name, name, other.name, body.perm)
        except ValueError as exc:
            raise fail(exc) from exc
        store.audit(user.name, "share" if body.perm else "unshare", f"{name} with {other.name}: {body.perm or '-'}")
        return {"shares": [{"user": u, "perm": p} for u, p in sorted(got.items())]}

    @app.get("/api/shared")
    def shared(user: User = Depends(user_of)) -> list[dict[str, Any]]:
        """The loops other users shared with this one, with what they may do (D701)."""
        out = []
        for owner, app_name, perm in store.shared_with(user.name):
            o = store.user(name=owner)
            w = Workspace(store.data, owner)
            if o is None or not (w.root / app_name).is_dir():
                continue
            meta = w.meta(app_name)
            out.append({"name": app_name, "owner": owner, "perm": perm, "document": meta.get("document"),
                        **runs.state(o, app_name), "summary": _summary(w, o, app_name)})
        return out

    @app.get("/api/audit")
    def audit(_a: User = Depends(admin_of)) -> list[dict[str, Any]]:
        return store.audit_log()

    # ---- applications
    @app.get("/api/apps")
    def apps(user: User = Depends(user_of)) -> list[dict[str, Any]]:
        """The user's loops, each running or not (D689), the most recently active first."""
        w = ws(user)
        out = [{**a, **runs.state(user, a["name"]), "summary": _summary(w, user, a["name"])} for a in w.apps()]
        out.sort(key=lambda a: (not a["running"], -(a.get("last_active") or 0), a["name"]))
        return out

    def _summary(w: Workspace, whose: User, name: str) -> dict[str, Any]:
        """A loop in a line (D693): designs measured, accepted, and the decision's value on the
        first objective."""
        from .results import designs

        run = runs.latest(whose, name)
        if not run or not os.path.exists(run["db"]):
            return {"designs": 0, "accepted": 0}
        try:
            answer = json.loads(loop_files(w.app(name))["answer"].read_text())
            decision = (answer.get("decision") or {}).get("name") if isinstance(answer.get("decision"), dict) else None
        except (OSError, ValueError, WorkspaceError):
            decision = None
        try:
            got = designs(run["db"], _stages(w, name), decision)
        except Exception:  # noqa: BLE001 -- a record the list cannot read: the state alone
            return {"designs": 0, "accepted": 0}
        out: dict[str, Any] = {"designs": len(got["designs"]), "accepted": got["counts"]["accepted"]}
        dec = next((d for d in got["designs"] if d["decision"]), None)
        if dec is not None:
            lim = got["limits"][0] if got["limits"] else None
            metric = lim["metric"] if lim else (got["metrics"][0] if got["metrics"] else None)
            if metric and metric in dec["numbers"]:
                out["best"] = {"design": dec["name"], "metric": metric, "value": dec["numbers"][metric],
                               "meets": dec["meets"].get(metric)}
        return out

    @app.post("/api/apps")
    async def upload(name: str = Form(...), files: list[UploadFile] = File(...),
                     replace: bool = Form(False), user: User = Depends(user_of)) -> dict[str, Any]:
        got = [(f.filename or "file", await f.read()) for f in files]
        try:
            meta = ws(user).create(name, got, replace=replace)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        store.audit(user.name, "upload", f"{name}: {len(got)} file(s)")
        return {"name": name, **meta}

    @app.post("/api/apps/{name}/files")
    async def add_files(name: str, files: list[UploadFile] = File(...), folder: str = Form(""), owner: str | None = None,
                        user: User = Depends(user_of)) -> dict[str, Any]:
        w, _whose = editor(user, owner, name)
        got = [(f.filename or "file", await f.read()) for f in files]
        try:
            written = w.add(name, got, folder)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        store.audit(user.name, "add files", f"{name}: {len(written)} file(s)")
        return {"written": written}

    @app.put("/api/apps/{name}/part")
    async def put_part(name: str, path: str, offset: int, request: Request, final: bool = False, owner: str | None = None,
                       user: User = Depends(user_of)) -> dict[str, Any]:
        """A large file in parts (D700): the raw body written at `offset`; `final` moves it into place."""
        w, _whose = editor(user, owner, name)
        data = await request.body()
        try:
            size = w.put_part(name, path, offset, data, final)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        if final:
            store.audit(user.name, "add files", f"{name}: {path} ({size} bytes, in parts)")
        return {"size": size}

    # ---- the applications folder of this Flux (D700): an admin sees them and makes one a loop
    def _apps_root() -> Path | None:
        given = os.environ.get("FLUX_APPLICATIONS")
        root = Path(given) if given else Path(__file__).resolve().parents[4] / "applications"
        return root if root.is_dir() else None

    @app.get("/api/admin/applications")
    def applications(a: User = Depends(admin_of)) -> dict[str, Any]:
        from . import admin as adm
        from .workspace import _pick_document

        root = _apps_root()
        if root is None:
            return {"root": None, "applications": []}
        mine = {x["name"]: x for x in ws(a).apps()}
        out = []
        for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            tops = [p.name for p in d.iterdir() if p.is_file()]
            doc = _pick_document(tops)
            if doc is None:
                continue
            statement = ""
            try:
                import yaml

                raw = yaml.safe_load((d / doc).read_text()) or {}
                statement = " ".join(str(raw.get("statement") or "").split())[:220] if isinstance(raw, dict) else ""
            except Exception:  # noqa: BLE001 -- a document the list cannot read: its name alone
                pass
            loop = mine.get(d.name)
            out.append({"name": d.name, "document": doc, "statement": statement, "size": adm.dir_size(d),
                        "loop": bool(loop), "linked": bool(loop and ws(a).meta(d.name).get("source") == str(d.resolve()))})
        return {"root": str(root), "applications": out}

    @app.post("/api/admin/applications/{app_name}/use")
    def use_application(app_name: str, refresh: bool = False, a: User = Depends(admin_of)) -> dict[str, Any]:
        """The application as one of the admin's loops: its files linked in; `refresh` takes them
        again (the loop's record, log and workbench stay)."""
        root = _apps_root()
        src = (root / app_name).resolve() if root else None
        if root is None or src is None or src.parent != root.resolve() or not src.is_dir():
            raise HTTPException(404, f"no application {app_name!r}")
        if refresh and any(runs.live(r) for r in store.runs(a, app_name)):
            raise HTTPException(409, "stop the loop first")
        try:
            meta = ws(a).import_dir(app_name, src, replace=refresh)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        store.audit(a.name, "application refreshed" if refresh else "application used", app_name)
        return {"name": app_name, **meta}

    @app.get("/api/apps/{name}/document")
    def document_views(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """The document as the configurator reads it back (D686): as written, and as the loader
        takes it; neither runs any of its code."""
        from .configure import views

        w, _whose = reader(user, owner, name)
        try:
            doc = w.meta(name).get("document")
            path = w.path(name, doc or "")
            return {"document": doc, **views(path)}
        except (WorkspaceError, ValueError, OSError) as exc:
            raise fail(exc) from exc

    @app.post("/api/apps/{name}/document/preview")
    def preview_document(name: str, body: DocSave, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """What a configurator save would write (D693): the document now, and after -- the kept
        keys carried over as the save would."""
        from .configure import merged, views

        w, _whose = editor(user, owner, name)
        try:
            doc = w.meta(name).get("document")
            path = w.path(name, doc or "")
            return {"document": doc, "before": path.read_text(), "after": merged(body.text, views(path)["raw"], body.kept)}
        except (WorkspaceError, ValueError, OSError) as exc:
            raise fail(exc) from exc

    @app.put("/api/apps/{name}/document")
    def save_document(name: str, body: DocSave, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """The configurator's YAML, with the keys it keeps carried over as written (D686)."""
        from .configure import merged, views

        w, _whose = editor(user, owner, name)
        try:
            doc = w.meta(name).get("document")
            path = w.path(name, doc or "")
            text = merged(body.text, views(path)["raw"], body.kept)
            w.write(name, doc, text)
            after = views(path)
        except (WorkspaceError, ValueError, OSError) as exc:
            raise fail(exc) from exc
        store.audit(user.name, "configure", name)
        return {"ok": "saved" + ("" if not after["error"] else f"; the loader says: {after['error']}"),
                "id": w.meta(name).get("id"), "error": after["error"]}

    @app.post("/api/apps/from-text")
    def from_text(body: DocText, user: User = Depends(user_of)) -> dict[str, Any]:
        try:
            meta = ws(user).create_from_text(body.name, body.filename, body.text)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        store.audit(user.name, "write document", body.name)
        return {"name": body.name, **meta}

    @app.delete("/api/apps/{name}")
    def delete_app(name: str, user: User = Depends(user_of)) -> dict[str, str]:
        if any(runs.live(r) for r in store.runs(user, name)):
            raise HTTPException(409, "stop the loop first")
        try:
            ws(user).delete(name)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        store.server_set(f"env:loop:{user.name}:{name}", None)        # D697: its variables and settings go with it
        store.server_set(f"adv:{user.name}:{name}", None)
        store.server_set(f"share:{user.name}:{name}", None)
        store.audit(user.name, "delete app", name)
        return {"ok": name}

    @app.get("/api/apps/{name}")
    def app_info(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        w, whose = reader(user, owner, name)
        try:
            w.app(name)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc
        perm = access(user, owner, name)[2]
        return {"name": name, "owner": whose.name, "mine": whose.id == user.id, "perm": perm, **w.meta(name), "files": w.files(name),
                "state": runs.state(whose, name)}

    @app.get("/api/apps/{name}/files")
    def app_files(name: str, path: str = "", owner: str | None = None, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        try:
            return reader(user, owner, name)[0].files(name, path)
        except WorkspaceError as exc:
            raise fail(exc) from exc

    @app.get("/api/apps/{name}/workbench")
    def workbench(name: str, owner: str | None = None, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        try:
            return reader(user, owner, name)[0].workbench(name)
        except WorkspaceError as exc:
            raise fail(exc) from exc

    @app.get("/api/apps/{name}/file")
    def app_file(name: str, path: str, download: bool = False, owner: str | None = None, user: User = Depends(user_of)):
        try:
            data, is_text = reader(user, owner, name)[0].read(name, path)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        if download or not is_text:
            return Response(data, media_type="application/octet-stream",
                            headers={"Content-Disposition": f'attachment; filename="{Path(path).name}"'})
        return Response(data, media_type="text/plain; charset=utf-8")

    @app.get("/api/apps/{name}/inputs")
    def list_inputs(name: str, owner: str | None = None, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        """The loop's own files (D696): what the configurator edits beside the document."""
        w, _whose = reader(user, owner, name)
        try:
            return w.inputs(name)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.delete("/api/apps/{name}/file")
    def delete_file(name: str, path: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, str]:
        try:
            editor(user, owner, name)[0].remove(name, path)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        store.audit(user.name, "delete file", f"{name}/{path}")
        return {"ok": f"{path} deleted"}

    @app.put("/api/apps/{name}/file")
    def put_file(name: str, path: str, body: FileText, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, str]:
        try:
            editor(user, owner, name)[0].write(name, path, body.text)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        store.audit(user.name, "edit", f"{name}/{path}")
        return {"ok": path}

    @app.post("/api/apps/{name}/check")
    def check(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        w, whose = editor(user, owner, name)
        try:
            d = w.app(name)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc
        doc = w.meta(name).get("document")
        env = {**run_env(store, whose, name), "FLUX_SANDBOX_APP": f"{whose.name}.{name}"}   # the owner's loop, its settings
        adv = advanced(store, whose.name, name)
        sandbox_env(env, sandbox, adv)
        machine_env(env, store.server_get("sandbox") or {}, adv, [])
        digest = w.inputs_digest(name)
        try:
            r = subprocess.run([shutil.which("flux") or sys.argv[0], "task", "check", str(d / doc)], cwd=str(d), env=env,
                               capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
            ok, output = r.returncode == 0, (r.stdout + r.stderr)[-20000:]
        except subprocess.TimeoutExpired:
            ok, output = False, "the check ran past 600 s"
        w.set_meta(name, last_check={"digest": digest, "ok": ok, "t": time.time(), "output": output})   # D693
        return {"ok": ok, "output": output}

    @app.get("/api/apps/{name}/preflight")
    def preflight(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """Before a start (D693): did the inputs change since the last start, and was the check
        run on them as they are now -- with what result."""
        w, _whose = editor(user, owner, name)
        try:
            w.app(name)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc
        meta, digest = w.meta(name), w.inputs_digest(name)
        last = meta.get("last_check") or {}
        return {"digest": digest, "changed": digest != meta.get("last_start_digest"),
                "checked": last.get("digest") == digest, "ok": bool(last.get("ok")) if last.get("digest") == digest else None,
                "output": last.get("output", "") if last.get("digest") == digest else "", "when": last.get("t"),
                "options": meta.get("last_options"), "paused": store.server_get("paused"),
                "network": {k: (store.server_get("sandbox") or {}).get(k) for k in ("network", "allow", "users_add")}}

    # ---- the loop: running or not; a start resumes it from its record (D689)
    def loop_of(name: str, user: User, owner: str | None = None, edit: bool = False) -> tuple[Workspace, User, Path, dict[str, Any] | None]:
        """(workspace, whose, the application's folder, its latest start or None); `edit`: a change."""
        w, whose = editor(user, owner, name) if edit else reader(user, owner, name)
        try:
            d = w.app(name)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc
        return w, whose, d, runs.latest(whose, name)

    @app.get("/api/apps/{name}/state")
    def loop_state(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        _w, whose, _d, _run = loop_of(name, user, owner)
        return runs.state(whose, name)

    @app.get("/api/loops")
    def loops_state(user: User = Depends(user_of)) -> list[dict[str, Any]]:
        """Every loop of the user with its state: what the page's notifications watch."""
        return [runs.state(user, a["name"]) for a in ws(user).apps()]

    @app.post("/api/apps/{name}/start")
    def start(name: str, body: RunOptions, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, str]:
        w, whose, d, _run = loop_of(name, user, owner, edit=True)
        meta = w.meta(name)
        try:     # the owner's loop: their record, settings and limits; who started it is said (D701)
            runs.start(whose, name, d, meta["document"], str(meta.get("id") or name), body.model_dump(), by=user)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        w.set_meta(name, last_start_digest=w.inputs_digest(name), last_options=body.model_dump())    # D693
        store.audit(user.name, "start", name if whose.id == user.id else f"{whose.name}/{name}")
        return {"ok": f"{name} started: it resumes from its record"}

    @app.post("/api/apps/{name}/stop")
    def stop(name: str, body: Stop, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, str]:
        if access(user, owner, name)[2] == "watch":             # the owner, an editor or an admin may stop it
            raise HTTPException(403, "you may watch this loop, not stop it")
        _w, whose, _d, run = loop_of(name, user, owner)
        said = runs.stop(run, now=body.now)
        store.audit(user.name, "stop", f"{whose.name}/{name}: {said}")
        return {"ok": said}

    @app.post("/api/apps/{name}/notes")
    def add_note(name: str, body: NoteIn, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, str]:
        _w, _whose, d, run = loop_of(name, user, owner, edit=True)   # the owner or an editor
        if not run or not runs.live(run):
            raise HTTPException(409, "the loop is not running")
        runs.note(d / "runs", user, body.text.strip())
        store.audit(user.name, "note", name)
        return {"ok": "sent: it reaches the next prompt, or answers the agent's open question"}

    @app.get("/api/apps/{name}/notes")
    def list_notes(name: str, owner: str | None = None, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        _w, _whose, d, _run = loop_of(name, user, owner)
        return runs.notes(d / "runs")

    async def _follow(path_of, start_after, offset: int, request: Request, kind: str):
        """Server-sent events: each new line of a file, as it grows, from byte `offset`. For the
        journal, `start_after()` is when the loop's latest start began: its tree, not the last."""
        from flux_loop.journal import read_events

        ino, offset = offset
        yield "retry: 3000\n\n"
        while True:
            if await request.is_disconnected():
                return
            path = path_of()
            if path and os.path.exists(path):
                st = os.stat(path)
                if st.st_ino != ino or st.st_size < offset:      # D694: another file (a new start's), or cut
                    if ino is not None or st.st_size < offset:
                        offset = 0
                    ino = st.st_ino
                if kind == "events":
                    events, new = read_events(path, offset)
                    since = start_after()
                    for e in events:
                        if e.get("t", 0) >= since:
                            yield f"id: {ino}-{new}\nevent: {kind}\ndata: {json.dumps(e)}\n\n"
                else:
                    with open(path, "rb") as fh:
                        fh.seek(offset)
                        chunk = fh.read(256 * 1024)
                    new = offset + len(chunk)
                    if chunk:
                        yield f"id: {ino}-{new}\nevent: {kind}\ndata: {json.dumps(chunk.decode('utf-8', 'replace'))}\n\n"
                if new != offset:
                    offset = new
                    continue
            yield ": keep-alive\n\n"
            await asyncio.sleep(1.0)

    def _offset(request: Request, offset: str) -> tuple[int | None, int]:
        """Where a follower resumes (D694): `<inode>-<byte>` from its last event's id (the header
        a reconnecting EventSource sends, or `offset` from a page that reopened it); a bare byte
        offset is taken on whatever file is there."""
        said = request.headers.get("last-event-id") or offset or "0"
        ino, _, at = said.rpartition("-")
        try:
            return (int(ino) if ino else None), int(at)
        except ValueError:
            return None, 0

    @app.get("/api/apps/{name}/events")
    async def events(name: str, request: Request, offset: str = "0", owner: str | None = None, user: User = Depends(user_of)):
        _w, whose, _d, _run = loop_of(name, user, owner)
        latest = lambda: runs.latest(whose, name)                               # noqa: E731 -- a new start moves it
        stream = _follow(lambda: runs.events_path(latest()), lambda: (latest() or {"started": 0})["started"] - 1,
                         _offset(request, offset), request, "events")
        return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/apps/{name}/log")
    async def log(name: str, request: Request, offset: str = "0", owner: str | None = None, user: User = Depends(user_of)):
        """The loop's one log, every start in it."""
        _w, _whose, d, _run = loop_of(name, user, owner)
        path = str(loop_files(d)["log"])
        stream = _follow(lambda: path, lambda: 0, _offset(request, offset), request, "log")
        return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/apps/{name}/log/raw")
    def log_raw(name: str, owner: str | None = None, user: User = Depends(user_of)):
        _w, _whose, d, _run = loop_of(name, user, owner)
        path = loop_files(d)["log"]
        if not path.exists():
            raise HTTPException(404, "no log yet")
        return FileResponse(path, media_type="text/plain; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{name}.log"'})

    @app.get("/api/apps/{name}/turns")
    def turns(name: str, k: int | None = None, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """The loop's model and agent turns, all of them, newest last."""
        _w, _whose, _d, run = loop_of(name, user, owner)
        path = runs.turns_path(run)
        out: list[dict[str, Any]] = []
        if path and os.path.exists(path):
            with open(path) as fh:
                for n, line in enumerate(fh, 1):
                    try:
                        t = json.loads(line)
                    except ValueError:
                        continue
                    if k is None:
                        t = {key: (v[:300] + "..." if isinstance(v, str) and len(v) > 300 else v)
                             for key, v in t.items() if key not in ("hops",)} | {"hops": len(t.get("hops") or [])}
                    elif n != k:
                        continue
                    out.append({"k": n, **t})
        return {"turns": out[-500:] if k is None else out}

    @app.get("/api/apps/{name}/timeline")
    def loop_timeline(name: str, start: int | None = None, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """Where one start's time went (D694): its phases as bars, and per kind of work."""
        from .timeline import timeline

        _w, _whose, _d, run = loop_of(name, user, owner)
        path = runs.events_path(run)
        if not path or not os.path.exists(path):
            return {"starts": [], "start": None, "bars": [], "kinds": [], "passes": []}
        return timeline(path, start)

    @app.get("/api/apps/{name}/usage")
    def loop_usage(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """What the loop's model and agent turns cost (D694)."""
        from .usage import usage

        _w, _whose, _d, run = loop_of(name, user, owner)
        return usage(runs.turns_path(run))

    def _user_usage(u: User) -> dict[str, Any]:
        from .usage import usage

        total: dict[str, Any] = {"user": u.name, "loops": 0, "turns": 0, "seconds": 0.0, "tokens_in": 0.0, "tokens_out": 0.0,
                                 "tokens_cached": 0.0, "cost_usd": 0.0, "counted": 0}
        for a in Workspace(store.data, u.name).apps():
            got = usage(runs.turns_path(runs.latest(u, a["name"])))["total"]
            total["loops"] += 1
            for k in ("turns", "seconds", "tokens_in", "tokens_out", "tokens_cached", "cost_usd", "counted"):
                total[k] += got[k]
        return total

    @app.get("/api/usage")
    def my_usage(user: User = Depends(user_of)) -> dict[str, Any]:
        """The user's turns over all their loops (D694)."""
        return _user_usage(user)

    @app.get("/api/admin/usage")
    def all_usage(_a: User = Depends(admin_of)) -> list[dict[str, Any]]:
        return [_user_usage(u) for u in store.users()]

    @app.get("/api/apps/{name}/results")
    def results(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        from flux_loop.report import load

        _w, _whose, d, run = loop_of(name, user, owner)
        cid, _rdir = runs.campaign(run)
        if not cid or not os.path.exists(run["db"]):
            return {"campaign": None}
        rep = load(run["db"], cid)
        from .results import designs, thin

        rows = thin(rep.rows, [(o.metric, o.direction) for o in rep.objectives])
        answer = None
        ans = loop_files(d)["answer"]
        if ans.exists():
            try:
                answer = json.loads(ans.read_text())
            except ValueError:
                pass
        decision = ((answer or {}).get("decision") or {}).get("name") if isinstance((answer or {}).get("decision"), dict) else None
        listed = designs(run["db"], _stages(_w, name), decision, limit=20000)
        objective_list = [{"metric": o.metric, "direction": o.direction, "goal": o.goal, "stage": o.stage, "unit": o.unit}
                          for o in rep.objectives]                     # for the Overview's charts (D692)
        return {"campaign": cid, "objectives": rep.objectives.describe(), "objective_list": objective_list, "rows": rows,
                "rows_total": len(rep.rows),
                "passes": [{"when": w, "conclusion": c} for w, c in rep.passes], "notes": rep.notes,
                "agent_turns": len(rep.agent_turns), "answer": answer, **listed}

    def _stages(w: Workspace, name: str) -> list[dict[str, Any]]:
        """The document's stages (order, cutoffs) as the loader reads them; [] when it refuses."""
        from .configure import views

        try:
            normal = views(w.path(name, w.meta(name).get("document") or ""))["normal"] or {}
        except Exception:  # noqa: BLE001 -- the record's own order then, no cutoffs
            return []
        return [st for st in normal.get("stages") or [] if st.get("name")]

    @app.get("/api/apps/{name}/design")
    def design(name: str, design: str, part: str = "", owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """One design of the loop: its source, why it failed, every stage's numbers (D690)."""
        from flux_store import CampaignStore

        _w, _whose, _d, run = loop_of(name, user, owner)
        if not run or not os.path.exists(run["db"]):
            raise HTTPException(404, "no record yet")
        store = CampaignStore(run["db"])
        found: dict[str, Any] = {"name": design, "part": part, "artifact": None, "trials": []}
        try:
            for camp in store.list_campaigns():
                for t in store.trials(camp["campaign_id"], status="ok"):
                    c = t.candidate or {}
                    if str(c.get("name")) != design or str(c.get("subgoal") or "") != part:
                        continue
                    if c.get("artifact"):
                        found["artifact"] = c["artifact"]
                    if t.result is not None and t.stage not in ("gate", "admit", "prototype"):
                        found["trials"].append({"stage": t.stage, "when": t.created_at,
                                                "metrics": {m: t.result.value_of(m) for m in t.result.metrics}})
        finally:
            store.close()
        if not found["trials"]:
            raise HTTPException(404, "no such design")
        return found

    @app.get("/api/apps/{name}/report", response_class=HTMLResponse)
    def report(name: str, owner: str | None = None, user: User = Depends(user_of)) -> HTMLResponse:
        from flux_loop.report import load, render

        _w, _whose, _d, run = loop_of(name, user, owner)
        cid, _rdir = runs.campaign(run)
        if not cid:
            raise HTTPException(404, "no record yet")
        return HTMLResponse(render(load(run["db"], cid)),
                            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
                                     "X-Frame-Options": "SAMEORIGIN"})

    # ---- pages
    if CRAFTER.is_dir():
        app.mount("/crafter-assets", StaticFiles(directory=CRAFTER), name="crafter")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app
