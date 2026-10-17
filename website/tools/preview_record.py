"""The docs' Preview app's fixtures (website/docs/preview/data/): a Flux server's answers, recorded, scrubbed and trimmed.

The preview (website/docs/preview/app.html) runs the web app in the browser over a mock (docs/assets/preview/mock.js)
that answers the app's GETs from these files. They are recorded from a real server run over a COPY of sample data
(never a real deployment: everything the walk sees is published):

    flux serve --data COPY --no-sandbox --port 18977 &          # with the users' passwords below
    python website/tools/preview_record.py run popsweep --passes 6 --raw RAW   # a live run: its stream and snapshots
    python website/tools/preview_record.py walk --raw RAW         # every GET the app makes, per user and loop
    python website/tools/preview_record.py build --raw RAW [--staged DIR]      # scrubbed fixtures into docs/preview/data

`run` resets the loop, starts it and records what its Live page is sent (the one stream: journal, live state, log,
each message with its time) and what the page polls meanwhile (its state, the loops list, Results); the preview's
Start replays it. `walk` asks, as each demo user, every GET route the app calls -- its pages, each loop's tabs, files,
designs, turns and past starts (what the preview cannot find it answers 404 and lists in `window.__preview.misses`). `build` writes what the preview reads: one file of a user's own answers,
one per loop, one per recorded run, an index; personal paths, hosts, keys and emails replaced by neutral ones,
logs and journals cut. `--staged DIR`: the UI review's staged loops (staging.py: results, ideas, turns, asks and a
journal for loops that cannot run without models or agents), laid over the walked answers."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "docs/preview/data"
URL = os.environ.get("PREVIEW_URL", "http://127.0.0.1:18977")
#: the sample data's demo accounts (its users.py): bob the internal user the preview opens as, ada its admin
PASSWORDS = {"bob": "bob has a secret", "ada": "ada the admin secret"}
USERS = ("bob", "ada")


# ---------------------------------------------------------------------------------------------------- the server
class Client:
    """One user's session: cookies kept, each answer as (status, content type, bytes)."""

    def __init__(self, user: str, url: str = URL):
        self.url, self.user = url, user
        self.web = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        status, _t, body = self.call("/login", "POST", {"name": user, "password": PASSWORDS[user]})
        if status != 200:
            raise SystemExit(f"login as {user}: {status} {body[:200]!r}")

    def call(self, path: str, method: str = "GET", body: Any = None, timeout: float = 60) -> tuple[int, str, bytes]:
        headers, data = {"X-Flux": "1"}, None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.url + "/api" + path, data=data, headers=headers, method=method)
        try:
            with self.web.open(req, timeout=timeout) as got:
                return got.status, got.headers.get("content-type", ""), got.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("content-type", ""), e.read()

    def json(self, path: str) -> Any:
        status, kind, body = self.call(path)
        if status != 200 or "json" not in kind:
            raise RuntimeError(f"GET {path}: {status} {body[:200]!r}")
        return json.loads(body)

    def stream(self, path: str):
        """A server-sent stream's messages as they come: (event, data, id)."""
        req = urllib.request.Request(self.url + "/api" + path, headers={"X-Flux": "1", "Accept": "text/event-stream"})
        with self.web.open(req, timeout=600) as got:
            ev, data, ident = "message", [], None
            for raw in got:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    if data:
                        yield ev, "\n".join(data), ident
                    ev, data, ident = "message", [], None
                elif line.startswith(":"):
                    continue
                else:
                    field, _, value = line.partition(":")
                    value = value[1:] if value.startswith(" ") else value
                    if field == "event":
                        ev = value
                    elif field == "data":
                        data.append(value)
                    elif field == "id":
                        ident = value


# ---------------------------------------------------------------------------------------------------- run: a live run
#: what the page polls while a loop runs, snapshotted through the run (the loop's `{n}`)
POLLED = ("/apps", "/loops", "/apps/{n}", "/apps/{n}/state", "/apps/{n}/results", "/apps/{n}/runs", "/apps/{n}/usage")


