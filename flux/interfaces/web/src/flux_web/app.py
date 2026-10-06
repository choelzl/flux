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
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import routes_accounts, routes_agents
from .models import (
    DocText, FileText, RunOptions, Stop, NoteIn, DocSave, MaintenanceSet, MaintenanceRun, Clean, Paused, Limit,
    NoticeIn, ForgetIn, MasksIn, StopAll, AskIn, ShareIn, EnvVar, Advanced, EmptyIn, CloneIn, MigrateIn,
    SandboxConfig,
)
from .runs import ADVANCED, HOST_RULE, RunManager, advanced, home_ready, sandbox_config, login_path, loop_files, machine_env, run_env, sandbox_env
from .store import Store, User
from .workspace import Workspace, WorkspaceError

COOKIE = "flux_session"
STATIC = Path(__file__).parent / "static"
#: The loop crafter's script, style and tool catalog (website/docs/assets), for the configurator (D686)
CRAFTER = Path(os.environ.get("FLUX_CRAFTER_ASSETS") or Path(__file__).resolve().parents[5] / "website" / "docs" / "assets")


def journal_messages(path: str, off: int, ino: int, since: float, kind: str = "events") -> tuple[list[str], int]:
    """The journal from byte `off` as server-sent messages, and the offset after (D855). A slice is
    one message whose data is its events -- compacted (D762) and from `since` on -- and whose id is
    the slice's end: a client that reconnects with it resumes after what it received whole. One
    message per event, each with the slice's end, skipped the rest of a slice when a client got
    only the first (and compaction merges a phase's updates into its last, so a per-event cursor
    cannot be made safe)."""
    from flux_loop.journal import compact, read_events

    events, new = read_events(path, off, limit=4 << 20)       # D759: in slices
    got = [e for e in compact(events) if e.get("t", 0) >= since]
    if not got and new == off:
        return [], new
    return [f"id: {ino}-{new}\nevent: {kind}\ndata: {json.dumps(got)}\n\n"], new


