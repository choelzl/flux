"""Persist stop/restart requests against a specific start, without a browser staying open."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import HTTPException

from .models import RunOptions
from .workspace import WorkspaceError


class LoopActions:
    KEY = "loop_actions"

    def __init__(self, ctx):
        self.ctx, self.store, self.runs = ctx, ctx.store, ctx.runs
        self._halt = threading.Event()
        self._thread = None

    def resume(self):
        with self.runs.lifecycle_lock:
            if self.store.server_get(self.KEY) and (self._thread is None or not self._thread.is_alive()):
                self._halt.clear()
                self._thread = threading.Thread(target=self._watch, daemon=True)
                self._thread.start()

    def close(self):
        self._halt.set()
        if self._thread:
            self._thread.join(timeout=2)

    def cancel(self, run):
        if run:
            with self.runs.lifecycle_lock:
                pending = self.store.server_get(self.KEY, {})
                pending.pop(str(run["id"]), None)
                self.store.server_set(self.KEY, pending or None)

    def options(self, run, user):
        saved = json.loads(run.get("options") or "{}")
        options = RunOptions.model_validate({"passes": saved.get("passes"), **saved})
        if not options.document:
            argv = json.loads(run.get("argv") or "[]")
            if len(argv) > 3 and argv[1:3] == ["task", "run"]:
                options.document = str(Path(argv[3]).relative_to(self.ctx.ws(user).app(run["app"])))
            else:
                options.document = self.ctx.ws(user).meta(run["app"]).get("document")
        return options

    def request(self, run, user, actor, *, restart=False, now=False):
        with self.runs.lifecycle_lock:
            current = self.store.run(run["id"]) if run else None
            if not current or not self.runs.live(current):
                return "not running"
            options = None
            if restart:
                if reason := self.store.server_get("paused"):
                    raise HTTPException(409, f"starts are paused by an admin: {reason}")
                if user.disabled:
                    raise HTTPException(409, "the loop's owner is disabled")
                if sum(self.runs.live(r) for r in self.store.runs(user)) > self.runs.limit(user):
                    raise HTTPException(409, "active loops exceed the owner's running limit")
                options = self.options(current, user)
                self.ctx.prepare_start(current["app"], options, user.name, actor)
            request = {"kind": "restart" if restart else "stop", "actor": actor.id, "now": now,
                       "user_id": current["user_id"], "app": current["app"],
                       "options": options.model_dump() if options else None, "signalled": False}
            pending = self.store.server_get(self.KEY, {})
            pending[str(current["id"])] = request
            self.store.server_set(self.KEY, pending)
            # Persist before signalling: a server update must not lose the replacement request.
            self.resume()
            said = self.runs.stop(current, now=now, why=f"{request['kind']} requested by {actor.name}")
            request["signalled"] = said != "not running" and not said.startswith("waiting for")
            self.store.server_set(self.KEY, pending)
            return ("restarting now; the current pass is abandoned" if now else "restart scheduled after this pass") if restart else said

    def _watch(self):
        while not self._halt.wait(0.2):
            with self.runs.lifecycle_lock:
                if not self.store.server_get(self.KEY):
                    self._thread = None
                    return
                self.tick()

    def tick(self):
        """Recheck authorization and budgets at the boundary; never overlap two starts."""
        with self.runs.lifecycle_lock:
            pending = self.store.server_get(self.KEY, {})
            for ident, request in list(pending.items()):
                run = self.store.run(int(ident))
                if run and (run["user_id"], run["app"]) != (request["user_id"], request["app"]):
                    pending.pop(ident)
                    continue
                if run and self.runs.live(run):
                    if not request.get("signalled"):
                        said = self.runs.stop(run, now=request["now"], why="stop requested from the web")
                        request["signalled"] = said != "not running" and not said.startswith("waiting for")
                    continue
                pending.pop(ident)
                if not run or request["kind"] != "restart":
                    continue
                user = self.store.user(user_id=run["user_id"])
                actor = self.store.user(user_id=request["actor"])
                try:
                    latest = self.runs.latest(user, run["app"]) if user else None
                    if not latest or latest["id"] != run["id"]:
                        continue  # A manual start, reset, deletion or move supersedes this request.
                    if user.disabled or actor is None or actor.disabled:
                        raise ValueError("the owner or requesting account is disabled or missing")
                    options = RunOptions.model_validate(request["options"])
                    if options.passes is not None:
                        options.passes = max(0, options.passes - self.runs.completed_passes(run))
                        if not options.passes:
                            self.store.notify(actor.name, f"{user.name}/{run['app']}: pass budget completed; left stopped")
                            continue
                    # Added agent kinds need the same registry as an HTTP request.
                    from flux_loop.agent import added_agents
                    from .agents import added_kinds

                    with added_agents(lambda: added_kinds(self.store)):
                        self.ctx.start_loop(run["app"], options, user.name, actor)
                    self.store.audit(actor.name, "restart", f"{user.name}/{run['app']}")
                    self.store.notify(actor.name, f"{user.name}/{run['app']} restarted", kind="ok")
                except (HTTPException, ValueError, WorkspaceError, OSError) as exc:
                    why = str(exc.detail if isinstance(exc, HTTPException) else exc)
                    self.store.audit(actor.name if actor else "", "restart failed", f"{run['app']}: {why}")
                    if actor:
                        self.store.notify(actor.name, f"{run['app']} could not restart: {why}", kind="warn")
            self.store.server_set(self.KEY, pending or None)