def record_run(args) -> None:
    c, n = Client(args.user), args.loop
    raw = Path(args.raw)
    raw.mkdir(parents=True, exist_ok=True)
    if c.json(f"/apps/{n}/state").get("running"):
        raise SystemExit(f"{n} is running: stop it first")
    status, _t, body = c.call(f"/apps/{n}/reset", "POST", {"keep": ["cache"]})
    print("reset", status, body[:200], flush=True)
    messages: list[list[Any]] = []
    snaps: dict[str, list[list[Any]]] = {p: [] for p in POLLED}
    done = threading.Event()
    t0 = time.time()

    def listen():
        try:
            for ev, data, ident in c.stream(f"/apps/{n}/stream?parts=events,live,log&window=30&tail=65536"):
                messages.append([round(time.time() - t0, 3), ev, data, ident])
                if done.is_set():
                    return
        except Exception as x:  # noqa: BLE001 -- the stream ends with the recording
            print("stream:", x, flush=True)

    status, _t, body = c.call(f"/apps/{n}/start", "POST", {"passes": args.passes})
    print("start", status, body[:200], flush=True)
    if status != 200:
        raise SystemExit(1)
    threading.Thread(target=listen, daemon=True).start()
    last: dict[str, bytes] = {}
    ended = None
    while True:
        for p in POLLED:
            path = p.format(n=n)
            status, kind, body = c.call(path)
            if status == 200 and last.get(p) != body:
                last[p] = body
                snaps[p].append([round(time.time() - t0, 3), json.loads(body)])
        st = json.loads(last[POLLED[3]])
        if not st.get("running") and ended is None and time.time() - t0 > 5:
            ended = time.time()
        if ended and time.time() - ended > 4:
            break
        if time.time() - t0 > args.timeout:
            print("timed out", flush=True)
            break
        time.sleep(args.every)
    done.set()
    time.sleep(1.5)
    out = {"loop": n, "user": args.user, "passes": args.passes, "recorded": t0, "duration": round(time.time() - t0, 3),
           "stream": messages, "polled": {p.format(n="{n}"): v for p, v in snaps.items()}}
    (raw / f"run-{n}.json").write_text(json.dumps(out))
    print(f"{n}: {len(messages)} messages, {sum(map(len, snaps.values()))} snapshots, {out['duration']:.0f}s", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="reset a loop, start it and record its live run")
    r.add_argument("loop")
    r.add_argument("--passes", type=int, default=6)
    r.add_argument("--user", default="bob")
    r.add_argument("--raw", required=True)
    r.add_argument("--every", type=float, default=1.0)
    r.add_argument("--timeout", type=float, default=1200)
    w = sub.add_parser("walk", help="record every GET the app makes, per demo user and loop")
    w.add_argument("--raw", required=True)
    b = sub.add_parser("build", help="the scrubbed fixtures into docs/preview/data")
    b.add_argument("--raw", required=True)
    b.add_argument("--staged", default=None)
    b.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    {"run": record_run, "walk": walk, "build": build}[args.cmd](args)
    return 0


#: a user's own answers: the bar, the loops list, Account, New loop
USER_GETS = ("/me", "/apps", "/loops", "/shared", "/group-loops", "/notices", "/impersonation", "/preferences/overview", "/settings",
             "/env", "/agents", "/logins", "/usage", "/new-by-agent/uses")
#: the admin's pages (Admin's tabs)
ADMIN_GETS = ("/admin/apps", "/admin/history?hours=24", "/admin/token-rate?hours=24", "/admin/resources", "/admin/maintenance",
              "/admin/controls", "/admin/sandbox", "/audit", "/admin/applications", "/admin/documents", "/admin/insights",
              "/admin/insights/disk", "/admin/masks", "/admin/agents?probe=false", "/admin/agents/versions", "/admin/settings",
              "/admin/env", "/admin/ask", "/admin/usage", "/users", "/groups")
#: a loop's answers: its page, each tab and what the tabs open (`` is the page itself, /apps/<name>)
LOOP_GETS = ("", "/state", "/results", "/runs", "/usage", "/ideas", "/digests", "/turns", "/notes", "/workbench", "/files",
             "/files?ignored=true", "/document", "/preferences", "/env", "/shares", "/ownership", "/hidden", "/asks", "/maintenance",
             "/reset", "/preflight", "/inputs", "/author", "/author/decisions", "/log/raw?download=false")
