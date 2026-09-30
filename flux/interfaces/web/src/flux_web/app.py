"""The web API and pages (D683). Every `/api` route but login needs a session cookie; every request
that changes something also needs the `X-Flux: 1` header, which a page of another site cannot
send (CSRF). A user sees their own applications and runs; an admin also manages users and
reads every run."""

from __future__ import annotations

import asyncio
import json
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

from .runs import RunManager, run_env
from .store import PUBLIC_SETTINGS, SECRET_SETTINGS, SESSION_DAYS, Store, User
from .workspace import Workspace, WorkspaceError

COOKIE = "flux_session"
STATIC = Path(__file__).parent / "static"
CRAFTER = Path(__file__).resolve().parents[4] / "website" / "docs" / "assets"


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


class Settings(BaseModel):
    values: dict[str, str | None]


def create_app(data: str | Path, *, sandbox: bool = True, secure_cookie: bool = False, max_running: int = 4) -> FastAPI:
    store = Store(data)
    runs = RunManager(store, sandbox=sandbox, max_running=max_running)
    app = FastAPI(title="Flux", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.store, app.state.runs = store, runs

    # ---- guards
    @app.middleware("http")
    async def csrf(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("x-flux") != "1":
                return JSONResponse({"detail": "missing the X-Flux header"}, status_code=403)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith("/api/runs/") or not request.url.path.endswith("/report"):
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

    def reader(user: User, owner: str | None) -> tuple[Workspace, User]:
        """Whose applications a read names: one's own, or, for an admin, any user's (D684)."""
        if not owner or owner == user.name:
            return ws(user), user
        if not user.admin:
            raise HTTPException(403, "admins only")
        other = store.user(name=owner)
        if other is None:
            raise HTTPException(404, "no such user")
        return Workspace(store.data, other.name), other

    def run_of(run_id: int, user: User) -> dict[str, Any]:
        r = store.run(run_id)
        if r is None or (r["user_id"] != user.id and not user.admin):
            raise HTTPException(404, "no such run")
        return r

    def fail(exc: Exception) -> HTTPException:
        return HTTPException(400, str(exc))

    # ---- accounts
    @app.post("/api/login")
    def login(body: Login, response: Response) -> dict[str, Any]:
        token = store.login(body.name, body.password)
        if token is None:
            store.audit(body.name, "login refused")
            time.sleep(0.5)
            raise HTTPException(401, "wrong name or password (five failures lock the name for ten minutes)")
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure_cookie,
                            max_age=SESSION_DAYS * 86400, path="/")
        store.audit(body.name, "login")
        u = store.user(name=body.name)
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

    @app.get("/api/settings")
    def get_settings(user: User = Depends(user_of)) -> dict[str, Any]:
        return {"values": store.settings(user), "public": list(PUBLIC_SETTINGS), "secret": list(SECRET_SETTINGS)}

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
        """Every user's applications, and which are running (D684)."""
        out = []
        for u in store.users():
            live = {r["app"] for r in store.runs(u) if runs.live(r)}
            for a in Workspace(store.data, u.name).apps():
                out.append({**a, "owner": u.name, "running": a["name"] in live})
        return out

    @app.get("/api/audit")
    def audit(_a: User = Depends(admin_of)) -> list[dict[str, Any]]:
        return store.audit_log()

    # ---- applications
    @app.get("/api/apps")
    def apps(user: User = Depends(user_of)) -> list[dict[str, Any]]:
        out = ws(user).apps()
        live = {r["app"] for r in store.runs(user) if runs.live(r)}
        for a in out:
            a["running"] = a["name"] in live
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
    async def add_files(name: str, files: list[UploadFile] = File(...), folder: str = Form(""),
                        user: User = Depends(user_of)) -> dict[str, Any]:
        got = [(f.filename or "file", await f.read()) for f in files]
        try:
            written = ws(user).add(name, got, folder)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        store.audit(user.name, "add files", f"{name}: {len(written)} file(s)")
        return {"written": written}

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
            raise HTTPException(409, "stop its runs first")
        try:
            ws(user).delete(name)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        store.audit(user.name, "delete app", name)
        return {"ok": name}

    @app.get("/api/apps/{name}")
    def app_info(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        w, whose = reader(user, owner)
        try:
            w.app(name)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"name": name, "owner": whose.name, "mine": whose.id == user.id, **w.meta(name), "files": w.files(name),
                "runs": [runs.state(r) for r in store.runs(whose, name)[:50]]}

    @app.get("/api/apps/{name}/files")
    def app_files(name: str, path: str = "", owner: str | None = None, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        try:
            return reader(user, owner)[0].files(name, path)
        except WorkspaceError as exc:
            raise fail(exc) from exc

    @app.get("/api/apps/{name}/file")
    def app_file(name: str, path: str, download: bool = False, owner: str | None = None, user: User = Depends(user_of)):
        try:
            data, is_text = reader(user, owner)[0].read(name, path)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        if download or not is_text:
            return Response(data, media_type="application/octet-stream",
                            headers={"Content-Disposition": f'attachment; filename="{Path(path).name}"'})
        return Response(data, media_type="text/plain; charset=utf-8")

    @app.put("/api/apps/{name}/file")
    def put_file(name: str, path: str, body: FileText, user: User = Depends(user_of)) -> dict[str, str]:
        try:
            ws(user).write(name, path, body.text)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        store.audit(user.name, "edit", f"{name}/{path}")
        return {"ok": path}

    @app.post("/api/apps/{name}/check")
    def check(name: str, user: User = Depends(user_of)) -> dict[str, Any]:
        w = ws(user)
        try:
            d = w.app(name)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc
        doc = w.meta(name).get("document")
        env = {**run_env(store, user), "FLUX_SANDBOX_APP": f"{user.name}-{name}"}
        if sandbox:
            env["FLUX_SANDBOX"] = "1"
        try:
            r = subprocess.run([shutil.which("flux") or sys.argv[0], "task", "check", str(d / doc)], cwd=str(d), env=env,
                               capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {"ok": False, "output": "the check ran past 600 s"}
        return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-20000:]}

    # ---- runs
    @app.post("/api/apps/{name}/runs")
    def start(name: str, body: RunOptions, user: User = Depends(user_of)) -> dict[str, Any]:
        w = ws(user)
        try:
            d = w.app(name)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc
        meta = w.meta(name)
        try:
            run_id = runs.start(user, name, d, meta["document"], str(meta.get("id") or name), body.model_dump())
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from exc
        store.audit(user.name, "start run", f"{name} #{run_id}")
        return {"id": run_id}

    @app.get("/api/runs")
    def list_runs(everyone: bool = False, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        rows = store.runs(None if (everyone and user.admin) else user)[:200]
        return [runs.state(r) for r in rows]

    @app.get("/api/runs/{run_id}")
    def run_state(run_id: int, user: User = Depends(user_of)) -> dict[str, Any]:
        return runs.state(run_of(run_id, user))

    @app.post("/api/runs/{run_id}/stop")
    def stop(run_id: int, body: Stop, user: User = Depends(user_of)) -> dict[str, str]:
        r = run_of(run_id, user)
        said = runs.stop(r, now=body.now)
        store.audit(user.name, "stop run", f"#{run_id}: {said}")
        return {"ok": said}

    @app.post("/api/runs/{run_id}/notes")
    def add_note(run_id: int, body: NoteIn, user: User = Depends(user_of)) -> dict[str, str]:
        r = run_of(run_id, user)
        if r["user_id"] != user.id:
            raise HTTPException(403, "only the run's owner steers it")
        if not runs.live(r):
            raise HTTPException(409, "the run has ended")
        runs.note(r, user, body.text.strip())
        store.audit(user.name, "note", f"#{run_id}")
        return {"ok": "sent: it reaches the next prompt, or answers the agent's open question"}

    @app.get("/api/runs/{run_id}/notes")
    def list_notes(run_id: int, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        return runs.notes(run_of(run_id, user))

    async def _follow(path_of, start_after: float, offset: int, request: Request, kind: str):
        """Server-sent events: each new line of a file, as it grows, from byte `offset`."""
        from flux_loop.journal import read_events

        while True:
            if await request.is_disconnected():
                return
            path = path_of()
            if path and os.path.exists(path):
                if kind == "events":
                    events, new = read_events(path, offset)
                    for e in events:
                        if e.get("t", 0) >= start_after:
                            yield f"id: {new}\nevent: {kind}\ndata: {json.dumps(e)}\n\n"
                else:
                    with open(path, "rb") as fh:
                        fh.seek(offset)
                        chunk = fh.read(256 * 1024)
                    new = offset + len(chunk)
                    if chunk:
                        yield f"id: {new}\nevent: {kind}\ndata: {json.dumps(chunk.decode('utf-8', 'replace'))}\n\n"
                if new != offset:
                    offset = new
                    continue
            yield ": keep-alive\n\n"
            await asyncio.sleep(1.0)

    def _offset(request: Request, offset: int) -> int:
        try:
            return int(request.headers.get("last-event-id") or offset)
        except ValueError:
            return offset

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: int, request: Request, offset: int = 0, user: User = Depends(user_of)):
        r = run_of(run_id, user)
        stream = _follow(lambda: runs.events_path(store.run(run_id) or r), r["started"] - 1, _offset(request, offset),
                         request, "events")
        return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/runs/{run_id}/log")
    async def log(run_id: int, request: Request, offset: int = 0, user: User = Depends(user_of)):
        r = run_of(run_id, user)
        stream = _follow(lambda: r["log"], 0, _offset(request, offset), request, "log")
        return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/runs/{run_id}/turns")
    def turns(run_id: int, k: int | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        r = run_of(run_id, user)
        path = runs.turns_path(r)
        out: list[dict[str, Any]] = []
        if path and os.path.exists(path):
            with open(path) as fh:
                for n, line in enumerate(fh, 1):
                    try:
                        t = json.loads(line)
                    except ValueError:
                        continue
                    if t.get("ts", 0) < r["started"] - 1:
                        continue
                    if k is None:
                        t = {key: (v[:300] + "..." if isinstance(v, str) and len(v) > 300 else v)
                             for key, v in t.items() if key not in ("hops",)} | {"hops": len(t.get("hops") or [])}
                    elif n != k:
                        continue
                    out.append({"k": n, **t})
        return {"turns": out}

    @app.get("/api/runs/{run_id}/results")
    def results(run_id: int, user: User = Depends(user_of)) -> dict[str, Any]:
        from flux_loop.report import load

        r = run_of(run_id, user)
        cid, _rdir = runs.campaign(r)
        if not cid or not os.path.exists(r["db"]):
            return {"campaign": None}
        rep = load(r["db"], cid)
        rows = [{"when": x.when, "stage": x.stage, "name": x.name, "part": x.part, "whole": x.whole,
                 "metrics": x.metrics} for x in rep.rows[-500:]]
        answer = None
        ans = Path(r["log"]).with_suffix(".json")
        if ans.exists():
            try:
                answer = json.loads(ans.read_text())
            except ValueError:
                pass
        return {"campaign": cid, "objectives": rep.objectives.describe(), "rows": rows,
                "passes": [{"when": w, "conclusion": c} for w, c in rep.passes], "notes": rep.notes,
                "agent_turns": len(rep.agent_turns), "answer": answer}

    @app.get("/api/runs/{run_id}/report", response_class=HTMLResponse)
    def report(run_id: int, user: User = Depends(user_of)) -> HTMLResponse:
        from flux_loop.report import load, render

        r = run_of(run_id, user)
        cid, _rdir = runs.campaign(r)
        if not cid:
            raise HTTPException(404, "no record yet")
        return HTMLResponse(render(load(r["db"], cid)),
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
