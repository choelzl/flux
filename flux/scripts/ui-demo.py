#!/usr/bin/env python3
"""Demo loops for looking at the web interface (D1046): small real loops whose agent answers at once with
preset text -- a design that passes, one repaired, one that always fails, one out of time, a scope, a
critic on every box, a sweep, parts, a baseline, a dictionary metric -- and two that keep running, so
LiveAlt, Results, Agents and the Reject popup can be looked at on real data without a model.

    python3 scripts/ui-demo.py                     # a server of its own, the demo loops in it; Ctrl-C ends it
    python3 scripts/ui-demo.py --port 8765 --data /tmp/flux-demo
    python3 scripts/ui-demo.py --server http://127.0.0.1:8000 --user bob --password-file ~/.config/flux/demo.pw
    python3 scripts/ui-demo.py --only agent-repair,critic --no-wait

Every loop is named `demo-<case>` and is replaced when it exists. The agent is a script beside the
document (`agent.py`): it reads its brief, says two lines, runs one command and writes the design --
good, repaired after a first failing draft, always failing, or slow -- and, as a critic, rejects one
verdict in three. Nothing here reaches a model or the network.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

AGENT = r'''import json, sys, time
from pathlib import Path
here = Path(sys.argv[0]).parent
mode, role = sys.argv[1], sys.argv[2]
if mode == "first":
    text, artifact = Path(sys.argv[3]).read_text(), Path(sys.argv[4]); sid = f"s-{role}-{time.time_ns()}"
else:
    sid, text, artifact = sys.argv[3], sys.argv[4], Path(sys.argv[5])
state = Path(__file__).parent / f".demo-{role}-turns"     # the loop's own: each draft is new, never one already measured
n = int(state.read_text()) + 1 if state.exists() else 1
state.write_text(str(n))
say = lambda kind, **x: print(json.dumps({"type": kind, "sessionID": sid, **x}), flush=True)
say("text", part={"text": f"Reading the brief ({len(text)} characters) and the files around it."})
say("tool_use", part={"tool": "bash", "state": {"input": {"command": "ls"}, "output": "problem.yaml check.py measure.py"}})
if role == "slow":
    for i in range(6):
        time.sleep(5)
        say("text", part={"text": f"Still working: step {i + 1} of 6."})
if role == "critic":
    bad = n % 3 == 1
    artifact.write_text(json.dumps({"ok": not bad, "issues": ["the adder ignores the carry-in"] if bad else [],
                                    "wrong": ["sim_us"] if bad else [], "why": "demo critic"}))
else:
    fails = {"good": 0, "bad": 2, "slow": 0}.get(role, 0)
    if role == "repair":
        fails = 2 if mode == "first" else 0
    artifact.write_text(f"design {n}\nfail={fails}\n")
say("text", part={"text": ("Rejected: the adder ignores the carry-in." if bad else "No objection.") if role == "critic" else "The design is written."})
'''
CHECK = r'''import sys, time
from pathlib import Path
text = Path(sys.argv[1]).read_text()
fails = int(text.split("fail=")[1].split()[0]) if "fail=" in text else 0
name = sys.argv[2] if len(sys.argv) > 2 else "test"
if len(sys.argv) > 3:                                  # a slow check: its seconds
    time.sleep(float(sys.argv[3]))
bad = fails if name in ("ut", "it") else 0
for i in range(bad):
    print(f"FAIL {name} case {i}")
print(f"{bad} failing of 3")
'''
MEASURE = r'''import json, sys, time, zlib
from pathlib import Path
text = Path(sys.argv[1]).read_text()
if len(sys.argv) > 2:
    time.sleep(float(sys.argv[2]))
h = zlib.crc32(text.encode()) % 1000
print(f"score={h / 10:.1f}")
print("sim_us=" + json.dumps({"t1": h % 97, "t2": (h * 7) % 89}))
'''
GEN = r'''import sys
from pathlib import Path
Path(sys.argv[1]).write_text(f"design {sys.argv[2] if len(sys.argv) > 2 else 'x'}\nfail={sys.argv[3] if len(sys.argv) > 3 else 0}\n")
'''
SCOPE_CHECK = r'''import sys
from pathlib import Path
n = Path(sys.argv[3]); k = int(n.read_text()) if n.exists() else 0; n.write_text(str(k + 1))
print(f"{1 if k % 2 == 0 else 0} failing of 2")
'''
FILES = {"agent.py": AGENT, "check.py": CHECK, "measure.py": MEASURE, "gen.py": GEN, "scope_check.py": SCOPE_CHECK}


def agent(role: str, timeout: int = 120) -> dict:
    return {"by": {"command": ["python3", "{home}/agent.py", "first", role, "{prompt_file}", "{artifact}"],
                   "resume": ["python3", "{home}/agent.py", "resume", role, "{session}", "{answer}", "{artifact}"],
                   "output": "opencode", "timeout_s": timeout}}


GATE = {"apply": "python3 {home}/check.py {artifact} apply", "build": "python3 {home}/check.py {artifact} build",
        "ut": "python3 {home}/check.py {artifact} ut", "it": "python3 {home}/check.py {artifact} it"}
MEASURE_STAGE = {"esl": {"command": "python3 {home}/measure.py {artifact}", "metrics": ["score", "sim_us"]}}
BASE = {"statement": "Write a design (a demo loop: its agent answers at once with preset text).", "language": "text"}


def flow(**boxes) -> dict:
    return {"test": dict(GATE), "measure": dict(MEASURE_STAGE), **boxes}


#: case -> (document, passes); passes 0: until stopped (a loop that keeps running)
CASES: dict[str, tuple[dict, int]] = {
    "agent-ok": ({**BASE, "flow": flow(generate=agent("good")), "objectives": [{"metric": "score", "direction": "maximize"}]}, 3),
    "agent-repair": ({**BASE, "flow": flow(generate=agent("repair")), "objectives": ["score"]}, 2),
    "agent-always-fails": ({**BASE, "flow": flow(generate=agent("bad")), "objectives": ["score"],
                            "budget": {"repair_attempts": 2, "steps": 3}}, 2),
    "out-of-time": ({**BASE, "flow": {"generate": agent("slow", 3), "test": dict(GATE)},
                     "budget": {"agent_continues": 1, "steps": 2, "repair_attempts": 1}}, 1),
    "scope": ({**BASE, "flow": flow(orchestrate={"scope": {
        "list": "python3 -c \"print('TADD add'); print('TSUB subtract')\"",
        "brief": "python3 -c \"import sys; print('MUST', sys.argv[1])\" {scope}",
        "check": "python3 {home}/scope_check.py {scope} {artifact} {home}/out/scope-count"}}, generate=agent("good")),
        "objectives": ["score"], "budget": {"steps": 5}}, 3),
    "critics": ({**BASE, "flow": {**flow(generate=agent("good"), select={"critic": agent("critic")}),
                                  "test": {**GATE, "critic": agent("critic")},
                                  "measure": {**MEASURE_STAGE, "critic": agent("critic")}},
                 "objectives": ["score"]}, 3),
    "sweep": ({**BASE, "flow": flow(orchestrate={"policy": "sweep", "space": {"x": [1, 2, 3, 4]}},
                                    generate={"command": "python3 {home}/gen.py {artifact} {x}"}), "objectives": ["score"]}, 1),
    "parts": ({**BASE, "parts": {"lo": "the low half", "hi": "the high half"}, "flow": flow(generate=agent("good")),
               "objectives": ["score"]}, 1),
    "baseline": ({**BASE, "baseline": True, "flow": flow(generate=agent("good")), "objectives": ["score"]}, 2),
    "dict-objective": ({**BASE, "flow": flow(generate=agent("good")),
                        "objectives": [{"metric": "sim_us", "type": "dict", "aggregate": "sum", "direction": "minimize", "unit": "us"}]}, 2),
    "gate-always-fails": ({**BASE, "flow": flow(orchestrate={"policy": "sweep", "space": {"x": [1, 2]}},
                                                generate={"command": "python3 {home}/gen.py {artifact} {x} 2"}), "objectives": ["score"]}, 1),
    "two-stages": ({**BASE, "flow": {**flow(generate=agent("good")), "measure": {
        "screen": {"command": "python3 {home}/measure.py {artifact}", "metrics": ["score"]},
        "confirm": {"command": "python3 {home}/measure.py {artifact}", "metrics": ["score", "sim_us"]}}},
        "objectives": ["score"]}, 2),
    # these keep running: an agent at work, a long measurement
    "running-agent": ({**BASE, "flow": flow(generate=agent("slow", 600)), "objectives": ["score"], "budget": {"passes": 0}}, 0),
    # D1089: a check of five slow tests, at work most of the time -- the graph's Check box mid-way (3 of 5)
    "running-check": ({**BASE, "flow": {**flow(generate=agent("good")), "test": {
        t: f"python3 {{home}}/check.py {{artifact}} {t} 6" for t in ("apply", "build", "lint", "ut", "it")}},
        "objectives": ["score"], "budget": {"passes": 0}}, 0),
    "running-measure": ({**BASE, "flow": {**flow(generate=agent("good")), "measure": {
        "slow": {"command": "python3 {home}/measure.py {artifact} 45", "metrics": ["score", "sim_us"], "timeout_s": 600}}},
        "objectives": ["score"], "budget": {"passes": 0}}, 0),
}


class Client:
    """The web API as the page uses it: a session cookie, the X-Flux header on writes."""

    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method: str, path: str, body: dict | None = None, files: list[tuple[str, str, bytes]] | None = None,
             fields: dict | None = None) -> dict:
        headers = {"X-Flux": "1"}
        data = None
        if files is not None:
            boundary = uuid.uuid4().hex
            parts = []
            for k, v in (fields or {}).items():
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
            for field, name, content in files:
                parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\n'
                             'Content-Type: application/octet-stream\r\n\r\n'.encode() + content + b"\r\n")
            data = b"".join(parts) + f"--{boundary}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with self.op.open(req, timeout=60) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path}: {e.code} {e.read().decode(errors='replace')[:300]}") from None
        return json.loads(raw) if raw else {}


def load(client: Client, cases: list[str], wait: bool) -> None:
    import yaml

    for case in cases:
        doc, passes = CASES[case]
        name = f"demo-{case}"
        try:
            client.call("POST", f"/api/apps/{name}/stop", {"now": True})
            client.call("DELETE", f"/api/apps/{name}")
        except RuntimeError:
            pass                                               # not there yet
        files = [("files", "problem.yaml", yaml.safe_dump(doc, sort_keys=False).encode())]
        files += [("files", f, body.encode()) for f, body in FILES.items()]
        client.call("POST", "/api/apps", files=files, fields={"name": name})
        while True:                               # a server runs a few loops at once per user: wait for a slot
            try:
                client.call("POST", f"/api/apps/{name}/start", {"passes": passes} if passes else {"passes": None})
                break
            except RuntimeError as exc:
                if "at most" not in str(exc):
                    raise
                time.sleep(3)
        print(f"  {name}: started ({passes} pass{'es' if passes != 1 else ''})" if passes else f"  {name}: started, runs until stopped", flush=True)
    if not wait:
        return
    finite = [c for c in cases if CASES[c][1]]
    deadline = time.time() + 900
    while finite and time.time() < deadline:
        time.sleep(2)
        for case in list(finite):
            st = client.call("GET", f"/api/apps/demo-{case}/state")
            if not st.get("running"):
                finite.remove(case)
                print(f"  demo-{case}: {'failed: ' + str(st.get('error'))[:120] if st.get('failed') else 'done'}", flush=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", help="load into a server that runs already (its URL)")
    ap.add_argument("--user", default="demo", help="the account to load them as (made when this script starts the server)")
    ap.add_argument("--password-file", help="the account's password, one line (an existing server)")
    ap.add_argument("--port", type=int, default=0, help="the port of the server this script starts (a free one)")
    ap.add_argument("--data", help="the data folder of the server this script starts (a temporary one)")
    ap.add_argument("--only", help="these cases, comma-separated: " + ", ".join(CASES))
    ap.add_argument("--no-wait", action="store_true", help="start them and return, without waiting for the finite ones")
    a = ap.parse_args()
    cases = [c.strip() for c in a.only.split(",")] if a.only else list(CASES)
    cases.sort(key=lambda c: c in CASES and CASES[c][1] == 0)   # the ones that keep running last: they keep their slots
    unknown = [c for c in cases if c not in CASES]
    if unknown:
        ap.error(f"no such case: {', '.join(unknown)}")
    server = None
    if a.server:
        if not a.password_file:
            ap.error("--server needs --password-file (credentials come from a file)")
        url, password = a.server, Path(a.password_file).expanduser().read_text().strip()
    else:
        data = Path(a.data or tempfile.mkdtemp(prefix="flux-demo-"))
        data.mkdir(parents=True, exist_ok=True)
        from flux_web.store import Store

        store, pw_file = Store(data), data / "demo-password"
        if store.user(name=a.user) is None:
            password = secrets.token_urlsafe(12)
            store.add_user(a.user, password, "admin")
            pw_file.write_text(password + "\n")
            pw_file.chmod(0o600)
        elif pw_file.exists():
            password = pw_file.read_text().strip()       # the same data folder again
        else:
            ap.error(f"{a.user} exists in {data} without {pw_file}: use another --user or --data")
        port = a.port or free_port()
        url = f"http://127.0.0.1:{port}"
        server = subprocess.Popen(["flux", "serve", "--port", str(port), "--data", str(data), "--no-sandbox"],
                                  stdout=subprocess.DEVNULL, stderr=open(data / "serve.log", "w"),
                                  env={**os.environ, "XDG_CACHE_HOME": str(data / "cache")})
        for _ in range(120):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.5)
        print(f"server: {url}  (user {a.user}, password in {pw_file}; data in {data})", flush=True)
    client = Client(url)
    client.call("POST", "/api/login", {"name": a.user, "password": password})
    try:
        load(client, cases, wait=not a.no_wait)
        if server is not None:
            print(f"\nopen {url} and log in as {a.user}; Ctrl-C stops the server", flush=True)
            server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(10)
            except subprocess.TimeoutExpired:
                server.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