#: the most of each a loop's file keeps (a file larger than TEXT_MAX is listed, not read)
MAX_FILES, MAX_DESIGNS, MAX_TURNS, TEXT_MAX = 60, 30, 25, 48_000
TEXTS = re.compile(r"\.(py|sv|v|svh|vh|yaml|yml|md|txt|json|jsonl|c|cc|cpp|h|hpp|ini|toml|cfg|sh|log|csv|tcl|sdc|gitignore)$|^[^.]+$")


class Walker:
    """One user's walk: each answer kept by its path (`/apps/sw/file?path=x`), with its status and kind."""

    def __init__(self, user: str):
        self.c, self.got = Client(user), {}

    def get(self, path: str) -> Any:
        status, kind, body = self.c.call(path)
        if "json" in kind:
            try:
                value: Any = json.loads(body)
            except ValueError:
                value = body.decode("utf-8", "replace")
        else:
            value = body.decode("utf-8", "replace")
        self.got[path] = {"s": status, "t": "json" if "json" in kind else "text", "b": value}
        return value if status == 200 else None

    def stream(self, path: str, wait: float = 2.5) -> None:
        """A stream's first messages -- the journal caught up, `ready`, the live state -- as a page opening it gets them."""
        msgs: list[list[Any]] = []

        def listen():
            try:
                for ev, data, ident in self.c.stream(path):
                    msgs.append([0, ev, data, ident])
            except Exception:  # noqa: BLE001 -- cut when the wait is over
                return
        th = threading.Thread(target=listen, daemon=True)
        th.start()
        th.join(wait)
        self.got[path] = {"s": 200, "t": "stream", "b": list(msgs)}

    def loop(self, name: str, owner: str | None) -> None:
        q = (lambda p: p + ("&" if "?" in p else "?") + "owner=" + enc(owner)) if owner else (lambda p: p)
        at = f"/apps/{enc(name)}"
        get = lambda p: self.get(q(at + p))                                      # noqa: E731
        for p in LOOP_GETS:
            get(p)
        # the files, folder by folder (the Files tab), and the text of the small ones
        todo, read = [""], 0
        while todo:
            folder = todo.pop(0)
            for row in get(f"/files?path={enc(folder)}" if folder else "/files") or []:
                path = row["path"]
                if row.get("dir"):
                    if path.count("/") < 2 and "__pycache__" not in path:
                        todo.append(path)
                    continue
                get(f"/item?path={enc(path)}")
                if read < MAX_FILES and row.get("size", 0) <= TEXT_MAX and TEXTS.search(path.rsplit("/", 1)[-1]):
                    read += 1
                    get(f"/file?path={enc(path)}")
        # Results: each design's detail (the drawer, Compare)
        for d in ((get("/results") or {}).get("designs") or [])[:MAX_DESIGNS]:
            get(f"/design?design={enc(d.get('base') or d['name'])}&part={enc(d.get('part') or '')}&key={enc(d.get('key') or '')}")
        # Agents: each turn
        for t in ((get("/turns") or {}).get("turns") or [])[-MAX_TURNS:]:
            get(f"/turns?k={t['k']}")
        # Live: each past start's journal, log and results (a start picked in its menu)
        hist = get("/runs") or {}
        for s in hist.get("starts") or []:
            get(f"/log/raw?run_id={s['id']}&download=false&preview=true")
            camp = next((c for c in hist.get("campaigns") or [] if c.get("run_id") == s.get("record_id")), None)
            if camp:
                get(f"/run-data?run_id={s['record_id']}&campaign={enc(camp['campaign_id'])}&start_id={s['id']}&kind=events")
        for a in get("/asks") or []:                    # Talk: each ask's agent
            get(f"/asks/{enc(a['id'])}/agent")
        # the live stream as a Live page opening it is sent it: the journal caught up and the live state
        self.stream(q(f"{at}/stream?parts=events,live"))


def enc(s: Any) -> str:
    return urllib.parse.quote(str(s), safe="")


