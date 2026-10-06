"""The agents (D734, D751, D807): each user's logins to them and their tests -- a loop needs its
agents passed for its owner -- the daily retest, who can write a problem (D704), and Admin › Agents:
every agent's program, login, home files, hosts and variables."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from .models import LoginInput, EnvVar, AgentConfig, AgentNew
from .runs import home_ready, sandbox_config, machine_env, run_env, sandbox_env
from .store import User


def register(app: FastAPI, ctx: SimpleNamespace) -> None:
    """The agents' routes; the readiness checks the other groups need go into `ctx` (D888)."""
    store, sandbox = ctx.store, ctx.sandbox
    user_of, admin_of = ctx.user_of, ctx.admin_of
    _rules, _set_env = ctx.rules, ctx.set_env

    # ---- every user's agent logins (D734, D747: internal users too, since each has a home of their own)
    from .agents import KINDS, check_new, found, registry, version, visible
    from .logins import LIMIT_S, Logins, logged_in

    logins = Logins()

    # ---- is an agent ready for a user (D751): tested from Account, a loop needs it passed
    def agent_test_of(user: User, agent: str) -> dict[str, Any]:
        return store.server_get(f"agent-test:{user.name}:{agent}") or {}

    def agents_gate(user: User, agents: list[str]) -> None:
        """A start, an authoring agent or an ask refused while an agent it needs has not passed
        its test for the loop's owner, whose logins it runs on (D769) -- before a turn is spent (D751)."""
        reg = registry(store)
        bad = [reg[a].label for a in agents if a in reg and not agent_test_of(user, a).get("ok")]
        if bad:
            raise HTTPException(409, f"{', '.join(bad)} not set up for {user.name} yet: {user.name}'s Account › My agents and models, the agent's tab: log in and Test")

    def author_agent(author: Any) -> list[str]:
        name = author.get("preset") if isinstance(author, dict) else author
        return [str(name)] if name in registry(store) else []

    def check_author(author: str) -> None:
        """Who writes or answers (D704, D705): an agent offered here (D807), or Flux's own model."""
        if author != "model" and author not in visible(store):
            raise HTTPException(400, f"who writes or answers is one of {', '.join([*visible(store), 'model'])}")

    testing: set[tuple[str, str]] = set()
    testing_lock = threading.Lock()

    def run_agent_test(user: User, agent: str, why: str = "") -> dict[str, Any]:
        """`flux agent test <agent> --live`, sandboxed as the user's runs are: their home, their
        settings, the network rules. Its result is kept: a passed test enables the agent (D751)."""
        with testing_lock:
            testing.add((user.name, agent))
        try:
            home_ready(store, user)
            env = {**run_env(store, user), "FLUX_SANDBOX_APP": f"{user.name}.agent-test", "PYTHONUNBUFFERED": "1",
                   "FLUX_SANDBOX_TIMEOUT": "450"}                      # D768: it ends itself, whatever happens to us
            sandbox_env(env, sandbox, {})
            machine_env(env, sandbox_config(store), {})
            flux = shutil.which("flux", path=env.get("PATH"))
            argv = [*([flux] if flux else [sys.executable, "-m", "flux_cli"]), "agent", "test", agent, "--live", "--json", "-"]
            try:
                r = subprocess.run(argv, cwd=str(store.home_of(user)), env=env, capture_output=True, text=True, timeout=420,
                                   stdin=subprocess.DEVNULL)
                line = next((ln for ln in reversed(r.stdout.splitlines()) if ln.startswith("{")), "")
                got = json.loads(line) if line else {"agent": agent, "ok": False, "steps": [
                    {"step": "run", "ok": False, "said": " ".join((r.stderr or r.stdout or f"exit {r.returncode}")[-500:].split())}]}
            except subprocess.TimeoutExpired:
                got = {"agent": agent, "ok": False, "steps": [{"step": "run", "ok": False, "said": "no answer within 420 s"}]}
            got["when"] = time.time()
            store.server_set(f"agent-test:{user.name}:{agent}", got)
            store.audit(user.name, "agent test", f"{agent}: {'ready' if got['ok'] else 'not ready'}{f' ({why})' if why else ''}")
            return got
        finally:
            with testing_lock:
                testing.discard((user.name, agent))

    #: D807: an agent's test is asked again a day after the last one -- a login whose session it
    #: refreshes stays alive, and one that can no longer be refreshed is said (and its runs held)
    #: before a loop finds out
    RETEST_S = 24 * 3600.0
    retesting: set[tuple[str, str]] = set()

    def retest_due(now: float | None = None) -> list[tuple[str, str]]:
        """Starts one test that is due (each minute's sample asks): the oldest, of an agent a user
        tested before and is offered, while nothing else of theirs is being tested or logged in."""
        now = time.time() if now is None else now
        if retesting:
            return []
        due = []
        for u in store.users():
            if u.disabled or logins.state(u.name).get("running"):
                continue
            for n in visible(store):
                t = agent_test_of(u, n)
                if t.get("when") and now - float(t["when"]) >= RETEST_S and (u.name, n) not in testing:
                    due.append((float(t["when"]), u, n, bool(t.get("ok"))))
        if not due:
            return []
        _when, u, n, was = min(due, key=lambda x: x[0])
        retesting.add((u.name, n))

        def go() -> None:
            try:
                got = run_agent_test(u, n, why="daily")
                if was and not got.get("ok"):
                    store.notify(u.name, f"{registry(store)[n].label}'s daily test failed: your loops wait for it -- "
                                 "Account › My agents and models, its tab: log in again", "#/account", "warn")
            finally:
                retesting.discard((u.name, n))

        threading.Thread(target=go, daemon=True).start()
        return [(u.name, n)]

    app.state.retest_due = retest_due

    def offered(agent: str) -> Any:
        a = visible(store).get(agent)
        if a is None:
            raise HTTPException(404, f"{agent} is not an agent offered here; agents: {', '.join(visible(store))}")
        return a

    @app.post("/api/agents/{agent}/test")
    def test_agent(agent: str, user: User = Depends(user_of)) -> dict[str, Any]:
        offered(agent)
        return run_agent_test(user, agent)

    @app.get("/api/logins")
    def get_logins(user: User = Depends(user_of)) -> dict[str, Any]:
        """The agents offered here (D807: their program found), each logged in or not, tested or not."""
        agents = visible(store)
        have = logged_in(store.home_of(user), agents)
        mine = store.settings(user)
        for n, a in agents.items():                       # D748: a printed token, kept
            have[n] = have[n] or bool(mine.get(f"FLUX_{a.up}_OAUTH_TOKEN"))
        return {"external": user.external, "agents": [{"id": n, "label": a.label, "kind": a.kind, "logged_in": have[n],
                                                       "tested": agent_test_of(user, n), "testing": (user.name, n) in testing,
                                                       "command": " ".join(a.login_command())} for n, a in agents.items()],
                "session": {k: v for k, v in logins.state(user.name).items() if k != "text"}}

    @app.post("/api/logins/{agent}")
    def start_login(agent: str, user: User = Depends(user_of)) -> dict[str, str]:
        a = offered(agent)
        try:
            cmd = a.login_command()
            printed = KINDS[a.kind]["printed"]
            home_ready(store, user)
            store.server_set(f"agent-test:{user.name}:{agent}", None)        # D751: a new login is tested again
            env = {**run_env(store, user), "FLUX_SANDBOX_APP": f"{user.name}.login", "PYTHONUNBUFFERED": "1",
                   "FLUX_SANDBOX_TIMEOUT": str(int(LIMIT_S) + 60)}  # D768: it ends itself, whatever happens to us
            sandbox_env(env, sandbox, {})
            machine_env(env, sandbox_config(store), {})       # the network rules apply to everyone
            def tested_after(rc: int) -> None:          # D768: a login that ended well is tested at once
                if rc == 0:
                    threading.Thread(target=run_agent_test, args=(user, agent), daemon=True).start()

            logins.start(user.name, agent, store.home_of(user), cmd, env,
                         on_secret=lambda name, value: store.set_setting(user, name, value),   # D748
                         on_end=tested_after, printed=(printed, f"FLUX_{a.up}_OAUTH_TOKEN") if printed else None)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        store.audit(user.name, "agent login", f"{agent}: {' '.join(cmd)}")
        return {"ok": f"{agent}'s login started"}

    @app.get("/api/logins/session")
    def login_session(since: int = 0, user: User = Depends(user_of)) -> dict[str, Any]:
        return logins.state(user.name, since)

    @app.post("/api/logins/session/input")
    def login_input(body: LoginInput, user: User = Depends(user_of)) -> dict[str, str]:
        try:
            logins.send(user.name, body.text, body.key)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": "sent"}

    @app.post("/api/logins/session/stop")
    def login_stop(user: User = Depends(user_of)) -> dict[str, str]:
        logins.stop(user.name)
        return {"ok": "stopping"}

    @app.get("/api/agents")
    def agents(user: User = Depends(user_of)) -> list[dict[str, Any]]:
        """Who can write a problem on this server for this user (D704)."""
        from .authoring import available

        env = run_env(store, user)
        got = available(env, visible(store))
        default = env.get("FLUX_DEFAULT_AGENT")     # D705: the admin's, or the user's own
        for a in got:
            a["default"] = a["id"] == default
        return got

    # ---- Admin › Agents (D756, D807): every agent -- the three built-in ones and those added, each a
    # kind -- its program, login, arguments, the files every home starts with for it, the hosts it
    # needs; whether this server finds it (only then is it offered to users); who has it ready
    def _paths(raw: list[str], what: str) -> list[str]:
        out = []
        for rel in raw:
            r = rel.strip().removeprefix("~/")
            if r.startswith("/") or ".." in r.split("/"):            # before the slashes go: /etc/passwd is not in a home
                raise HTTPException(400, f"{rel!r}: a path inside the home folder, such as {what}")
            r = r.strip("/")
            if r:
                out.append(r)
        return out

    def _program(exe: str) -> str:
        exe = exe.strip()
        if exe and not (exe.startswith("/") or re.fullmatch(r"[A-Za-z0-9_.+-]+", exe)):
            raise HTTPException(400, "the program: an absolute path, or a name found on PATH")
        return exe

    @app.get("/api/admin/agents")
    def admin_agents(_a: User = Depends(admin_of)) -> dict[str, Any]:
        from concurrent.futures import ThreadPoolExecutor

        reg = registry(store)
        with ThreadPoolExecutor(max_workers=max(1, len(reg))) as pool:       # each asked at once
            seen = dict(zip(reg, pool.map(lambda a: (lambda f: (f, version(f)))(found(a, store)), reg.values())))
        out = []
        for name, a in reg.items():
            prog, ver = seen[name]
            users = []
            for u in store.users():
                t = agent_test_of(u, name)
                users.append({"user": u.name, "kind": u.role, "state": "ready" if t.get("ok") else "failed" if t.get("when") else "not tested",
                              "when": t.get("when")})
            out.append({"id": name, "kind": a.kind, "builtin": a.builtin, "label": a.label, "bin": a.bin, "login": a.login,
                        "login_default": KINDS[a.kind]["login"], "args": a.args, "home": a.home, "hosts": a.hosts,
                        "login_files": a.login_files, "found": prog, "version": ver, "users": users})
        return {"agents": out, "kinds": [{"id": k, "label": v["label"]} for k, v in KINDS.items()]}

    @app.post("/api/admin/agents")
    def add_admin_agent(body: AgentNew, a: User = Depends(admin_of)) -> dict[str, Any]:
        """An agent of a kind under a name of its own (D807): an OpenCode of a company's own beside
        the plain one -- offered once its program is found."""
        try:
            name = check_new(store, body.name, body.kind)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        cfg = store.server_get("agents") or {}
        cfg[name] = {"kind": body.kind, "label": body.label.strip() or name, "bin": _program(body.bin)}
        store.server_set("agents", cfg)
        store.audit(a.name, "agent added", f"{name}: a {body.kind}, program {cfg[name]['bin'] or '(none yet)'}")
        return {"ok": f"{name} added", "found": found(registry(store)[name], store)}

    @app.delete("/api/admin/agents/{agent}")
    def remove_admin_agent(agent: str, a: User = Depends(admin_of)) -> dict[str, str]:
        reg = registry(store)
        if agent not in reg:
            raise HTTPException(404, f"no agent named {agent}")
        if reg[agent].builtin:
            raise HTTPException(409, f"{agent} is built in: clear its program instead")
        keys = sum(reg[agent].keys().values(), ())
        store.forget_settings(keys)                               # its settings, the server's and every user's
        store.server_set(f"env:agent:{agent}", None)
        for u in store.users():
            store.server_set(f"env:agent:{agent}:user:{u.id}", None)
            store.server_set(f"agent-test:{u.name}:{agent}", None)
        cfg = store.server_get("agents") or {}
        cfg.pop(agent, None)
        store.server_set("agents", cfg or None)
        store.audit(a.name, "agent removed", agent)
        return {"ok": f"{agent} removed"}

    @app.put("/api/admin/agents/{agent}")
    def put_admin_agent(agent: str, body: AgentConfig, a: User = Depends(admin_of)) -> dict[str, Any]:
        reg = registry(store)
        if agent not in reg:
            raise HTTPException(404, f"no agent named {agent}; agents: {', '.join(reg)}")
        login = body.login.strip()
        if login:
            import shlex

            try:
                if not shlex.split(login):
                    raise ValueError
            except ValueError as exc:
                raise HTTPException(400, "the login command: a command line, e.g. opencode auth login") from exc
        x = reg[agent]
        x.bin, x.login, x.args = _program(body.bin), login, body.args.strip()
        x.label = body.label.strip() or x.label
        x.home, x.hosts, x.login_files = _paths(body.home, ".config/opencode"), _rules(body.hosts), _paths(body.login_files, ".local/share/nga/auth.json")
        cfg = store.server_get("agents") or {}
        cfg[agent] = x.stored()
        store.server_set("agents", cfg)
        store.audit(a.name, "agent settings", f"{agent}: program {x.bin or '(on PATH)'}; login {x.login or '(default)'}; "
                    f"{len(x.home)} home path(s), {len(x.hosts)} host(s)")
        return {"ok": f"{agent} saved"}

    # ---- each agent's own variables (D807): the server's (an admin's), a user's own
    def _agent_named(agent: str) -> str:
        if agent not in registry(store):
            raise HTTPException(404, f"no agent named {agent}")
        return agent

    @app.put("/api/admin/agents/{agent}/env")
    def put_agent_env(agent: str, body: EnvVar, a: User = Depends(admin_of)) -> list[dict[str, Any]]:
        return _set_env(f"agent:{_agent_named(agent)}", body, a, f"the server's {agent}")

    @app.put("/api/agents/{agent}/env")
    def put_my_agent_env(agent: str, body: EnvVar, user: User = Depends(user_of)) -> list[dict[str, Any]]:
        return _set_env(f"agent:{_agent_named(agent)}:user:{user.id}", body, user, f"{user.name}'s {agent}")

    # what a start, an author and an ask are gated on (D751, D769), and the minute's sample's retest (D807)
    ctx.agents_gate, ctx.author_agent, ctx.check_author, ctx.retest_due = agents_gate, author_agent, check_author, retest_due
