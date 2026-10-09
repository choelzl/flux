"""The web API and pages (D683). Every `/api` route but login needs a session cookie; every request
that changes something also needs the `X-Flux: 1` header, which a page of another site cannot
send (CSRF). A user sees their own applications and runs; an admin also manages users and
reads every run."""

from __future__ import annotations

import re
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import routes_accounts, routes_admin, routes_agents, routes_loops, routes_results
from .models import EnvVar
from .routes_results import journal_messages  # noqa: F401 -- D888: still importable from here
from .runs import HOST_RULE, LOOKUP, RunManager, loop_files
from .store import Store, User
from .workspace import Workspace, WorkspaceError

COOKIE = "flux_session"
STATIC = Path(__file__).parent / "static"
#: The loop crafter's script, style and tool catalog (website/docs/assets), for the configurator (D686)
CRAFTER = Path(os.environ.get("FLUX_CRAFTER_ASSETS") or Path(__file__).resolve().parents[5] / "website" / "docs" / "assets")


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
    from .maintenance import Maintenance

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
        from flux_loop.agent import added_agents

        from .agents import added_kinds

        with added_agents(lambda: added_kinds(store)):    # D934: a document may name an added agent
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
        perm = store.loop_access(user, other, name)
        if perm:
            return Workspace(store.data, other.name), other, perm
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

    def runner(user: User, owner: str | None, name: str) -> tuple[Workspace, User]:
        w, whose = reader(user, owner, name)
        if not store.can_run(user, whose, name):
            raise HTTPException(403, "you may not run loops or agents here")
        return w, whose

    def creator(user: User) -> None:
        if not user.can("create_loops"):
            raise HTTPException(403, "you may not create, upload or clone loops")

    def fail(exc: Exception) -> HTTPException:
        return HTTPException(400, str(exc))

    # ---- shared by the route groups (D888): the loop a call names, the variables' lists, the host
    # rules, the stderr masks, a loop in a line -- the groups' routes are in routes_*.py
    def loop_of(name: str, user: User, owner: str | None = None, edit: bool = False,
                run: bool = False) -> tuple[Workspace, User, Path, dict[str, Any] | None]:
        """(workspace, whose, the application's folder, its latest start or None); `edit`: a change."""
        w, whose = editor(user, owner, name) if edit else reader(user, owner, name)
        if run:
            runner(user, owner, name)
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

    def _summary(w: Workspace, whose: User, name: str, run: Any = LOOKUP) -> dict[str, Any]:
        """A loop in a line (D693): designs measured, accepted, and the decision's value on the
        first objective. `run`: its latest start, when the caller has it (D918)."""
        from .results import designs

        if run is LOOKUP:
            run = runs.latest(whose, name)
        if not run or not os.path.exists(run["db"]):
            return {"designs": 0, "accepted": 0}
        from .results import decision_doc

        try:
            decision = decision_doc(run["db"], loop_files(w.app(name))["answer"], runs.campaign(run)[0])   # D809: the latest pass's
        except WorkspaceError:
            decision = None
        try:
            got = designs(run["db"], _stages(w, name), decision, stale_s=30,      # D774: a running loop's line, every 30 s
                          since=run.get("started"))
        except Exception:  # noqa: BLE001 -- a record the list cannot read: the state alone
            return {"designs": 0, "accepted": 0}
        # D901: every design, not the first page's -- 1,200 designs were said as 1,000, 1,000 this start
        out: dict[str, Any] = {"designs": got["total"], "accepted": got["counts"]["accepted"],
                               "this_run": got["this_start"], "feasible": got["feasible"]}
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
                          access=access, reader=reader, editor=editor, runner=runner, creator=creator,
                          fail=fail, loop_of=loop_of, env_list=_env_list,
                          set_env=_set_env, rules=_rules, masks=_masks, summary=_summary, stages=_stages, all_loops=_all_loops)
    # the route groups (D888); agents first: it puts the readiness gates and the retest into ctx
    routes_accounts.register(app, ctx)
    routes_agents.register(app, ctx)
    routes_admin.register(app, ctx)
    routes_loops.register(app, ctx)
    routes_results.register(app, ctx)

    # ---- pages
    if CRAFTER.is_dir():
        app.mount("/crafter-assets", StaticFiles(directory=CRAFTER), name="crafter")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app