def walk(args) -> None:
    raw = Path(args.raw)
    raw.mkdir(parents=True, exist_ok=True)
    for user in USERS:
        w = Walker(user)
        for p in USER_GETS:
            w.get(p)
        if (w.got["/me"]["b"] or {}).get("role") == "admin":
            for p in ADMIN_GETS:
                w.get(p)
        loops = [(None, a["name"]) for a in w.got["/apps"]["b"] or []]
        loops += [(a["owner"], a["name"]) for a in (w.got["/shared"]["b"] or []) + (w.got["/group-loops"]["b"] or [])]
        for owner, name in loops:
            print(user, owner or "", name, flush=True)
            w.loop(name, owner)
        (raw / f"walk-{user}.json").write_text(json.dumps(w.got))
        print(user, len(w.got), "answers", flush=True)


# ---------------------------------------------------------------------------------------------------- build
#: what is never published: the recording machine's paths, its people, hosts and keys -- as neutral demo ones
SCRUB = [
    (re.compile(r"/tmp/claude-\d+/pv/data"), "/srv/flux"),
    (re.compile(r"/tmp/claude-\d+(/[A-Za-z0-9_.-]+)?"), "/tmp/flux"),
    (re.compile(r"/home/[A-Za-z0-9_.-]+/AEDAF"), "/opt/flux"),
    (re.compile(r"/home/[A-Za-z0-9_.-]+"), "/home/demo"),
    (re.compile(r"/Users/[A-Za-z0-9_.-]+"), "/home/demo"),
    (re.compile(r"choelzl|[Cc]edric|[Hh][oö]e?lzl|AEDAF|wireless-ws\d*"), "demo"),
    (re.compile(r"localai\.[A-Za-z0-9.-]+|[A-Za-z0-9.-]*cyprien\.[a-z]+"), "models.example.com"),
    (re.compile(r"nixchip0\.cachix\.org"), "cache.example.org"),
    (re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.)[A-Za-z0-9-]+\.[A-Za-z0-9.-]*[a-z]{2,}"), "demo@example.com"),
    (re.compile(r"\b(sk|pk|rk|ghp|gho|ghs|xoxb|xoxp)[-_](?:ant-|proj-|or-v1-)?[A-Za-z0-9_-]{20,}"), "sk-redacted"),
    (re.compile(r"\b(?:eyJ[A-Za-z0-9_-]{10,}\.){2}[A-Za-z0-9_-]{10,}"), "redacted-token"),
]
#: after the scrub, none of these may be left (the build refuses)
NEVER = re.compile(r"/home/(?!demo\b)|claude-\d|choelzl|cyprien|localai\.|AEDAF|nixchip0", re.I)
#: the most a string keeps; a file's text and a document keep more
CUT, CUT_FILE, LOG_LINES, KEEP_TURNS = 4000, TEXT_MAX, 300, 12


def scrub(v: Any) -> Any:
    if isinstance(v, str):
        for pat, rep in SCRUB:
            v = pat.sub(rep, v)
        return v
    if isinstance(v, list):
        return [scrub(x) for x in v]
    if isinstance(v, dict):
        return {scrub(k): scrub(x) for k, x in v.items()}
    return v


def cut(v: Any, n: int = CUT) -> Any:
    """Every string of `v` at most `n` characters long, the cut said."""
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + f"\n… ({len(v) - n:,} more characters cut for the preview)"
    if isinstance(v, list):
        return [cut(x, n) for x in v]
    if isinstance(v, dict):
        return {k: cut(x, n) for k, x in v.items()}
    return v


def migrated(events: list[dict]) -> list[dict]:
    """A journal in today's format (D1236: Live reads only that): an older one converted as the server converts it."""
    try:
        from flux_loop.journal_migrate import migrate_events
    except ImportError:                                        # outside the dev shell: as recorded
        return events
    return migrate_events(events)[0]


def split_path(path: str) -> tuple[str | None, str | None, str]:
    """A recorded path as (owner, loop, the rest with its query sorted, owner left out); no loop for a user's own."""
    base, _, query = path.partition("?")
    params = [(k, v) for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True)]
    owner = next((v for k, v in params if k == "owner"), None)
    params = sorted((k, v) for k, v in params if k != "owner")
    m = re.match(r"^/apps/([^/]+)(/.*)?$", base)
    if not m or m.group(1) in ("from-text", "new-empty", "new-by-agent"):
        return None, None, base + ("?" + urllib.parse.urlencode(params) if params else "")
    rest = (m.group(2) or "") + ("?" + urllib.parse.urlencode(params) if params else "")
    return owner, urllib.parse.unquote(m.group(1)), rest


