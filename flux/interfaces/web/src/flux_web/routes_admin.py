"""The admin's pages (D695): every loop, the machine and its history (D699), the containers and
caches, scheduled maintenance (D885), notifications (D846), the pause and the running limits, the
sandbox's settings (D698), the audit, this Flux's applications (D700), old documents migrated
(D811), Insights (D766), the stderr masks (D850) and past turns priced (D841)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from .models import (
    MaintenanceSet, MaintenanceRun, Clean, Paused, Limit, NoticeIn, ForgetIn, MasksIn, StopAll, MigrateIn,
    SandboxConfig,
)
from .runs import login_path
from .store import User
from .workspace import Workspace, WorkspaceError


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """The admin's routes; the minute's sample of the machine goes into `app.state` (D888)."""
    store, runs, sandbox, history, maintenance = ctx.store, ctx.runs, ctx.sandbox, ctx.history, ctx.maintenance
    admin_of, ws, fail = ctx.admin_of, ctx.ws, ctx.fail
    _summary, _rules, _masks, _all_loops, retest_due = ctx.summary, ctx.rules, ctx.masks, ctx.all_loops, ctx.retest_due
    from .maintenance import TASKS

    app.state.sample = lambda: _sample()

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

    @app.get("/api/audit")
    def audit(_a: User = Depends(admin_of)) -> list[dict[str, Any]]:
        store.take_refusals()                       # D708: what the sandboxes refused, up to now
        return store.audit_log()

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
