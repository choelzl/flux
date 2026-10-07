"""A user's loops (D684, D689): their own maintenance (D885), variables and advanced settings (D697),
sharing (D701), the list, uploads and files (D700), a problem written or revised by an agent
(D704), questions about a loop (D705), the document and the configurator (D686), the check and
the preflight (D693), a new, an empty (D825) or a cloned (D824) loop, start and stop, and notes."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from .models import (
    DocText, FileText, RunOptions, Stop, NoteIn, DocSave, AskIn, ShareIn, EnvVar, Advanced, EmptyIn, CloneIn, MoveIn,
)
from .runs import ADVANCED, advanced, home_ready, sandbox_config, machine_env, run_env, sandbox_env
from .store import User
from .workspace import Changed, Exists, Workspace, WorkspaceError


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """The loops' routes, each change through `editor` and each read through `reader` or `loop_of` (D888)."""
    store, runs, sandbox, authoring, asks, maintenance = ctx.store, ctx.runs, ctx.sandbox, ctx.authoring, ctx.asks, ctx.maintenance
    user_of, admin_of, ws, access, reader, editor, fail, loop_of = (ctx.user_of, ctx.admin_of, ctx.ws, ctx.access, ctx.reader,
                                                                     ctx.editor, ctx.fail, ctx.loop_of)
    _env_list, _set_env, _rules, _summary = ctx.env_list, ctx.set_env, ctx.rules, ctx.summary
    agents_gate, author_agent, check_author = ctx.agents_gate, ctx.author_agent, ctx.check_author
    from .maintenance import TASKS

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

    @app.get("/api/apps/{name}/env")
    def loop_env(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """A loop's variables, with the user's and the server's under them, and its advanced settings."""
        _w, whose, _d, _run = loop_of(name, user, owner)
        return {"loop": _env_list(f"loop:{whose.name}:{name}"), "user": _env_list(f"user:{whose.id}"),
                "server": [] if whose.external else ctx.inherited_list("global"),   # D734, D925: names only
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
        got = {k: v for k, v in body.model_dump().items() if v not in (None, "", []) and not (k == "sandbox" and v is True)
               and not (k in ("parallel", "raw_network") and v is False)}
        if got.get("mounts"):
            from .admin import cache_root
            from .runs import check_mounts

            try:                                                  # D936: refused before it is kept
                got["mounts"] = check_mounts(got["mounts"], store.data, _d, cache_root())
            except ValueError as exc:
                raise HTTPException(400, f"mounts: {exc}") from exc
        before = advanced(store, whose.name, name).get("mounts") or []
        store.server_set(f"adv:{whose.name}:{name}", got or None)
        store.audit(a.name, "advanced settings", f"{whose.name}/{name}: {json.dumps(got) or 'defaults'}")
        if before != (got.get("mounts") or []):
            from .runs import mounts_said

            store.audit(a.name, "sandbox mounts", f"{whose.name}/{name}: {mounts_said(got) or 'none'}")     # D936
        return {"advanced": got}

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

    # ---- applications
    @app.get("/api/apps")
    def apps(user: User = Depends(user_of)) -> list[dict[str, Any]]:
        """The user's loops, each running or not (D689), the most recently active first."""
        w = ws(user)
        last = store.latest_runs(user)                    # D918: every loop's latest start in one query
        out = [{**a, **runs.state(user, a["name"], last.get(a["name"])), "summary": _summary(w, user, a["name"], last.get(a["name"]))}
               for a in w.apps()]
        out.sort(key=lambda a: (not a["running"], -(a.get("last_active") or 0), a["name"]))
        return out

    @app.post("/api/apps")
    async def upload(name: str = Form(...), files: list[UploadFile] = File(...),
                     replace: bool = Form(False), user: User = Depends(user_of)) -> dict[str, Any]:
        got = [(f.filename or "file", await f.read()) for f in files]
        try:
            meta = ws(user).create(name, got, replace=replace)
        except Exists as exc:                              # D906: only an explicit `replace` replaces
            raise HTTPException(409, str(exc)) from exc
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
        agents_gate(whose, author_agent(author), name)                      # D751, D769: the owner's agents; D923: in this loop
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
        except Exists as exc:                              # D906: a name taken is a conflict, never replaced
            raise HTTPException(409, str(exc)) from exc
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

    @app.get("/api/apps/{name}/file")
    def app_file(name: str, path: str, download: bool = False, raw: bool = False, owner: str | None = None, user: User = Depends(user_of)):
        """A file: text as a preview of at most TEXT_MAX bytes, `X-Flux-Truncated` and its whole
        size said when cut (D907); a download, or a file not text, streamed whole in pieces."""
        from urllib.parse import quote

        w = reader(user, owner, name)[0]
        try:
            data, is_text, size = w.read(name, path)
        except WorkspaceError as exc:
            raise fail(exc) from exc
        if download or raw or not is_text:
            def pieces():
                with w.open_file(name, path) as (fh, _size):
                    while chunk := fh.read(1 << 20):
                        yield chunk
            leaf = Path(path).name
            ascii_leaf = leaf.encode("ascii", "replace").decode().replace('"', "_")
            return StreamingResponse(pieces(), media_type="text/plain; charset=utf-8" if raw and is_text and not download else "application/octet-stream", headers={
                "Content-Disposition": f"{'inline' if raw and is_text and not download else 'attachment'}; filename=\"{ascii_leaf}\"; filename*=UTF-8''{quote(leaf)}",
                "Content-Length": str(size)})
        cut = size > len(data)
        try:
            rev = w.item(name, path)["revision"]                  # D908: a save names what it read
        except WorkspaceError:
            rev = ""
        return Response(data, media_type="text/plain; charset=utf-8",
                        headers={"X-Flux-Size": str(size), "X-Flux-Revision": rev, **({"X-Flux-Truncated": "1"} if cut else {})})

    @app.get("/api/apps/{name}/inputs")
    def list_inputs(name: str, owner: str | None = None, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        """The loop's own files (D696): what the configurator edits beside the document."""
        w, _whose = reader(user, owner, name)
        try:
            return w.inputs(name)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc

    def _mutation_failed(exc: WorkspaceError) -> HTTPException:
        """D908: a name taken or an item changed since it was read is a conflict; the rest refused."""
        return HTTPException(409, str(exc)) if isinstance(exc, (Exists, Changed)) else fail(exc)

    @app.get("/api/apps/{name}/item")
    def file_item(name: str, path: str = "", owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """What a file, folder or link is (D908), with what this user may do with it and why not."""
        w, _whose, perm = access(user, owner, name)
        try:
            got = w.item(name, path)
        except WorkspaceError as exc:
            raise HTTPException(404, str(exc)) from exc
        why = "you may watch this loop, not change it" if perm == "watch" else got["protected"]
        return {**got, "can": {k: not why for k in ("rename", "move", "delete")}, "why": why}

    @app.get("/api/apps/{name}/item/size")
    def file_item_size(name: str, path: str = "", owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """A folder's contents in all (D908): asked for when it is selected, bounded, never on a list."""
        try:
            return reader(user, owner, name)[0].folder_size(name, path)
        except WorkspaceError as exc:
            raise fail(exc) from exc

    @app.post("/api/apps/{name}/move")
    def move_item(name: str, body: MoveIn, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """A rename or a move in the loop (D908): one no-clobber step, a folder with its contents."""
        w, whose = editor(user, owner, name)
        try:
            got = w.move(name, body.path, body.to, body.revision)
        except WorkspaceError as exc:
            raise _mutation_failed(exc) from exc
        store.audit(user.name, "move file", f"{whose.name}/{name}: {body.path} -> {got['path']}")
        return {"ok": f"{body.path} is now {got['path']}", **got}

    @app.delete("/api/apps/{name}/file")
    def delete_file(name: str, path: str, recursive: bool = False, revision: str | None = None, owner: str | None = None,
                    user: User = Depends(user_of)) -> dict[str, str]:
        """A file or a link deleted (a link as the link, D905); a folder with `recursive` (D908)."""
        try:
            got = editor(user, owner, name)[0].remove(name, path, recursive=recursive, rev=revision)
        except WorkspaceError as exc:
            raise _mutation_failed(exc) from exc
        store.audit(user.name, "delete file", f"{name}/{path}" + ("" if got["kind"] == "file" else f" (a {got['kind']})"))
        return {"ok": f"{path} deleted", **got}

    @app.put("/api/apps/{name}/file")
    def put_file(name: str, path: str, body: FileText, revision: str | None = None, owner: str | None = None,
                 user: User = Depends(user_of)) -> dict[str, str]:
        """A file written whole; with `revision`, only over the file as it was read (D908)."""
        w = editor(user, owner, name)[0]
        try:
            if revision:
                w._check_rev(w.entry(name, path), revision, path)
            w.write(name, path, body.text)
            rev = w.item(name, path)["revision"]
        except FileNotFoundError as exc:
            raise HTTPException(409, f"{path!r} is gone since you opened it") from exc
        except WorkspaceError as exc:
            raise _mutation_failed(exc) from exc
        store.audit(user.name, "edit", f"{name}/{path}")
        return {"ok": path, "revision": rev}

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

        from flux_loop.document import confined, task_in

        d = editor(user, owner, name)[0].app(name)
        try:
            raw = yaml.safe_load(body.text)
            if not isinstance(raw, dict):
                return {"ok": False, "error": "the document is not a mapping of keys (statement:, flow:, ...)"}
            with confined(d):                                 # D905: nothing read outside the loop
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
        last = store.latest_runs(user)                    # D918: one query for the user's loops
        out = [runs.state(user, a["name"], last.get(a["name"])) for a in ws(user).apps()]
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
            from flux_loop.document import confined

            with confined(d):                              # D905: the host reads nothing outside the loop
                needs = agents_used(load_task(str(d / meta["document"])))
        except Exception:  # noqa: BLE001 -- a document the run itself will refuse, saying why
            needs = []
            from flux_loop.migrate import migrate_loop

            older = [x for x in migrate_loop(d)["documents"] if x["file"] == meta["document"] and x["status"] != "current"]
            if older:                                  # D811: of an earlier form -- said here, with what to do
                raise HTTPException(409, f"{meta['document']} is of an earlier form ({len(older[0]['said'])} change(s) to make"
                                         + (", and some need a person" if older[0]["manual"] else "")
                                         + "): an admin migrates it in Admin › Loops › Migrate old documents")
        agents_gate(whose, needs, name)                # D923: of the configuration this loop runs it with
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