def answer(rec: dict, rest: str = "") -> Any:
    """One recorded answer as the preview keeps it: {j: json} or {x: text}, `s` its status when not 200;
    a stream as {stream: {events, live}}."""
    if rec["t"] == "stream":
        events, live = [], None
        for _dt, ev, data, _id in rec["b"]:
            if ev == "events":
                d = json.loads(data)
                events += d if isinstance(d, list) else [d]
            elif ev == "live":
                live = json.loads(data)
        events = [e for e in events if e.get("ev") != "window"]
        return {"stream": {"events": cut(migrated(events), 3000), "live": cut(live, 3000)}}
    body = rec["b"]
    base = rest.split("?")[0]
    if rec["s"] != 200 or not isinstance(body, str) and base in ("/log/raw", "/run-data"):
        body = cut(body)
    elif base == "/file":
        body = cut(body, CUT_FILE)
    elif base == "/log/raw":
        lines = (body or "").splitlines(keepends=True)
        body = ("… (earlier lines cut for the preview)\n" if len(lines) > LOG_LINES else "") + "".join(lines[-LOG_LINES:])
    elif base == "/run-data" and isinstance(body, str):
        events = []
        for line in body.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        body = "".join(json.dumps(e) + "\n" for e in cut(migrated(events), 3000))
    elif base == "/document":
        body = cut(body, CUT_FILE)
    else:
        body = cut(body)
    out = {"j": body} if rec["t"] == "json" else {"x": body}
    if rec["s"] != 200:
        out["s"] = rec["s"]
    return out


def thin(snaps: list[list[Any]], every: float = 3.0) -> list[list[Any]]:
    """A run's snapshots of one answer, at most one per `every` seconds (a change of its keys always kept), the last kept."""
    out: list[list[Any]] = []
    for dt, body in snaps:
        if out and dt - out[-1][0] < every and (not isinstance(body, dict) or set(body) == set(out[-1][1] or {})):
            continue
        out.append([dt, body])
    if snaps and out[-1] is not snaps[-1]:
        out.append(snaps[-1])
    return out


def build_run(path: Path, owner: str, name: str) -> dict:
    """A recorded live run as the preview replays it: its stream's messages and the page's polled answers, by time."""
    rec = json.loads(path.read_text())
    stream = []
    for dt, ev, data, _id in rec["stream"]:
        d = json.loads(data) if data else {}
        if ev == "events":
            d = cut(migrated(d if isinstance(d, list) else [d]), 3000)
        elif ev == "live":
            d = cut(d, 3000)
        stream.append([dt, ev, d])
    polled = {}
    for p, snaps in rec["polled"].items():
        rest = p.replace("/apps/{n}", "", 1) if p.startswith("/apps/{n}") else None
        if p in ("/apps", "/loops"):                      # the loop's own row: the others are the list's as walked
            snaps = [[dt, next((r for r in body if (r.get("name") or r.get("app")) == name and not r.get("owner")), None)] for dt, body in snaps]
            polled["row" + p] = thin([s for s in snaps if s[1]])
        elif rest is not None:
            polled[rest] = thin([[dt, cut(b)] for dt, b in snaps], 6.0 if rest == "/results" else 3.0)
    return {"loop": name, "owner": owner, "passes": rec["passes"], "recorded": rec["recorded"], "duration": rec["duration"],
            "stream": stream, "polled": polled}