def create_app(data: str | Path, *, sandbox: bool = True, secure_cookie: bool = False, max_running: int = 4) -> FastAPI:
    store = Store(data)
    runs = RunManager(store, sandbox=sandbox, max_running=max_running)
    app = FastAPI(title="Flux", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.store, app.state.runs = store, runs
    from .authoring import Authoring

    authoring = Authoring()                         # D704: problems written by an agent
    app.state.authoring = authoring
    from .asks import Asks

    asks = Asks()                                   # D705: questions about a loop, answered by an agent
    app.state.asks = asks
    from .history import History

    history = History(store.path)                    # D699: the machine over time, in the server's database
    app.state.history = history
    app.state.sample = lambda: _sample()
    from .maintenance import TASKS, Maintenance

    def _all_loops() -> list[tuple[str, str, Path]]:
        out = []
        for u in store.users():
            w = Workspace(store.data, u.name)
            out += [(u.name, a["name"], w.root / a["name"]) for a in w.apps()]
        return out

    def _loop_live(user: str, app_name: str) -> bool:
        u = store.user(name=user)
        r = runs.latest(u, app_name) if u else None
        return bool(r and runs.live(r))

    maintenance = Maintenance(store, _loop_live, _all_loops)      # D885
    app.state.maintenance = maintenance

    # ---- guards
    SLOW_S = float(os.environ.get("FLUX_SLOW_S", "0.5"))
    @app.middleware("http")
    async def csrf(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("x-flux") != "1":
                return JSONResponse({"detail": "missing the X-Flux header"}, status_code=403)
        t0 = time.monotonic()
        resp = await call_next(request)
        took = time.monotonic() - t0                      # D774: a slow answer is said in the server's log
        if took > SLOW_S:
            print(f"flux serve: slow: {request.method} {request.url.path} {took:.2f} s", file=sys.stderr, flush=True)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.endswith("/report"):
            resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if not request.url.path.startswith("/api/"):
            # D719: the page, its scripts and styles asked again each time (a 304 when unchanged):
            # without it a browser keeps an old crafter.js or app.js after an update, by heuristic;
            # D889: app.js's modules (ui.js, loops.js, ...) too, so an update never mixes old and new
            resp.headers.setdefault("Cache-Control", "no-cache")
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
        "watch" when the owner shared it with them; "admin" for an admin, who edits anyone's (D812)."""
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
        """Whose loop a change names: one's own, one shared with this user to edit (D701), or for an
        admin anyone's (D812)."""
        w, whose, perm = access(user, owner, name)
        if perm not in ("owner", "edit", "admin"):
            raise HTTPException(403, "you may watch this loop, not change it")
        return w, whose

    def fail(exc: Exception) -> HTTPException:
        return HTTPException(400, str(exc))

    # ---- shared by the route groups (D888): the loop a call names, the variables' lists, the host
    # rules, the stderr masks, a loop in a line -- the groups' routes are in routes_*.py
    def loop_of(name: str, user: User, owner: str | None = None, edit: bool = False) -> tuple[Workspace, User, Path, dict[str, Any] | None]:
        """(workspace, whose, the application's folder, its latest start or None); `edit`: a change."""
        w, whose = editor(user, owner, name) if edit else reader(user, owner, name)
        try:
            d = w.app(name)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc
        return w, whose, d, runs.latest(whose, name)

    def _env_list(scope: str) -> list[dict[str, Any]]:
        return [{"name": k, **v} for k, v in sorted(store.env(scope).items())]

    def _set_env(scope: str, body: EnvVar, who: User, what: str) -> list[dict[str, Any]]:
        try:
            store.set_env(scope, body.name, body.value, body.secret)
        except ValueError as exc:
            raise fail(exc) from exc
        store.audit(who.name, "variable" if body.value is not None else "variable removed", f"{what}: {body.name}")
        return _env_list(scope)

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

    def _masks() -> Any:
        from .masks import Masks

        return Masks(store.server_get("stderr_masks") or [])

    def _since(designs: list[dict[str, Any]], started: Any) -> int:
        """The designs first measured since the loop's latest start (D837)."""
        from datetime import datetime

        def at(s: Any) -> float:
            try:
                return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
            except ValueError:
                return 0.0

        try:
            t0 = float(started)
        except (TypeError, ValueError):
            return 0
        return sum(1 for d in designs if at(d.get("first")) >= t0)

    def _summary(w: Workspace, whose: User, name: str) -> dict[str, Any]:
        """A loop in a line (D693): designs measured, accepted, and the decision's value on the
        first objective."""
        from .results import designs

        run = runs.latest(whose, name)
        if not run or not os.path.exists(run["db"]):
            return {"designs": 0, "accepted": 0}
        from .results import decision_doc

        try:
            decision = decision_doc(run["db"], loop_files(w.app(name))["answer"], runs.campaign(run)[0])   # D809: the latest pass's
        except WorkspaceError:
            decision = None
        try:
            got = designs(run["db"], _stages(w, name), decision, stale_s=30)      # D774: a running loop's line, every 30 s
        except Exception:  # noqa: BLE001 -- a record the list cannot read: the state alone
            return {"designs": 0, "accepted": 0}
        out: dict[str, Any] = {"designs": len(got["designs"]), "accepted": got["counts"]["accepted"],
                               "this_run": _since(got["designs"], run.get("started"))}
        dec = next((d for d in got["designs"] if d["decision"]), None)
        if dec is not None:
            lim = got["limits"][0] if got["limits"] else None
            metric = lim["metric"] if lim else (got["metrics"][0] if got["metrics"] else None)
            if metric and metric in dec["numbers"]:
                out["best"] = {"design": dec["name"], "metric": metric, "value": dec["numbers"][metric],
                               "meets": dec["meets"].get(metric)}
        return out

    def _stages(w: Workspace, name: str) -> list[dict[str, Any]]:
        """The document's stages (order, cutoffs) as the loader reads them; [] when it refuses."""
        from .configure import views

        try:
            normal = views(w.path(name, w.meta(name).get("document") or ""))["normal"] or {}
        except Exception:  # noqa: BLE001 -- the record's own order then, no cutoffs
            return []
        measure = (normal.get("flow") or {}).get("measure") or {}          # D775: a map, name -> command or settings
        return [{"name": n, **(v if isinstance(v, dict) else {})} for n, v in measure.items()]

    ctx = SimpleNamespace(store=store, runs=runs, sandbox=sandbox, secure_cookie=secure_cookie, cookie=COOKIE, authoring=authoring,
                          asks=asks, history=history, maintenance=maintenance, user_of=user_of, admin_of=admin_of, ws=ws,
                          access=access, reader=reader, editor=editor, fail=fail, loop_of=loop_of, env_list=_env_list,
                          set_env=_set_env, rules=_rules, masks=_masks, summary=_summary, stages=_stages)
    routes_accounts.register(app, ctx)
    routes_agents.register(app, ctx)
    agents_gate, author_agent, check_author, retest_due = ctx.agents_gate, ctx.author_agent, ctx.check_author, ctx.retest_due

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
        """One minute's sample of the machine (D699); the sandboxes' refusals into the audit (D708);
        an agent's daily test, when one is due (D807)."""
        try:
            store.take_refusals()
        except Exception:  # noqa: BLE001 -- the sample goes on
            pass
        try:
            retest_due()
        except Exception:  # noqa: BLE001 -- the sample goes on
            pass
        from flux_cli.sandbox import _local

        from . import admin as adm

        try:                                         # D885: the scheduled clean-up, the containers' reaper (D768) among it
            maintenance.tick()
        except Exception:  # noqa: BLE001 -- the sample goes on
            pass

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

    @app.get("/api/admin/token-rate")
    def token_rate(hours: float = 24, _a: User = Depends(admin_of)) -> dict[str, Any]:
        """Tokens per second over every loop (D838), in and out, agents' and Flux's model's."""
        from . import insights

        hours = max(0.25, min(hours, 168))
        return {"hours": hours, "samples": insights.token_rate(insights.turns(store, runs), hours)}

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

    # ---- Admin › Maintenance (D885): scheduled clean-up, Gitea's cron tasks
    @app.get("/api/admin/maintenance")
    def get_maintenance(a: User = Depends(admin_of)) -> dict[str, Any]:
        return {"tasks": maintenance.view(), "loops": [f"{u}/{n}" for u, n, _d in _all_loops()]}

    @app.put("/api/admin/maintenance/{key}")
    def put_maintenance(key: str, body: MaintenanceSet, a: User = Depends(admin_of)) -> dict[str, Any]:
        if key not in TASKS:
            raise HTTPException(404, f"no task {key}")
        try:
            got = maintenance.set_config(key, body.on, body.every_h, body.params)
        except ValueError as exc:
            raise fail(exc) from exc
        store.audit(a.name, "maintenance settings", f"{TASKS[key].title}: {json.dumps(got)}")
        return got

    @app.post("/api/admin/maintenance/{key}/run")
    def run_maintenance(key: str, body: MaintenanceRun, a: User = Depends(admin_of)) -> dict[str, Any]:
        if key not in TASKS:
            raise HTTPException(404, f"no task {key}")
        loop = tuple(body.loop.split("/", 1)) if body.loop else None
        if loop is not None and len(loop) != 2:
            raise HTTPException(400, "loop: user/name")
        try:
            return maintenance.run(key, by=a.name, loop=loop)
        except ValueError as exc:
            raise fail(exc) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/apps/{name}/maintenance")
    def loop_maintenance(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """The loop's own clean-up (D885): what its owner may run on it now."""
        _w, whose = editor(user, owner, name)
        tag = f"{whose.name}/{name}"
        return {"tasks": [{"key": t["key"], "title": t["title"], "what": t["what"], "running": t["running"],
                           "last": next((r for r in t["runs"] if r.get("loop") == tag), None)}
                          for t in maintenance.view() if t["per_loop"]]}

    @app.post("/api/apps/{name}/maintenance/{key}")
    def run_loop_maintenance(name: str, key: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        _w, whose = editor(user, owner, name)
        if key not in TASKS or not TASKS[key].per_loop:
            raise HTTPException(404, f"no task {key} for a loop")
        try:
            return maintenance.run(key, by=user.name, loop=(whose.name, name))
        except ValueError as exc:
            raise fail(exc) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/admin/notify")
    def admin_notify(body: NoticeIn, a: User = Depends(admin_of)) -> dict[str, Any]:
        """A notification from the admin (D846): to every user, or those named."""
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "a notification needs a text")
        names = {u.name for u in store.users() if not u.disabled}
        to = [n for n in body.to if n in names] if body.to else sorted(names)
        if body.to and len(to) != len(set(body.to)):
            raise HTTPException(400, f"no such user: {', '.join(sorted(set(body.to) - names))}")
        kind = body.kind if body.kind in ("info", "warn", "bad", "ok") else "info"
        for n in to:
            store.notify(n, f"{a.name}: {text[:500]}", "", kind)
        store.audit(a.name, "notification", f"to {', '.join(to) if body.to else 'everyone'}: {text[:200]}")
        return {"sent": len(to)}

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

    @app.get("/api/apps/{name}/env")
    def loop_env(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """A loop's variables, with the user's and the server's under them, and its advanced settings."""
        _w, whose, _d, _run = loop_of(name, user, owner)
        return {"loop": _env_list(f"loop:{whose.name}:{name}"), "user": _env_list(f"user:{whose.id}"),
                "server": [] if whose.external else _env_list("global"),       # D734: not under an external owner's runs
                "advanced": advanced(store, whose.name, name), "advanced_said": ADVANCED,
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
        got = {k: v for k, v in body.model_dump().items() if v not in (None, "") and not (k == "sandbox" and v is True)
               and not (k == "parallel" and v is False)}
        store.server_set(f"adv:{whose.name}:{name}", got or None)
        store.audit(a.name, "advanced settings", f"{whose.name}/{name}: {json.dumps(got) or 'defaults'}")
        return {"advanced": got}

    @app.get("/api/admin/sandbox")
    def get_sandbox(_a: User = Depends(admin_of)) -> dict[str, Any]:
        """What every sandbox gets (D698), and the server user's login PATH to choose from."""
        return {"config": {**SandboxConfig().model_dump(), **(store.server_get("sandbox") or {})}, "login_path": login_path(),
                "path": os.environ.get("PATH", "").split(os.pathsep), "home": os.path.expanduser("~"), "sandboxed": sandbox}

    @app.put("/api/admin/sandbox")
    def put_sandbox(body: SandboxConfig, a: User = Depends(admin_of)) -> dict[str, Any]:
        if body.network not in ("open", "allowlist"):
            raise HTTPException(400, "network: open or allowlist")
        for d in body.path:
            if not d.startswith("/"):
                raise HTTPException(400, f"{d!r}: a PATH directory is absolute")
        for rel in body.home_seed:
            r = rel.strip().removeprefix("~/").strip("/")
            if not r or r.startswith("/") or ".." in r.split("/"):
                raise HTTPException(400, f"{rel!r}: a path inside the home folder, such as .config/opencode")
        cfg = body.model_dump()
        cfg["allow"] = _rules(body.allow)
        cfg["home_seed"] = [r.strip().removeprefix("~/").strip("/") for r in body.home_seed if r.strip()]
        cfg["path"] = [d.strip() for d in body.path if d.strip()]
        store.server_set("sandbox", cfg)
        store.audit(a.name, "sandbox settings", f"network {cfg['network']}: {', '.join(cfg['allow']) or '-'}; "
                    f"PATH +{len(cfg['path'])}{' +login' if cfg['login_path'] else ''}; homes start with {len(cfg['home_seed'])} path(s)")
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
        before = store.shares(user.name, name).get(other.name)
        try:
            got = store.set_share(user.name, name, other.name, body.perm)
        except ValueError as exc:
            raise fail(exc) from exc
        href = f"#/u/{user.name}/app/{name}"
        if body.perm and body.perm != before:             # D702: the user is told
            store.notify(other.name, f"{user.name} shared {name} with you to {body.perm}", href, "ok")
        elif not body.perm and before:
            store.notify(other.name, f"{user.name} stopped sharing {name} with you", "", "warn")
        store.audit(user.name, "share" if body.perm else "unshare", f"{name} with {other.name}: {body.perm or '-'}")
        return {"shares": [{"user": u, "perm": p} for u, p in sorted(got.items())]}

    @app.delete("/api/apps/{name}/shares/me")
    def leave_share(name: str, owner: str, user: User = Depends(user_of)) -> dict[str, str]:
        """A loop shared with this user, left by them (D702); its owner is told."""
        o = store.user(name=owner)
        if o is None or user.name not in store.shares(o.name, name):
            raise HTTPException(404, "this loop is not shared with you")
        store.set_share(o.name, name, user.name, None)
        store.notify(o.name, f"{user.name} left {name}", f"#/app/{name}/settings", "info")
        store.audit(user.name, "left a share", f"{o.name}/{name}")
        return {"ok": f"you left {o.name}'s {name}"}

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
        store.take_refusals()                       # D708: what the sandboxes refused, up to now
        return store.audit_log()

    # ---- applications
    @app.get("/api/apps")
    def apps(user: User = Depends(user_of)) -> list[dict[str, Any]]:
        """The user's loops, each running or not (D689), the most recently active first."""
        w = ws(user)
        out = [{**a, **runs.state(user, a["name"]), "summary": _summary(w, user, a["name"])} for a in w.apps()]
        out.sort(key=lambda a: (not a["running"], -(a.get("last_active") or 0), a["name"]))
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

    @app.delete("/api/apps/{name}/part")
    def drop_part(name: str, path: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, str]:
        """A file sent in parts, cancelled (D702): its partial file goes."""
        try:
            editor(user, owner, name)[0].drop_part(name, path)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        return {"ok": f"{path}: the part sent is gone"}

    # ---- a problem written or revised by an agent (D704)
    def _author_env(whose: User, name: str, by: User | None = None, author: Any = None) -> dict[str, str]:
        agents_gate(whose, author_agent(author))                            # D751, D769: the owner's agents
        home_ready(store, whose)
        env = {**run_env(store, whose, name), "FLUX_SANDBOX_APP": f"{whose.name}.{name}", "PYTHONUNBUFFERED": "1"}
        adv = advanced(store, whose.name, name)
        sandbox_env(env, sandbox, adv)
        machine_env(env, sandbox_config(store), adv)
        return env

    async def _attach(d: Path, files: list[UploadFile] | None) -> list[Path]:
        from .authoring import ATTACHED
        from .workspace import safe_rel

        out = []
        for f in files or []:
            data = await f.read()
            if not f.filename or not data:
                continue
            target = d / ATTACHED / safe_rel(f.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            from .confine import replace

            target = replace(target, data, d)                    # D852: never written through a link
            out.append(target)
        if sum(p.stat().st_size for p in out) > 256 * 2 ** 20:
            raise HTTPException(400, "at most 256 MB of files for the agent to read")
        return out

    @app.post("/api/apps/new-by-agent")
    async def new_by_agent(name: str = Form(...), prompt: str = Form(...), author: str = Form("opencode"),
                           files: list[UploadFile] | None = File(None), user: User = Depends(user_of)) -> dict[str, Any]:
        """A new loop whose problem an agent writes from a description and files (D704)."""
        if not prompt.strip():
            raise HTTPException(400, "say what the loop should do")
        check_author(author)
        w = ws(user)
        try:
            d = w.create_empty(name)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        try:
            got = await _attach(d, files)
            authoring.start(app_dir=d, workspace=w, name=name, prompt=prompt, author=author, env=_author_env(user, name, None, author),
                            attachments=got, revise=None, by=user.name)
        except (ValueError, HTTPException) as exc:
            shutil.rmtree(d, ignore_errors=True)
            raise exc if isinstance(exc, HTTPException) else fail(exc) from exc
        store.audit(user.name, "loop by an agent", f"{name}: {author}")
        return {"name": name, "ok": f"{name}: the agent is writing its problem"}

    @app.post("/api/apps/{name}/author")
    async def revise_by_agent(name: str, prompt: str = Form(...), author: str = Form("opencode"),
                              files: list[UploadFile] | None = File(None), owner: str | None = None,
                              user: User = Depends(user_of)) -> dict[str, Any]:
        """An agent revises the loop's problem as told (D704); not while the loop runs."""
        w, whose, d, run = loop_of(name, user, owner, edit=True)
        if run and runs.live(run):
            raise HTTPException(409, "stop the loop first: its problem is in use")
        if not prompt.strip():
            raise HTTPException(400, "say what to change")
        check_author(author)
        try:
            got = await _attach(d, files)
            authoring.start(app_dir=d, workspace=w, name=name, prompt=prompt, author=author, env=_author_env(whose, name, user, author),
                            attachments=got, revise=w.meta(name).get("document"), by=user.name)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        store.audit(user.name, "problem revised by an agent", f"{whose.name}/{name}: {author}")
        return {"ok": "the agent is revising the problem"}

    @app.get("/api/apps/{name}/author")
    def author_state(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        w, _whose, d, _run = loop_of(name, user, owner)
        return authoring.state(d, w, name)

    @app.post("/api/apps/{name}/author/stop")
    def author_stop(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, str]:
        _w, _whose, d, _run = loop_of(name, user, owner, edit=True)
        return {"ok": authoring.stop(d)}

    # ---- questions about a loop, answered by an agent (D705)
    def _ask_id(ident: str) -> str:
        if not re.fullmatch(r"\d{8}-\d{6}(-\d+)?", ident):
            raise HTTPException(404, "no such question")
        return ident

    @app.get("/api/apps/{name}/asks")
    def list_asks(name: str, owner: str | None = None, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        _w, _whose, d, _run = loop_of(name, user, owner)
        return asks.list(d)

    @app.post("/api/apps/{name}/asks")
    def ask_about(name: str, body: AskIn, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """An agent reads the loop (its files, its record, its log) and answers; it changes nothing."""
        _w, whose, d, _run = loop_of(name, user, owner, edit=True)
        if not body.question.strip():
            raise HTTPException(400, "ask something")
        check_author(body.author)
        try:
            ident = asks.start(app_dir=d, question=body.question, author=body.author, env=_author_env(whose, name, user, body.author), by=user.name)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        store.audit(user.name, "asked about a loop", f"{whose.name}/{name}: {body.author}")
        return {"id": ident, "ok": "the agent is reading the loop"}

    @app.post("/api/apps/{name}/asks/{ident}/stop")
    def stop_ask(name: str, ident: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, str]:
        _w, _whose, d, _run = loop_of(name, user, owner, edit=True)
        return {"ok": asks.stop(d, _ask_id(ident))}

    @app.delete("/api/apps/{name}/asks/{ident}")
    def forget_ask(name: str, ident: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, str]:
        _w, _whose, d, _run = loop_of(name, user, owner, edit=True)
        try:
            asks.forget(d, _ask_id(ident))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": "forgotten"}

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
            return {"document": doc, "before": path.read_text(), "after": merged(body.text, views(path)["raw"] or {}, body.kept)}
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
            text = merged(body.text, views(path)["raw"] or {}, body.kept)
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
    def app_files(name: str, path: str = "", ignored: bool = False, owner: str | None = None,
                  user: User = Depends(user_of)) -> list[dict[str, Any]]:
        """A folder of the loop; `ignored`: also what its .gitignore ignores, marked (D703)."""
        try:
            return reader(user, owner, name)[0].files(name, path, show_ignored=ignored)
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
    def check(name: str, owner: str | None = None, document: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        w, whose = editor(user, owner, name)
        try:
            d = w.app(name)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc
        doc = document or w.meta(name).get("document")
        if document and document not in [x["path"] for x in w.documents(name)]:
            raise HTTPException(400, f"{name} has no problem {document!r}")
        home_ready(store, whose)
        env = {**run_env(store, whose, name), "FLUX_SANDBOX_APP": f"{whose.name}.{name}"}   # the owner's loop: its settings and logins (D769)
        adv = advanced(store, whose.name, name)
        sandbox_env(env, sandbox, adv)
        machine_env(env, sandbox_config(store), adv)
        digest = w.inputs_digest(name)
        try:
            r = subprocess.run([shutil.which("flux") or sys.argv[0], "task", "check", str(d / doc)], cwd=str(d), env=env,
                               capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
            ok, output = r.returncode == 0, (r.stdout + r.stderr)[-20000:]
        except subprocess.TimeoutExpired:
            ok, output = False, "the check ran past 600 s"
        w.set_meta(name, last_check={"digest": digest, "ok": ok, "t": time.time(), "output": output, "document": doc})   # D693
        return {"ok": ok, "output": output}

    # ---- D811: documents of an earlier form, brought to today's by an admin
    def _running(u: User, name: str) -> bool:
        run = runs.latest(u, name)
        return bool(run) and runs.live(run)

    def _loops_documents() -> list[dict[str, Any]]:
        from flux_loop.migrate import migrate_loop

        out = []
        for u in store.users():
            w = Workspace(store.data, u.name)
            for a in w.apps():
                try:
                    got = migrate_loop(w.app(a["name"]))
                except Exception as exc:  # noqa: BLE001 -- one loop's trouble, said; the others listed
                    got = {"documents": [{"file": "?", "to": "?", "status": "failed", "said": [], "manual": [], "text": "",
                                          "why": f"{type(exc).__name__}: {exc}"[:500]}]}
                docs = [{k: x[k] for k in ("file", "to", "status", "said", "manual", "why", "text")} for x in got["documents"]]
                out.append({"user": u.name, "app": a["name"], "documents": docs, "running": _running(u, a["name"])})
        return out

    @app.get("/api/admin/documents")
    def admin_documents(_a: User = Depends(admin_of)) -> dict[str, Any]:
        """Every loop's documents, and what each would change to be of today's form (nothing written)."""
        loops = _loops_documents()
        return {"loops": [x for x in loops if any(d["status"] != "current" for d in x["documents"])],
                "total": len(loops), "documents": sum(len(x["documents"]) for x in loops)}

    @app.post("/api/admin/documents/migrate")
    def admin_migrate(body: MigrateIn, a: User = Depends(admin_of)) -> dict[str, Any]:
        """One loop's documents (`user` and `app`), or every loop's, brought to today's form: each
        written only when it loads, the original kept as `<file>.orig`; a running loop is left
        alone; a renamed record or document is followed by its runs and by the loop."""
        from flux_loop.migrate import migrate_loop

        done = []
        for u in store.users():
            if body.user and u.name != body.user:
                continue
            w = Workspace(store.data, u.name)
            for app_ in w.apps():
                name = app_["name"]
                if body.app and name != body.app:
                    continue
                if _running(u, name):
                    done.append({"user": u.name, "app": name, "documents": [], "why": "running: stop it first"})
                    continue
                folder = w.app(name)
                if all(x["status"] == "current" for x in migrate_loop(folder)["documents"]):
                    continue
                got = migrate_loop(folder, write=True)
                renamed = {Path(k).name: Path(v).name for k, v in got["moves"].items() if Path(k).parent == folder}
                doc = w.meta(name).get("document")
                if doc in renamed:
                    w.set_meta(name, document=renamed[doc], id=name)
                store.move_paths(got["moves"])
                migrated = [x["file"] for x in got["documents"] if x["status"] == "migrated"]
                if migrated:
                    store.audit(a.name, "document migrated", f"{u.name}/{name}: {', '.join(migrated)}")
                done.append({"user": u.name, "app": name, "why": "",
                             "documents": [{k: x[k] for k in ("file", "to", "status", "manual", "why")} for x in got["documents"]]})
        if body.app and not done:
            raise HTTPException(404, f"nothing to migrate in {body.user}/{body.app}")
        return {"done": done, "migrated": sum(d["status"] == "migrated" for x in done for d in x["documents"])}

    @app.post("/api/apps/new-empty")
    def new_empty(body: EmptyIn, user: User = Depends(user_of)) -> dict[str, Any]:
        """A loop's baseline (D825) -- the skeleton problem.yaml, the README of the folder's parts, an
        empty library/ -- to fill in with the configurator. Nothing of a case."""
        from flux_cli.commands import baseline_files

        name = body.name.strip()
        try:
            meta = ws(user).create(name, [(rel, text.encode()) for rel, text in baseline_files(name)])
        except WorkspaceError as exc:
            raise fail(exc) from exc
        (ws(user).app(name) / "library").mkdir(exist_ok=True)
        store.audit(user.name, "empty loop", name)
        return {"name": name, **meta}

    @app.post("/api/apps/{name}/clone")
    def clone_loop(name: str, body: CloneIn, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """A loop cloned (D824) into the caller's own: any loop they can see -- their own, one shared with
        them, for an admin anyone's. Its problem, never its runs'; its workbench when asked."""
        _w, whose, d, _run = loop_of(name, user, owner)
        try:
            meta = ws(user).clone(body.to.strip(), d, workbench=body.workbench, source=f"{whose.name}/{name}")
        except WorkspaceError as exc:
            raise fail(exc) from exc
        store.audit(user.name, "clone loop", f"{whose.name}/{name} -> {body.to.strip()}" + (" (with its workbench)" if body.workbench else ""))
        return {"name": body.to.strip(), **meta}

    @app.post("/api/apps/{name}/validate")
    def validate_text(name: str, body: FileText, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """Whether a document, not yet saved, loads (D757): what Direct edit says before it writes."""
        import yaml

        from flux_loop.document import task_in

        d = editor(user, owner, name)[0].app(name)
        try:
            raw = yaml.safe_load(body.text)
            if not isinstance(raw, dict):
                return {"ok": False, "error": "the document is not a mapping of keys (statement:, flow:, ...)"}
            task_in(raw, d)
        except yaml.YAMLError as exc:
            return {"ok": False, "error": f"not YAML: {' '.join(str(exc).split())[:300]}"}
        except Exception as exc:  # noqa: BLE001 -- what the loader says is what the user reads
            return {"ok": False, "error": " ".join(str(exc).split())[:400]}
        return {"ok": True, "error": ""}

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
        if last.get("document", meta.get("document")) != meta.get("document"):
            last = {}                                  # a check of another of its problems (D787)
        return {"document": meta.get("document"), "documents": w.documents(name),"digest": digest, "changed": digest != meta.get("last_start_digest"),
                "checked": last.get("digest") == digest, "ok": bool(last.get("ok")) if last.get("digest") == digest else None,
                "output": last.get("output", "") if last.get("digest") == digest else "", "when": last.get("t"),
                "options": meta.get("last_options"), "paused": store.server_get("paused")}

    # ---- the loop: running or not; a start resumes it from its record (D689)
    @app.get("/api/apps/{name}/state")
    def loop_state(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        _w, whose, _d, _run = loop_of(name, user, owner)
        return runs.state(whose, name)

    @app.get("/api/loops")
    def loops_state(user: User = Depends(user_of)) -> list[dict[str, Any]]:
        """Every loop of the user with its state, and those shared with them (D702: `owner` set):
        what the page's notifications watch."""
        out = [runs.state(user, a["name"]) for a in ws(user).apps()]
        for owner, app_name, _perm in store.shared_with(user.name):
            o = store.user(name=owner)
            if o is not None and (Workspace(store.data, owner).root / app_name).is_dir():
                out.append({**runs.state(o, app_name), "owner": owner})
        return out

    @app.post("/api/apps/{name}/start")
    def start(name: str, body: RunOptions, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, str]:
        w, whose, d, _run = loop_of(name, user, owner, edit=True)
        meta = w.meta(name)
        if authoring.state(d).get("running"):
            raise HTTPException(409, "an agent is writing this loop's problem: start it once it is done")
        if not meta.get("document"):
            raise HTTPException(409, "this loop has no problem document yet")
        docs = w.documents(name)                   # D787: several problems: the start names one
        if body.document:
            if body.document not in [x["path"] for x in docs]:
                raise HTTPException(400, f"{name} has no problem {body.document!r}")
            meta = w.set_meta(name, document=body.document)
        elif sum(x["ok"] for x in docs) > 1:
            raise HTTPException(409, f"{name} has {sum(x['ok'] for x in docs)} problems: say which one to start ("
                                + ", ".join(x["path"] for x in docs if x["ok"]) + ")")
        try:                                       # D751: the agents it hands work to, tested by its owner (D769)
            from flux_loop import load_task
            from flux_loop.agent_check import agents_used

            needs = agents_used(load_task(str(d / meta["document"])))
        except Exception:  # noqa: BLE001 -- a document the run itself will refuse, saying why
            needs = []
            from flux_loop.migrate import migrate_loop

            older = [x for x in migrate_loop(d)["documents"] if x["file"] == meta["document"] and x["status"] != "current"]
            if older:                                  # D811: of an earlier form -- said here, with what to do
                raise HTTPException(409, f"{meta['document']} is of an earlier form ({len(older[0]['said'])} change(s) to make"
                                         + (", and some need a person" if older[0]["manual"] else "")
                                         + "): an admin migrates it in Admin › Loops › Migrate old documents")
        agents_gate(whose, needs)
        try:     # the owner's loop: their record, settings and limits; who started it is said (D701)
            from flux_loop.document import record_name

            runs.start(whose, name, d, meta["document"], record_name(d / meta["document"]), body.model_dump(), by=user)
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
        from .confine import Escape

        try:
            runs.note(d / "runs", user, body.text.strip())
        except (Escape, OSError) as exc:                  # D852: an inbox that is a link is not written
            raise HTTPException(400, "the loop's inbox is not a file of this loop") from exc
        store.audit(user.name, "note", name)
        return {"ok": "sent: it reaches the next prompt, or answers the agent's open question"}

    @app.delete("/api/apps/{name}/notes/{ident}")
    def remove_note(name: str, ident: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, str]:
        """D808: a note off the page (and out of a loop that has not read it yet)."""
        _w, _whose, d, _run = loop_of(name, user, owner, edit=True)
        from .confine import Escape

        try:
            gone = runs.forget_note(d / "runs", user, ident)
        except (Escape, OSError) as exc:
            raise HTTPException(400, "the loop's inbox is not a file of this loop") from exc
        if not gone:
            raise HTTPException(404, "no such note")
        store.audit(user.name, "note removed", name)
        return {"ok": "the note is removed"}

    @app.get("/api/apps/{name}/notes")
    def list_notes(name: str, owner: str | None = None, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        _w, _whose, d, _run = loop_of(name, user, owner)
        return runs.notes(d / "runs")

    def _confined(path: str | None, roots: Any) -> str | None:
        """D852: `path` when it is no link and lies in the loop's own folders; else None (not there)."""
        from .confine import Escape, within

        if not path or os.path.islink(path):
            return None
        try:
            within(path, *roots)
        except Escape:
            return None
        return path

    async def _follow(path_of, start_after, offset: int, request: Request, kind: str, preface: tuple[str, ...] = (),
                      roots_of=lambda: ()):
        """Server-sent events: each new line of a file, as it grows, from byte `offset`. For the
        journal, `start_after()` is when the loop's latest start began: its tree, not the last.
        D774: each look -- the run's record, the file, a slice read and parsed -- is a worker
        thread's: the event loop that answers every other request never waits on a disk or the
        store."""

        at = {"ino": offset[0], "offset": offset[1]}

        def look() -> tuple[list[str], bool]:
            path = _confined(path_of(), roots_of())             # D852: no link, nothing outside the loop
            if not (path and os.path.exists(path)):
                return [], False
            st = os.stat(path)
            ino, off = at["ino"], at["offset"]
            if st.st_ino != ino or st.st_size < off:              # D694: another file (a new start's), or cut
                if ino is not None or st.st_size < off:
                    off = 0
                ino = st.st_ino
            out: list[str] = []
            if kind == "events":
                out, new = journal_messages(path, off, ino, start_after())
            else:
                from .confine import open_read

                with open_read(path, *roots_of()) as fh:
                    fh.seek(off)
                    chunk = fh.read(256 * 1024)
                new = off + len(chunk)
                if chunk:
                    out = [f"id: {ino}-{new}\nevent: {kind}\ndata: {json.dumps(chunk.decode('utf-8', 'replace'))}\n\n"]
            moved = new != off
            at["ino"], at["offset"] = ino, new
            return out, moved

        yield "retry: 3000\n\n"
        for p in preface:                                      # D759: what the window left out, said first
            yield p
        while True:
            if await request.is_disconnected():
                return
            out, moved = await asyncio.to_thread(look)
            for line in out:
                yield line
            if moved:
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
    def events(name: str, request: Request, offset: str = "0", window: int = 0, owner: str | None = None,
                     user: User = Depends(user_of)):
        """The latest start's journal; `window` (D759): only its last that many passes, a first
        `window` event saying how many came before -- a day-long run's tree opens at once."""
        from flux_loop.journal import window_start

        _w, whose, _d, _run = loop_of(name, user, owner)
        latest = lambda: runs.latest(whose, name)                               # noqa: E731 -- a new start moves it
        at, preface = _offset(request, offset), ()
        path = _confined(runs.events_path(latest()), runs.roots(latest()))   # D852
        if window > 0 and at[1] == 0 and path and os.path.exists(path):
            got = window_start(path, window)
            if got is not None:
                at = (os.stat(path).st_ino, got[0])
                said = {"ev": "window", "before": got[1], "t": 0, "cut": got[2] is not None}
                preface = (f"event: events\ndata: {json.dumps(said)}\n\n",)
                if got[2] is not None and got[2] >= 0:         # D762: inside a pass -- its mark first
                    from .confine import open_read

                    with open_read(path, *runs.roots(latest())) as fh:
                        fh.seek(got[2])
                        preface += (f"event: events\ndata: {fh.readline().decode('utf-8', 'replace').strip()}\n\n",)
        stream = _follow(lambda: runs.events_path(latest()), lambda: (latest() or {"started": 0})["started"] - 1,
                         at, request, "events", preface, roots_of=lambda: runs.roots(latest()))
        return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/apps/{name}/live")
    def live(name: str, request: Request, owner: str | None = None, user: User = Depends(user_of)):
        """The latest start's live state (D761): `live.json` -- each running phase's latest fields
        and the standings -- sent whole each time it changes."""
        _w, whose, _d, _run = loop_of(name, user, owner)

        seen: list[Any] = [None]

        def look() -> str | None:                            # D774: a worker thread's, not the event loop's
            run = runs.latest(whose, name)
            ev = runs.events_path(run)
            path = _confined(os.path.join(os.path.dirname(ev), "live.json"), runs.roots(run)) if ev else None   # D852
            try:
                st = os.stat(path) if path else None
            except OSError:
                st = None
            if st is None or (st.st_ino, st.st_mtime_ns, st.st_size) == seen[0]:
                return None
            seen[0] = (st.st_ino, st.st_mtime_ns, st.st_size)
            try:
                from .confine import open_read

                with open_read(path, *runs.roots(run), text=True) as fh:
                    body = fh.read()
                json.loads(body)
                return _masks().body(body)                        # D850: the admin's stderr masks
            except (OSError, ValueError):
                seen[0] = None                                  # half written: read again
                return None

        async def stream():
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                body = await asyncio.to_thread(look)
                yield f"event: live\ndata: {body}\n\n" if body is not None else ": keep-alive\n\n"
                await asyncio.sleep(1.0)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/apps/{name}/log")
    def log(name: str, request: Request, offset: str = "0", tail: int = 0, owner: str | None = None,
                  user: User = Depends(user_of)):
        """The loop's one log, every start in it; `tail` (D759): only its last that many bytes, a
        first `skipped` event saying how many came before."""
        _w, _whose, d, _run = loop_of(name, user, owner)
        path = str(loop_files(d)["log"])
        at, preface = _offset(request, offset), ()
        ok = _confined(path, (str(d),))                           # D852
        if tail > 0 and at[1] == 0 and ok and os.path.exists(path) and os.path.getsize(path) > tail:
            size = os.path.getsize(path)
            from .confine import open_read

            with open_read(path, str(d)) as fh:                  # from the first whole line of the tail
                fh.seek(size - tail)
                skip = size - tail + fh.read(64 * 1024).find(b"\n") + 1
            at = (os.stat(path).st_ino, skip)
            preface = (f"event: skipped\ndata: {json.dumps({'bytes': skip})}\n\n",)
        stream = _follow(lambda: path, lambda: 0, at, request, "log", preface, roots_of=lambda: (str(d),))
        return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/apps/{name}/log/raw")
    def log_raw(name: str, owner: str | None = None, user: User = Depends(user_of)):
        _w, _whose, d, _run = loop_of(name, user, owner)
        from .confine import Escape, open_read

        path = loop_files(d)["log"]
        try:
            fh = open_read(path, str(d))                           # D852: never a link out of the loop
        except FileNotFoundError:
            raise HTTPException(404, "no log yet") from None
        except (Escape, OSError) as exc:
            raise HTTPException(400, "the log is not a file of this loop") from exc

        def chunks():
            with fh:
                while True:
                    b = fh.read(1 << 16)
                    if not b:
                        return
                    yield b

        return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.log"'})

    turn_index: dict[str, tuple[int, int, list[int], list[dict[str, Any]]]] = {}   # path -> (inode, read, offsets, summaries)
    turn_lock = threading.Lock()

    def _turn_summaries(path: str) -> tuple[list[int], list[dict[str, Any]]]:
        """D779: each turn's byte offset and its summary (long text cut, hops and steps counted),
        read as the file grows -- a day's turns are not parsed again on every look."""
        st = os.stat(path)
        with turn_lock:
            got = turn_index.get(path)
            ino, read, offsets, rows = got if got is not None and got[0] == st.st_ino and got[1] <= st.st_size \
                else (st.st_ino, 0, [], [])
            if read < st.st_size:
                with open(path, "rb") as fh:
                    fh.seek(read)
                    data = fh.read()
                at = 0
                while True:
                    nl = data.find(b"\n", at)
                    if nl < 0:
                        break                                # a line being written waits
                    line = data[at:nl]
                    offsets.append(read + at)
                    try:
                        t = json.loads(line)
                        t = {key: (v[:300] + "..." if isinstance(v, str) and len(v) > 300 else v)
                             for key, v in t.items() if key not in ("hops", "steps")} | {"hops": len(t.get("hops") or []),
                                                                                         "steps": len(t.get("steps") or [])}
                    except ValueError:
                        t = None
                    rows.append(t)
                    at = nl + 1
                read += at
            turn_index[path] = (ino, read, offsets, rows)
            return list(offsets), list(rows)

    @app.get("/api/apps/{name}/turns")
    def turns(name: str, k: int | None = None, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """The loop's model and agent turns, newest last: the last 500 summed up, or turn `k` whole."""
        _w, _whose, _d, run = loop_of(name, user, owner)
        path = runs.turns_path(run)
        if not (path and os.path.exists(path)):
            return {"turns": []}
        offsets, rows = _turn_summaries(path)
        hide = _masks()                                       # D850: the admin's stderr masks
        if k is None:
            listed = [{"k": n, **t} for n, t in enumerate(rows, 1) if t is not None]
            return {"turns": hide.fields(listed[-500:]), "total": len(listed)}
        if not 1 <= k <= len(offsets):
            return {"turns": []}
        with open(path, "rb") as fh:                         # the one turn, read from its place
            fh.seek(offsets[k - 1])
            try:
                return {"turns": hide.fields([{"k": k, **json.loads(fh.readline())}])}
            except ValueError:
                return {"turns": []}

    @app.get("/api/apps/{name}/timeline")
    def loop_timeline(name: str, start: int | None = None, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """Where one start's time went (D694): its phases as bars, and per kind of work."""
        from .timeline import timeline

        _w, _whose, _d, run = loop_of(name, user, owner)
        path = runs.events_path(run)
        if not path or not os.path.exists(path):
            return {"starts": [], "start": None, "bars": [], "kinds": [], "passes": []}
        return timeline(path, start, running=bool(runs.live(run)) if run else False)

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

    @app.get("/api/admin/insights")
    def admin_insights(days: int = 7, a: User = Depends(admin_of)) -> dict[str, Any]:
        """Admin › Insights (D766): failures, usage by day, endpoints, network refusals, disk."""
        from . import insights as ins

        days = max(1, min(int(days), 60))
        since = time.time() - days * ins.DAY
        rows = ins.turns(store, runs)
        res = resources(a)
        forgot = store.server_get("insights_forgot") or {}
        hide = _masks()
        return {"days": days, "failures": hide.fields(ins.failures(store, runs, since)), "usage": ins.usage_by_day(rows, max(days, 7)),
                "endpoints": hide.fields(ins.endpoints(rows, since, forgot.get("endpoint"))),
                "network": ins.network(str(store.refusals_file), since, forgot.get("network")),
                "disk": ins.disk(store, res["loops"])}

    @app.post("/api/admin/insights/forget")
    def insights_forget(body: ForgetIn, a: User = Depends(admin_of)) -> dict[str, Any]:
        """D850: an endpoint or a refused host out of Insights -- its uses until now; a later one brings it back."""
        if body.kind not in ("endpoint", "network") or not body.key.strip():
            raise HTTPException(400, "forget an endpoint or a network host, by its key")
        forgot = store.server_get("insights_forgot") or {}
        forgot.setdefault(body.kind, {})[body.key] = time.time()
        store.server_set("insights_forgot", forgot)
        store.audit(a.name, "insights: removed", f"{body.kind} {body.key}")
        return {"ok": True}

    @app.get("/api/admin/masks")
    def get_masks(_a: User = Depends(admin_of)) -> dict[str, Any]:
        return {"masks": store.server_get("stderr_masks") or []}

    @app.put("/api/admin/masks")
    def put_masks(body: MasksIn, a: User = Depends(admin_of)) -> dict[str, Any]:
        """D850: the stderr lines the pages leave out -- a text, or a /regular expression/."""
        from .masks import check

        try:
            got = check(body.masks)
        except ValueError as exc:
            raise fail(exc) from exc
        store.server_set("stderr_masks", got)
        store.audit(a.name, "stderr masks", f"{len(got)} pattern(s)")
        return {"masks": got}

    @app.post("/api/admin/reprice")
    def reprice(a: User = Depends(admin_of)) -> dict[str, Any]:
        """Past turns priced once at today's prices (D841); running loops skipped."""
        from .pricing import reprice as price_past

        got = price_past(store, runs)
        store.audit(a.name, "past turns priced", f"{got['turns']} turn(s) in {got['loops']} loop(s), ${got['usd']:.2f}"
                    + (f"; skipped, running: {', '.join(got['skipped'])}" if got["skipped"] else ""))
        return got

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
                from .confine import open_read

                with open_read(ans, str(d), text=True) as fh:        # D852: runs/ is the run's to write
                    answer = json.loads(fh.read())
            except (ValueError, OSError):
                pass
        from .results import decision_doc

        decision = decision_doc(run["db"], ans, cid)            # D809: the record's latest pass's, while it runs too; D840: which one
        from .results import decision_said

        decided_by = decision_said(run["db"], cid)              # D815: why, as the loop said it
        listed = designs(run["db"], _stages(_w, name), decision, limit=20000)
        objective_list = [{"metric": o.metric, "direction": o.direction, "goal": o.goal, "stage": o.stage, "unit": o.unit}
                          for o in rep.objectives]                     # for the Overview's charts (D692)
        return {"campaign": cid, "objectives": rep.objectives.describe(), "objective_list": objective_list, "rows": rows,
                "rows_total": len(rep.rows),
                "passes": [{"when": w, "conclusion": c} for w, c in rep.passes], "notes": rep.notes,
                "agent_turns": len(rep.agent_turns), "answer": answer, "decided_by": decided_by, **listed}

    @app.get("/api/apps/{name}/design")
    def design(name: str, design: str, part: str = "", key: str = "", owner: str | None = None,
               user: User = Depends(user_of)) -> dict[str, Any]:
        """One design of the loop: its source, why it failed, every stage's numbers (D690); `key`:
        which, of the designs a name was given to (D840)."""
        from flux_store import CampaignStore

        from .results import content_key

        _w, _whose, _d, run = loop_of(name, user, owner)
        if not run or not os.path.exists(run["db"]):
            raise HTTPException(404, "no record yet")
        store = CampaignStore(run["db"])
        found: dict[str, Any] = {"name": design, "part": part, "artifact": None, "trials": []}
        try:
            for camp in store.list_campaigns():
                for t in store.trials(camp["campaign_id"], status="ok"):
                    c = t.candidate or {}
                    if str(c.get("name")) != design or str(c.get("subgoal") or "") != part or (key and content_key(c) != key):
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
