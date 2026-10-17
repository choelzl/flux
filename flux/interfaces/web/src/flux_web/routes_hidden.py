"""Paths hidden from a loop's AI agents (D1159): the document's `flow.knowledge.hidden`, read for the Files tab
and changed one path at a time -- by the loop's editors, inside its folder, on the audit trail."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import yaml
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .store import User
from .workspace import WorkspaceError


class HiddenIn(BaseModel):
    """A path in the loop (a file, a folder, a glob), hidden from its agents or shown to them again."""
    path: str = Field(min_length=1, max_length=1000)
    hidden: bool = True


def said(text: str) -> list[str]:
    """The paths a document's text hides, as written; [] when it says none or is not a mapping."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return []
    k = ((doc or {}).get("flow") or {}).get("knowledge") if isinstance(doc, dict) and isinstance(doc.get("flow"), dict) else None
    got = k.get("hidden") if isinstance(k, dict) else None
    got = [got] if isinstance(got, str) else got
    return [str(p) for p in got if isinstance(p, str)] if isinstance(got, list) else []


def _bare(p: str) -> str:
    return p.strip().strip("/")


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    store, user_of, reader, editor, fail = ctx.store, ctx.user_of, ctx.reader, ctx.editor, ctx.fail

    def document(w: Any, name: str) -> tuple[str, Any]:
        doc = w.meta(name).get("document")
        if not doc:
            raise HTTPException(409, "this loop has no document to say what is hidden")
        return doc, w.path(name, doc)

    @app.get("/api/apps/{name}/hidden")
    def hidden_paths(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """The paths the document hides from the loop's agents, as written."""
        w, _whose = reader(user, owner, name)
        try:
            doc = w.meta(name).get("document")
            return {"document": doc or "", "hidden": said(w.path(name, doc).read_text()) if doc else []}
        except (WorkspaceError, OSError) as exc:
            raise fail(exc) from exc

    @app.put("/api/apps/{name}/hidden")
    def set_hidden(name: str, body: HiddenIn, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """`path` added to the document's `flow.knowledge.hidden`, or taken out of it; the rest of the document as
        written. A path outside the loop, absolute, or the document itself is refused, as the loader refuses it."""
        from flux_loop.hidden import said_hidden, set_hidden_text

        w, whose = editor(user, owner, name)
        try:
            doc, path = document(w, name)
            text = path.read_text()
            now = said(text)
            want = body.path.strip()
            if body.hidden:
                said_hidden([want], path.parent, "hidden")         # the loader's own checks: inside, not the document
                paths = now if any(_bare(p) == _bare(want) for p in now) else [*now, want]
            else:
                if not any(_bare(p) == _bare(want) for p in now):
                    raise HTTPException(409, f"{want} is not hidden by name: a folder or a pattern above it hides it")
                paths = [p for p in now if _bare(p) != _bare(want)]
            if paths != now:
                w.write(name, doc, set_hidden_text(text, paths))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (WorkspaceError, OSError) as exc:
            raise fail(exc) from exc
        if paths != now:
            store.audit(user.name, "hide from agents" if body.hidden else "show to agents", f"{whose.name}/{name}: {want}", loop=(whose.name, name))
        return {"ok": f"{want} {'hidden from' if body.hidden else 'shown to'} the agents", "hidden": paths}