def build(args) -> None:
    raw, out = Path(args.raw), Path(args.out)
    walked = {u: json.loads((raw / f"walk-{u}.json").read_text()) for u in USERS if (raw / f"walk-{u}.json").exists()}
    recorded = (raw / "walk-bob.json").stat().st_mtime
    users: dict[str, dict] = {}
    loops: dict[str, dict] = {}
    for user, got in walked.items():
        mine = users.setdefault(user, {})
        for path, rec in got.items():
            owner, name, rest = split_path(path)
            if name is None:
                mine[rest] = answer(rec, rest)
                continue
            key = f"{owner or user}/{name}"
            if key in loops and rest in loops[key]:
                continue                                  # the owner's walk first; another's view of it adds only what is missing
            loops.setdefault(key, {})[rest] = answer(rec, rest)
        # the turns kept: the latest few, with their own answers
        for key, ans in loops.items():
            turns = ((ans.get("/turns") or {}).get("j") or {}).get("turns")
            if turns and len(turns) > KEEP_TURNS:
                ans["/turns"]["j"]["turns"] = turns[-KEEP_TURNS:]
                keep = {f"/turns?k={t['k']}" for t in turns[-KEEP_TURNS:]}
                for k in [k for k in ans if k.startswith("/turns?k=") and k not in keep]:
                    del ans[k]
    # a past start's journal is its own; the latest start's is the stream's (the preview reads it there)
    for key, ans in loops.items():
        starts = (((ans.get("/runs") or {}).get("j")) or {}).get("starts") or []
        latest = max((s["id"] for s in starts), default=None)
        for k in [k for k in ans if k.startswith("/run-data?") and f"start_id={latest}&" in k + "&"]:
            del ans[k]
    runs = {}
    for f in sorted(raw.glob("run-*.json")):
        rec = json.loads(f.read_text())
        runs[f"{rec['user']}/{rec['loop']}"] = build_run(f, rec["user"], rec["loop"])
    if args.staged:
        stage(Path(args.staged), users, loops, recorded)
    index = {"format": 1, "recorded": recorded, "users": {u: (a.get("/me") or {}).get("j") for u, a in users.items()},
             "loops": sorted(loops), "runs": sorted(runs), "autostart": [k for k in runs if k.endswith("/popsweep")],
             "default": "bob"}
    files = {"index.json": index, **{f"users/{u}.json": a for u, a in users.items()},
             **{f"loops/{k}.json": a for k, a in loops.items()}, **{f"runs/{k}.json": r for k, r in runs.items()}}
    if out.exists():
        for old in out.rglob("*.json"):
            old.unlink()
    total = 0
    for rel, value in files.items():
        text = json.dumps(scrub(value), separators=(",", ":"), ensure_ascii=False)
        bad = NEVER.search(text)
        if bad:
            raise SystemExit(f"{rel}: {text[max(0, bad.start() - 80):bad.end() + 80]!r} is left after the scrub")
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(text)
        total += len(text.encode())
    print(f"{len(files)} files, {total / 1e6:.2f} MB in {out}")


# ---------------------------------------------------------------------------------------------------- staged loops
def summary_of(r: dict) -> dict:
    """The loops list's line of a staged loop (its designs, the best), from its Results as the server sums them up."""
    ds = [d for d in r.get("designs") or [] if not d.get("baseline")]
    dec = next((d for d in ds if d.get("decision")), None)
    main = (r.get("main_metrics") or r.get("metrics") or [None])[:1]
    best = None
    if dec and main[0] in (dec.get("numbers") or {}):
        best = {"design": dec["name"], "metric": main[0], "value": dec["numbers"][main[0]], "meets": None, "stage": dec.get("shown"),
                "measurements": {m: {"value": v, "meets": None} for m, v in dec["numbers"].items()}}
    acc = sum(d.get("verdict") == "accepted" for d in ds)
    return {"designs": len(ds), "accepted": acc, "this_run": len(ds), "accepted_this_run": acc, "feasible": bool(dec),
            "metrics": r.get("metrics") or [], "main_metrics": main, **({"best": best} if best else {})}


