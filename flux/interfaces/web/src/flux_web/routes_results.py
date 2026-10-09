"""A loop's outputs: its workbench, the journal, live state and log as server-sent events (D694,
D759, D761; one stream for the three, D917), the raw log, its model and agent turns (D779), the
timeline and usage (D694), the results, one design (D690) and the report."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from .runs import loop_files
from .store import User
from .workspace import Workspace, WorkspaceError


def journal_slice(path: str, off: int, since: float) -> tuple[list[dict[str, Any]] | None, int]:
    """The journal from byte `off`: its events -- compacted (D762), from `since` on -- and the offset
    after; None when nothing new."""
    from flux_loop.journal import compact, read_events

    events, new = read_events(path, off, limit=4 << 20)       # D759: in slices
    got = [e for e in compact(events) if e.get("t", 0) >= since]
    if not got and new == off:
        return None, new
    return got, new


def journal_messages(path: str, off: int, ino: int, since: float, kind: str = "events") -> tuple[list[str], int]:
    """The journal from byte `off` as server-sent messages, and the offset after (D855). A slice is
    one message whose data is its events -- compacted (D762) and from `since` on -- and whose id is
    the slice's end: a client that reconnects with it resumes after what it received whole. One
    message per event, each with the slice's end, skipped the rest of a slice when a client got
    only the first (and compaction merges a phase's updates into its last, so a per-event cursor
    cannot be made safe)."""
    got, new = journal_slice(path, off, since)
    if got is None:
        return [], new
    return [f"id: {ino}-{new}\nevent: {kind}\ndata: {json.dumps(got)}\n\n"], new


#: D917: what a loop's one stream carries
PARTS = ("events", "live", "log")


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

    def selected_run(name: str, user: User, owner: str | None, run_id: int | None):
        w, whose, d, run = loop_of(name, user, owner)
        if run_id is not None:
            run = store.run(run_id)
            if not run or run["user_id"] != whose.id or run["app"] != name:
                raise HTTPException(404, "no such start of this loop")
        return w, whose, d, run

    def selected_campaign(run, campaign):
        if campaign is None:
            return runs.campaign(run)[0]
        from flux_store import CampaignStore

        if not run or not os.path.isfile(run["db"]):
            raise HTTPException(404, "no record for this start")
        record = CampaignStore(run["db"])
        try:
            if campaign not in {c["campaign_id"] for c in record.list_campaigns()}:
                raise HTTPException(404, "no such campaign in this record")
        finally:
            record.close()
        return campaign

    def start_end(name, user, owner, start_id, run):
        if start_id is None:
            return None
        _w, whose, _d, start = selected_run(name, user, owner, start_id)
        if not run or start["db"] != run["db"]:
            raise HTTPException(404, "this start uses another record")
        if start.get("ended"):
            return float(start["ended"])
        following = next((r for r in reversed(store.runs(whose, name)) if r["id"] > start_id), None)
        if following:
            return float(following["started"])
        import time

        return time.time()

    @app.get("/api/apps/{name}/runs")
    def run_history(name: str, owner: str | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """Starts of this loop and the retained campaigns (a campaign may span several starts)."""
        from flux_store import CampaignStore

        _w, whose, _d, _run = loop_of(name, user, owner)
        starts, campaigns, seen = [], [], {}
        for run in store.runs(whose, name):
            running = runs.live(run)
            record_id = seen.setdefault(run["db"], run["id"])
            starts.append({k: run.get(k) for k in ("id", "started", "ended", "rc")} | {"running": running, "record_id": record_id})
            if record_id != run["id"] or not os.path.isfile(run["db"]):
                continue
            record = CampaignStore(run["db"])
            try:
                campaigns.extend({**c, "run_id": run["id"]} for c in record.list_campaigns())
            finally:
                record.close()
        return {"starts": starts, "campaigns": sorted(campaigns, key=lambda c: c["created_at"], reverse=True)}

    @app.get("/api/apps/{name}/run-data")
    def run_data(name: str, campaign: str, kind: str, run_id: int | None = None, start_id: int | None = None, owner: str | None = None,
                 user: User = Depends(user_of)):
        """A retained campaign's complete raw journal or transcript."""
        from .confine import Escape, open_read
        from .run_history import trace_path

        _w, _whose, _d, run = selected_run(name, user, owner, run_id)
        selected_campaign(run, campaign)
        lo, hi = 0.0, float("inf")
        if start_id is not None:
            _w, whose, _d, start = selected_run(name, user, owner, start_id)
            if start["db"] != run["db"]:
                raise HTTPException(404, "this start uses another record")
            lo = float(start["started"])
            following = next((r for r in reversed(store.runs(whose, name)) if r["id"] > start_id), None)
            hi = float(following["started"]) if following else float("inf")
        try:
            path = trace_path(run, campaign, kind, runs)
            fh = open_read(path, *runs.roots(run))
        except FileNotFoundError:
            raise HTTPException(404, "this campaign's data is no longer available") from None
        except (Escape, ValueError, OSError) as exc:
            raise HTTPException(400, "this campaign's data is not available here") from exc

        def chunks():
            with fh:
                if start_id is not None:
                    for line in fh:
                        try:
                            row = json.loads(line)
                            t = float(row.get("t" if kind == "events" else "ts") or 0)
                        except (ValueError, TypeError, AttributeError):
                            continue
                        if lo <= t < hi:
                            yield line
                    return
                while chunk := fh.read(1 << 16):
                    yield chunk
        return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8")

    def _reader(kind: str, path_of, at: tuple[int | None, int], roots_of, since_of=lambda run: 0):
        """A followed file -- the log or the journal -- from its cursor `at` (inode, byte): `look(run)`
        is its next piece, (data or None, moved, cursor after). For the journal, `since_of(run)` is
        when the loop's latest start began: its tree, not the last. D917: `run` is the latest start,
        looked up once for every part of a look."""
        cur = {"ino": at[0], "offset": at[1]}

        def look(run: dict[str, Any] | None) -> tuple[str | None, bool, str]:
            path = _confined(path_of(run), roots_of(run))             # D852: no link, nothing outside the loop
            if not (path and os.path.exists(path)):
                return None, False, f"{cur['ino'] or ''}-{cur['offset']}"
            st = os.stat(path)
            ino, off = cur["ino"], cur["offset"]
            if st.st_ino != ino or st.st_size < off:              # D694: another file (a new start's), or cut
                if ino is not None or st.st_size < off:
                    off = 0
                ino = st.st_ino
            data = None
            if kind == "events":
                got, new = journal_slice(path, off, since_of(run))
                data = None if got is None else json.dumps(got)
            else:
                from .confine import open_read

                with open_read(path, *roots_of(run)) as fh:
                    fh.seek(off)
                    chunk = fh.read(256 * 1024)
                new = off + len(chunk)
                data = json.dumps(chunk.decode("utf-8", "replace")) if chunk else None
            cur["ino"], cur["offset"] = ino, new
            return data, new != off, f"{ino}-{new}"

        return look

    def _live_look(whose: User, name: str):
        """The latest start's live state (D761): `live.json` -- each running phase's latest fields
        and the standings -- whole each time it changes; no cursor (a new connection gets it at once)."""
        seen: list[Any] = [None]

        def look(run: dict[str, Any] | None) -> tuple[str | None, bool, None]:
            ev = runs.events_path(run)
            path = _confined(os.path.join(os.path.dirname(ev), "live.json"), runs.roots(run)) if ev else None   # D852
            try:
                st = os.stat(path) if path else None
            except OSError:
                st = None
            if st is None or (st.st_ino, st.st_mtime_ns, st.st_size) == seen[0]:
                return None, False, None
            seen[0] = (st.st_ino, st.st_mtime_ns, st.st_size)
            try:
                from .confine import open_read

                with open_read(path, *runs.roots(run), text=True) as fh:
                    body = fh.read()
                json.loads(body)
                return _masks().body(body), False, None             # D850: the admin's stderr masks
            except (OSError, ValueError):
                seen[0] = None                                  # half written: read again
                return None, False, None

        return look

    async def _multiplex(request: Request, run_of, readers: dict[str, Any], preface: tuple[str, ...] = (), bare: bool = False):
        """Server-sent events (D694, D917): each part's new pieces as they come -- `readers`, kind ->
        look -- in ONE response. A message's id is every part's cursor as delivered up to it,
        `events:<inode>-<byte>,log:<inode>-<byte>` (`bare`: only `<inode>-<byte>`, a one-file route's
        own), so a reconnect resumes each part where it was. The journal's first catch-up is followed
        by a `ready` event: the page tells saved tasks loading from a run not begun. D774: each look
        -- the run's record, the files, a slice read and parsed -- is a worker thread's: the event
        loop that answers every other request never waits on a disk or the store."""
        sent: dict[str, str] = {}
        ready = "events" not in readers

        def look_all() -> tuple[list[tuple[str, str, str | None]], bool]:
            run = run_of()                                        # D918: once a look, for every part
            out, moved = [], False
            for kind, look in readers.items():
                data, m, cursor = look(run)
                moved = moved or m
                if data is not None:
                    out.append((kind, data, cursor))
            return out, moved

        yield "retry: 3000\n\n"
        for p in preface:                                      # D759: what the window left out, said first
            yield p
        while True:
            if await request.is_disconnected():
                return
            out, moved = await asyncio.to_thread(look_all)
            for kind, data, cursor in out:
                if cursor is None:
                    yield f"event: {kind}\ndata: {data}\n\n"
                    continue
                sent[kind] = cursor
                ident = cursor if bare else ",".join(f"{k}:{v}" for k, v in sent.items())
                yield f"id: {ident}\nevent: {kind}\ndata: {data}\n\n"
            if moved:
                continue
            if not ready:
                ready = True
                yield "event: ready\ndata: {}\n\n"
            if not out:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1.0)

    def _cursors(request: Request, said: dict[str, str], bare: str = "") -> dict[str, tuple[int | None, int]]:
        """Where each part resumes (D694, D917): from the last event's id a reconnecting EventSource
        sends -- `kind:<inode>-<byte>,...`; a one-file route's bare `<inode>-<byte>` is its `bare`
        kind's -- else from what the page asked (`said`, a page that reopened its stream). A bare byte
        offset is taken on whatever file is there."""
        header = request.headers.get("last-event-id")
        if header:
            for piece in header.split(","):
                kind, _, at = piece.rpartition(":")
                if (kind or bare) in said:
                    said[kind or bare] = at
        out = {}
        for kind, at in said.items():
            ino, _, off = (at or "0").rpartition("-")
            try:
                out[kind] = (int(ino) if ino else None), int(off)
            except ValueError:
                out[kind] = None, 0
        return out

    def _events_part(whose: User, name: str, at: tuple[int | None, int], window: int) -> tuple[Any, tuple[str, ...]]:
        """The latest start's journal; `window` (D759): only its last that many passes, a first
        `window` event saying how many came before -- a day-long run's tree opens at once."""
        from flux_loop.journal import start_offset, window_start

        run = runs.latest(whose, name)
        preface: tuple[str, ...] = ()
        path = _confined(runs.events_path(run), runs.roots(run))   # D852
        if at[1] == 0 and path and os.path.exists(path):
            got = window_start(path, window) if window > 0 else None
            if got is None:                                    # D918: the whole start -- from its hello, not byte 0
                at = (os.stat(path).st_ino, start_offset(path))
            else:
                at = (os.stat(path).st_ino, got[0])
                said = {"ev": "window", "before": got[1], "t": 0, "cut": got[2] is not None}
                preface = (f"event: events\ndata: {json.dumps(said)}\n\n",)
                if got[2] is not None and got[2] >= 0:         # D762: inside a pass -- its mark first
                    from .confine import open_read

                    with open_read(path, *runs.roots(run)) as fh:
                        fh.seek(got[2])
                        preface += (f"event: events\ndata: {fh.readline().decode('utf-8', 'replace').strip()}\n\n",)
        return _reader("events", runs.events_path, at, runs.roots,
                       since_of=lambda r: (r or {"started": 0})["started"] - 1), preface

    def _log_part(d: Path, at: tuple[int | None, int], tail: int) -> tuple[Any, tuple[str, ...]]:
        """The loop's one log, every start in it; `tail` (D759): only its last that many bytes, a
        first `skipped` event saying how many came before."""
        path = str(loop_files(d)["log"])
        preface: tuple[str, ...] = ()
        ok = _confined(path, (str(d),))                           # D852
        if tail > 0 and at[1] == 0 and ok and os.path.exists(path) and os.path.getsize(path) > tail:
            size = os.path.getsize(path)
            from .confine import open_read

            with open_read(path, str(d)) as fh:                  # from the first whole line of the tail
                fh.seek(size - tail)
                skip = size - tail + fh.read(64 * 1024).find(b"\n") + 1
            at = (os.stat(path).st_ino, skip)
            preface = (f"event: skipped\ndata: {json.dumps({'bytes': skip})}\n\n",)
        return _reader("log", lambda _r: path, at, lambda _r: (str(d),)), preface

    def _sse(gen) -> StreamingResponse:
        return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/apps/{name}/stream")
    def loop_stream(name: str, request: Request, parts: str = ",".join(PARTS), events: str = "0", log: str = "0",
                    window: int = 0, tail: int = 0, owner: str | None = None, user: User = Depends(user_of)):
        """D917: the loop's journal, live state and log -- `parts`, any of them -- as ONE server-sent
        stream. A browser keeps six connections to a server on HTTP/1; a Live page's three streams
        left a second Live tab's none for an ordinary request. `events`, `log`: each part's cursor
        (`<inode>-<byte>`, a reopened page's), `window` and `tail` as the journal's and the log's own."""
        _w, whose, d, _run = loop_of(name, user, owner)
        want = [p for p in PARTS if p in parts.split(",")]
        at = _cursors(request, {"events": events, "log": log})
        readers: dict[str, Any] = {}
        preface: tuple[str, ...] = ()
        if "events" in want:
            readers["events"], said = _events_part(whose, name, at["events"], window)
            preface += said
        if "live" in want:
            readers["live"] = _live_look(whose, name)
        if "log" in want:
            readers["log"], said = _log_part(d, at["log"], tail)
            preface += said
        return _sse(_multiplex(request, lambda: runs.latest(whose, name), readers, preface))

    @app.get("/api/apps/{name}/log/raw")
    def log_raw(name: str, owner: str | None = None, run_id: int | None = None, download: bool = True,
                preview: bool = False, user: User = Depends(user_of)):
        _w, whose, d, run = selected_run(name, user, owner, run_id)
        from .confine import Escape, open_read

        path = loop_files(d)["log"]
        try:
            fh = open_read(path, str(d))                           # D852: never a link out of the loop
        except FileNotFoundError:
            raise HTTPException(404, "no log yet") from None
        except (Escape, OSError) as exc:
            raise HTTPException(400, "the log is not a file of this loop") from exc

        lo, hi = 0, os.fstat(fh.fileno()).st_size
        if run_id is not None:
            from .run_history import log_range

            following = next((r for r in reversed(store.runs(whose, name)) if r["id"] > run_id), None)
            try:
                lo, hi = log_range(fh, run, following)
            except FileNotFoundError:
                fh.close()
                raise HTTPException(404, "this start's log is no longer available") from None
        size = hi - lo
        cut = preview and size > (1 << 20)
        if preview:
            hi = min(hi, lo + (1 << 20))

        def chunks():
            with fh:
                fh.seek(lo)
                left = hi - lo
                while left > 0:
                    b = fh.read(min(left, 1 << 16))
                    if not b:
                        return
                    left -= len(b)
                    yield b

        return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8",
                                 headers={"Content-Disposition": f'{"attachment" if download else "inline"}; filename="{name}.log"',
                                          "X-Flux-Size": str(size), **({"X-Flux-Truncated": "1"} if cut else {})})

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
    def turns(name: str, k: int | None = None, owner: str | None = None, run_id: int | None = None,
              campaign: str | None = None, start_id: int | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        """The loop's model and agent turns, newest last: the last 500 summed up, or turn `k` whole."""
        _w, _whose, _d, run = selected_run(name, user, owner, run_id)
        if campaign is not None:
            from .run_history import trace_path

            selected_campaign(run, campaign)
            try:
                path = _confined(trace_path(run, campaign, "turns", runs), runs.roots(run))
            except (ValueError, OSError):
                path = None
        else:
            path = runs.turns_path(run)
        if not (path and os.path.exists(path)):
            if campaign is not None:
                raise HTTPException(404, "this campaign's transcript is no longer available")
            return {"turns": []}
        offsets, rows = _turn_summaries(path)
        hide = _masks()                                       # D850: the admin's stderr masks
        lo, hi = 0.0, float("inf")
        if start_id is not None:
            _w, whose, _d, start = selected_run(name, user, owner, start_id)
            if start["db"] != run["db"]:
                raise HTTPException(404, "this start uses another record")
            lo = float(start["started"])
            following = next((r for r in reversed(store.runs(whose, name)) if r["id"] > start_id), None)
            hi = float(following["started"]) if following else float("inf")
        if k is None:
            listed = [{"k": n, **t} for n, t in enumerate(rows, 1) if t is not None and lo <= float(t.get("ts") or 0) < hi]
            return {"turns": hide.fields(listed if start_id is not None else listed[-500:]), "total": len(listed)}
        if not 1 <= k <= len(offsets):
            return {"turns": []}
        if rows[k - 1] is None or not lo <= float(rows[k - 1].get("ts") or 0) < hi:
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
                                 "tokens_cached": 0.0, "cost_usd": 0.0, "counted": 0, "partial": 0}
        for a in Workspace(store.data, u.name).apps():
            got = usage(runs.turns_path(runs.latest(u, a["name"])))["total"]
            total["loops"] += 1
            for k in ("turns", "seconds", "tokens_in", "tokens_out", "tokens_cached", "cost_usd", "counted", "partial"):
                total[k] += got[k]
        return total

    @app.get("/api/usage")
    def my_usage(user: User = Depends(user_of)) -> dict[str, Any]:
        """The user's turns over all their loops (D694)."""
        return _user_usage(user)

    @app.get("/api/admin/usage")
    def all_usage(_a: User = Depends(admin_of)) -> list[dict[str, Any]]:
        return [_user_usage(u) for u in store.users()]

    @app.get("/api/apps/{name}/ideas")
    def ideas(name: str, owner: str | None = None, run_id: int | None = None, campaign: str | None = None,
              start_id: int | None = None, user: User = Depends(user_of)) -> dict[str, Any]:
        from datetime import datetime, timezone

        from flux_loop.ideas import notebook
        from flux_store import CampaignStore

        _w, _whose, _d, run = selected_run(name, user, owner, run_id)
        cid = selected_campaign(run, campaign)
        until = start_end(name, user, owner, start_id, run)
        if not cid or not os.path.isfile(run["db"]):
            return {"campaign": None, "ideas": []}
        record = CampaignStore(run["db"])
        try:
            return notebook(record, cid, until=datetime.fromtimestamp(until, timezone.utc).isoformat() if until is not None else None)
        finally:
            record.close()

    @app.get("/api/apps/{name}/results")
    def results(name: str, owner: str | None = None, run_id: int | None = None, campaign: str | None = None, start_id: int | None = None,
                raw: bool = False,
                user: User = Depends(user_of)) -> dict[str, Any]:
        from flux_loop.report import load

        _w, _whose, d, run = selected_run(name, user, owner, run_id)
        cid = selected_campaign(run, campaign)
        until = start_end(name, user, owner, start_id, run)
        if not cid or not os.path.exists(run["db"]):
            return {"campaign": None}
        rep = load(run["db"], cid, until=until)
        from .results import designs, thin

        rows = thin(rep.rows, [(o.metric, o.direction) for o in rep.objectives], cap=len(rep.rows) if raw else 3000)
        answer = None
        ans = loop_files(d)["answer"] if campaign is None and run_id is None and start_id is None else None
        if ans is not None and ans.exists():
            try:
                from .confine import open_read

                with open_read(ans, str(d), text=True) as fh:        # D852: runs/ is the run's to write
                    answer = json.loads(fh.read())
            except (ValueError, OSError):
                pass
        from .results import decision_doc

        decision = decision_doc(run["db"], ans, cid, until)     # D809: the record's pass; D840: which design
        from .results import decision_said

        decided_by = decision_said(run["db"], cid, until)       # D815: why, as the loop said it
        stages = _stages(_w, name) if campaign is None else [{"name": s} for s in dict.fromkeys(row.stage for row in rep.rows)]
        listed = designs(run["db"], stages, decision, limit=None if raw else 20000, campaign=campaign, until=until)
        objective_list = [{"metric": o.metric, "direction": o.direction, "goal": o.goal, "stage": o.stage, "unit": o.unit}
                          for o in rep.objectives]                     # for the Overview's charts (D692)
        return {"campaign": cid, "objectives": rep.objectives.describe(), "objective_list": objective_list, "rows": rows,
                "rows_total": len(rep.rows),
                "passes": [{"when": w, "conclusion": c} for w, c in rep.passes], "notes": rep.notes,
                "agent_turns": len(rep.agent_turns), "answer": answer, "decided_by": decided_by, **listed}

    @app.get("/api/apps/{name}/design")
    def design(name: str, design: str, part: str = "", key: str = "", owner: str | None = None,
               run_id: int | None = None, campaign: str | None = None, start_id: int | None = None,
               user: User = Depends(user_of)) -> dict[str, Any]:
        """One design of the loop: its source, why it failed, every stage's numbers (D690); `key`:
        which, of the designs a name was given to (D840)."""
        from flux_store import CampaignStore

        from .results import _when, content_key

        _w, _whose, _d, run = selected_run(name, user, owner, run_id)
        if campaign is not None:
            selected_campaign(run, campaign)
        until = start_end(name, user, owner, start_id, run)
        if not run or not os.path.exists(run["db"]):
            raise HTTPException(404, "no record yet")
        store = CampaignStore(run["db"])
        found: dict[str, Any] = {"name": design, "part": part, "artifact": None, "trials": []}
        try:
            for camp in store.list_campaigns():
                if campaign is not None and camp["campaign_id"] != campaign:
                    continue
                for t in store.trials(camp["campaign_id"], status="ok"):
                    if until is not None and _when(t.created_at) > until:
                        continue
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
    def report(name: str, owner: str | None = None, run_id: int | None = None, campaign: str | None = None, start_id: int | None = None,
               user: User = Depends(user_of)) -> HTMLResponse:
        from flux_loop.report import load, render

        _w, _whose, _d, run = selected_run(name, user, owner, run_id)
        cid = selected_campaign(run, campaign)
        until = start_end(name, user, owner, start_id, run)
        if not cid:
            raise HTTPException(404, "no record yet")
        return HTMLResponse(render(load(run["db"], cid, until=until)),
                            headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
                                     "X-Frame-Options": "SAMEORIGIN"})
