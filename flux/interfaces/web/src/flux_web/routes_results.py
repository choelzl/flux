"""A loop's outputs: its workbench, the journal, live state and log as server-sent events (D694,
D759, D761), the raw log, its model and agent turns (D779), the timeline and usage (D694), the
results, one design (D690) and the report."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from types import SimpleNamespace
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from .runs import loop_files
from .store import User
from .workspace import Workspace, WorkspaceError


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


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """The results' routes, every read through the guards `ctx` holds (D888)."""
    store, runs = ctx.store, ctx.runs
    user_of, admin_of, reader, fail, loop_of = ctx.user_of, ctx.admin_of, ctx.reader, ctx.fail, ctx.loop_of
    _masks, _stages = ctx.masks, ctx.stages

    @app.get("/api/apps/{name}/workbench")
    def workbench(name: str, owner: str | None = None, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        try:
            return reader(user, owner, name)[0].workbench(name)
        except WorkspaceError as exc:
            raise fail(exc) from exc

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