def stage(where: Path, users: dict, loops: dict, recorded: float) -> None:
    """The UI review's staged loops (its staging.py, at `where`): loops that cannot run without models or agents,
    shown running, asking or finished -- results, ideas, agents' turns, asks and a journal laid over the walked
    answers, as the review staged them in the page."""
    sys.path.insert(0, str(where))
    import staging as S                                   # noqa: PLC0415 -- the review's, not the repo's

    S.NOW = recorded
    scratch = where.parent
    looptree = Path(__file__).resolve().parents[2] / "flux/tests/unit/fixtures/looptree"
    for name, mode, journal, results, question, agents, task in (
            ("adder8", "running", scratch / "running.jsonl", S.rtl_results("adder8"), False, ("claude",), 19),
            ("primes_agents", "running", scratch / "ian-last.jsonl", S.py_results("primes_agents"), True, ("claude", "opencode", "codex"), 11),
            ("codec_parts", "finished", looptree / "parallel.jsonl", S.parts_results(("decoder", "encoder"), "codec_parts"), False, (), None),
            ("nlu", "running", looptree / "sweep.jsonl", S.parts_results(("recip", "rsqrt", "exp", "log"), "nlu"), False, ("claude",), None)):
        ans = loops[f"bob/{name}"]
        end_at = recorded - 5 if mode == "running" else recorded - 1800
        ev = migrated(S.journal_text(journal, end_at)[0])
        t0 = min([e["t"] for e in ev if isinstance(e.get("t"), (int, float))] or [end_at - 600])
        hist = S.history(t0, end_at, running=mode == "running")
        st = {"campaign": name, "events": True, "last_active": end_at, "failed": False, "stopped": False}
        if mode == "running":
            marks = [json.loads(e.get("why") or "{}").get("n") for e in ev if e.get("ev") == "mark" and e.get("name") == "pass"]
            marks = [m for m in marks if isinstance(m, (int, float))]
            st.update({"running": True, "since": t0, "pass_since": recorded - 90, "at_rest": False, **({"passes": int(max(marks)) - 1} if marks else {})})
            if question:                                  # the review's question, put as this loop's agent would
                st["question"] = {**S.question(recorded), "options": ["Keep the table (fast, 4 KB)", "Compute every call (no table)"],
                                  "question": "Should count_primes keep a precomputed table for n below 1000?\n"
                                              "- Keep the table (fast, 4 KB)\n- Compute every call (no table)"}
        row = dict(st, summary=summary_of(results), **({"agents": list(agents)} if mode == "running" else {}))
        ans[""]["j"]["state"] = {**(ans[""]["j"].get("state") or {}), **st}
        ans["/state"]["j"] = {**ans["/state"]["j"], **st}
        for u, mine in users.items():
            for lst in ("/apps", "/loops", "/admin/apps"):
                for r in (mine.get(lst) or {}).get("j") or []:
                    if (r.get("name") or r.get("app")) == name and (r.get("owner") or r.get("user") or u) == "bob":
                        r.update(row)
        ans["/runs"] = {"j": hist}
        ans["/log/raw?download=false"] = {"x": S.log_text(name, t0, running=mode == "running")}
        ans["/results"] = {"j": results}
        for d in results["designs"]:                      # each design's detail: its measurements (no source kept)
            q = urllib.parse.urlencode(sorted({"design": d.get("base") or d["name"], "part": d.get("part") or "", "key": d.get("key") or ""}.items()))
            ans[f"/design?{q}"] = {"j": {"name": d["name"], "part": d.get("part") or "",
                                         "artifact": f"# {d['name']}: its source is not in the preview's sample data\n",
                                         "trials": [{"stage": st_, "when": d.get("last"), "metrics": m} for st_, m in d["stages"].items()]}}
        if name != "adder8":                              # the review's ideas, turns and asks are an adder's: adder8's only
            if mode == "running":
                ans["/usage"] = {"j": S.usage()}
            ans["/stream?parts=events%2Clive"] = {"stream": {"events": cut(ev, 3000), "live": None}}
            continue
        ans["/ideas"] = {"j": S.ideas()}
        ans["/digests"] = {"j": S.tpl("pop_digests.json")}
        ans["/usage"] = {"j": S.usage()}
        ans["/notes"] = {"j": S.notes()}
        ans["/workbench"] = {"j": S.workbench()}
        turns = cut(S.turns(name))
        ans["/turns"] = {"j": {"turns": turns}}
        for t in turns:
            ans[f"/turns?k={t['k']}"] = {"j": {"turns": [t]}}
        asks = S.asks(recorded)
        ans["/asks"] = {"j": asks}
        for a in asks:
            ans[f"/asks/{enc(a['id'])}/agent"] = {"j": S.ask_agent()}
        ans["/stream?parts=events%2Clive"] = {"stream": {"events": cut(ev, 3000),
                                                         "live": S.agent_live(task) if task and mode == "running" else None}}



if __name__ == "__main__":
    sys.exit(main())
