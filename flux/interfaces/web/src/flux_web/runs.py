"""A loop's starts from the web (D683, D689). A loop -- an application -- is running or not; starting
it again resumes it from its record. Each start is `flux task run` detached in its own session,
sandboxed, appending to the loop's one log (`<app>/runs/loop.log`, a line marking each start), with
one answer (`runs/answer.json`) and one notes inbox (`runs/inbox.jsonl`). The server keeps its
starts to know the process and the audit trail; nothing shows them as numbers. What a loop is doing
is read from what it writes -- the record's run pointer, `run.json`, `events.jsonl`, `turns.jsonl` --
so a loop outlives the server, and a restarted server finds it again."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .store import Store, User

__all__ = ["RunManager", "loop_files", "run_env"]

#: The server's own model keys: never in the run of a user who brought their own endpoint.
_SERVER_KEYS = ("FLUX_REMOTE_API_KEY", "FLUX_REMOTE_API_KEY_FILE", "OPENROUTER_API_KEY")
#: D734: the machine's model and agent variables an external user's run never gets.
_MODEL_VARS = ("FLUX_REMOTE_", "FLUX_LLM_", "OLLAMA_", "OPENROUTER_", "ANTHROPIC_", "OPENAI_", "FLUX_OPENCODE_",
               "FLUX_CLAUDE_", "FLUX_CODEX_", "OPENCODE_", "CLAUDE_", "CODEX_", "FLUX_DEFAULT_AGENT")

#: The agents' folders in the server account's environment, never a run's (D748): each user's are in their home.
_OWN_FOLDERS = ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "OPENCODE_CONFIG_DIR", "NGA_DATA_HOME", "XDG_CONFIG_HOME",
                "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")


def home_ready(store: Store, user: User) -> Path:
    """`user`'s Flux home (D744), started from the admin's list of home files where it lacks
    them -- copied from this server account's home, never over what the user has."""
    from flux_cli.sandbox import seed_home

    home = store.home_of(user)
    seeds = list((store.server_get("sandbox") or {}).get("home_seed", list(HOME_SEED)))
    for a in (store.server_get("agents") or {}).values():   # D756: each agent's own files too
        seeds += list(a.get("home") or [])
    seed_home(home, Path(os.path.expanduser("~")), list(dict.fromkeys(seeds)))
    return home


#: What a home starts with unless the admin says otherwise (D744): the agents' configuration --
#: never anyone's logins: each user logs in on their Account page.
HOME_SEED = (".config/opencode",
             # D765: a corporate OpenCode's own parts beside its login (its plugins' tools and assets, its state)
             ".local/share/opencode/mapper", ".local/share/opencode/icons", ".local/share/opencode/mock-tools",
             ".local/share/opencode/request-utils", ".local/share/opencode/images", ".local/state/opencode/kv2.json")


def run_env(store: Store, user: User, app: str | None = None) -> dict[str, str]:
    """The environment of a user's run or check (D684, D696): the server's, then the model
    settings the admin set for the server, then the user's own. A run never reads the server's
    flux.env itself (FLUX_CONFIG): the server loaded it once. Per group (Flux's model, each agent's
    own, D807), a user who names their own endpoint gets none of the server's values of that group
    -- no server key goes to someone else's endpoint. HOME (D744) is `user`'s Flux home:
    a loop's owner, whose agents' logins a shared loop runs on, whoever starts it (D769)."""
    from .store import GROUPS

    env = {**os.environ, "FLUX_CONFIG": os.devnull}
    # D748: an agent's own folder set for the server account (CODEX_HOME=~/.codex) points away from
    # the user's home: their login and sessions would land in the scratch of the server's HOME path
    for k in _OWN_FOLDERS:
        env.pop(k, None)
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)       # D748: a login is a person's, never the server account's
    server, mine = store.server_settings(reveal=True), store.settings(user, reveal=True)
    if user.external:
        # D734: an external user brings their own: none of the machine's model and agent settings
        # (the server's flux.env is in its environment) and none of the admin's -- only the
        # agents' programs, which say what runs, not on whose account
        env = {k: v for k, v in env.items() if not k.startswith(_MODEL_VARS) or k.endswith("_BIN")}
        server = {}
    web: dict[str, str] = {}
    for name, g in GROUPS.items():
        keys = (*g["public"], *g["secret"])
        if mine.get(g["endpoint"]):
            for k in keys:
                env.pop(k, None)
            if name == "model":
                for k in _SERVER_KEYS:
                    env.pop(k, None)
            vals = {k: mine[k] for k in keys if k in mine}
        else:
            vals = {**{k: server[k] for k in keys if k in server}, **{k: mine[k] for k in keys if k in mine}}
            if name == "model" and vals.get("FLUX_REMOTE_API_KEY"):
                env.pop("FLUX_REMOTE_API_KEY_FILE", None)             # a key set here wins over the server's file
        web.update(vals)
    env.update(web)
    if web.get("FLUX_REMOTE_BASE_URL"):
        env["FLUX_LLM_REMOTE"] = "1"
    names: list[str] = list(web)
    _agents(store, user, env, server, mine, web, names)
    # D697: the variables set on the web -- the server's, the user's, the loop's, in that order;
    # their names pass into the sandbox whatever they look like, and to every agent (D807)
    shared: list[str] = []
    for scope in (*(() if user.external else ("global",)), f"user:{user.id}", *([f"loop:{user.name}:{app}"] if app else [])):
        for name, x in store.env(scope, reveal=True).items():
            env[name] = x["value"]
            names.append(name)
            shared.append(name)
    if shared:
        env["FLUX_SHARED_VARS"] = ",".join(dict.fromkeys(shared))
    if names:
        env["FLUX_SANDBOX_PASS"] = ",".join(dict.fromkeys(names))
    env["FLUX_SANDBOX_REFUSALS"] = str(store.refusals_file)      # D708: hosts its sandbox refused, for the audit
    env["FLUX_SANDBOX_HOME"] = str(store.home_of(user))   # D744: every user's own home (started by `home_ready`)
    return env


_LOGIN_PATH: list[str] = []


def login_path() -> list[str]:
    """The server user's login PATH (D698), asked of their own shell once (from the user database,
    not $SHELL, which a nix shell sets to its own): a service or a nix shell often runs without
    ~/.local/bin, ~/.opencode/bin and the like. Interactive too, for what .bashrc adds; the answer
    is read between markers, past whatever the shell prints."""
    if not _LOGIN_PATH:
        import pwd
        import re

        try:
            shell = pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"
        except KeyError:
            shell = "/bin/sh"
        base = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        try:
            text = Path("/etc/environment").read_text()
            m = re.search(r'^PATH="?([^"\n]+)"?', text, re.M)
            base = m.group(1) if m else base
        except OSError:
            pass
        home = os.path.expanduser("~")
        try:
            r = subprocess.run([shell, "-ilc", 'printf "\\n@@FLUXPATH@@%s@@\\n" "$PATH"'], capture_output=True, text=True, timeout=20,
                               stdin=subprocess.DEVNULL, start_new_session=True,
                               env={"HOME": home, "USER": os.environ.get("USER", ""), "LOGNAME": os.environ.get("USER", ""),
                                    "SHELL": shell, "TERM": "dumb", "PATH": base})
            m = re.search(r"@@FLUXPATH@@(.*?)@@", r.stdout)
            if m:
                _LOGIN_PATH.extend(d for d in m.group(1).split(os.pathsep) if d.startswith("/"))
        except (OSError, subprocess.TimeoutExpired):
            pass
    return list(_LOGIN_PATH)


#: Network rules an allowlist takes: a domain (and its subdomains), *.domain, an IP, a CIDR, localhost.
HOST_RULE = r"(\*\.)?[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*|[0-9A-Fa-f:.]+(/\d{1,3})?"


def sandbox_config(store: Store) -> dict[str, Any]:
    """What every sandbox gets (D698), with the hosts each agent needs (D756) in its allowlist."""
    cfg = dict(store.server_get("sandbox") or {})
    hosts = [h for a in (store.server_get("agents") or {}).values() for h in (a.get("hosts") or [])]
    if hosts:
        cfg["allow"] = list(dict.fromkeys([*(cfg.get("allow") or []), *hosts]))
    return cfg


def machine_env(env: dict[str, str], cfg: dict[str, Any], adv: dict[str, Any], asked: list[str]) -> str:
    """What the admin set for every sandbox (D698): PATH directories (and the server user's login
    PATH) and the network (the home files start each user's home, D744: `home_ready`). Returns how the network
    was set, for the log line. `asked`: the start's own allowlist entries."""
    from urllib.parse import urlsplit

    dirs = [*(cfg.get("path") or []), *(login_path() if cfg.get("login_path") else [])]
    have = env.get("PATH", "").split(os.pathsep)
    extra = [d for d in dict.fromkeys(dirs) if d and d not in have and os.path.isdir(d)]
    if extra:
        env["PATH"] = os.pathsep.join([*extra, *[d for d in have if d]])   # the sandbox mounts each, read-only
    env.pop("FLUX_SANDBOX_ALLOW", None)
    env.pop("FLUX_SANDBOX_NET", None)
    if env.get("FLUX_SANDBOX") == "0":
        return ""                                         # on the host: the admin chose this loop's network as the machine's
    loop = [str(x) for x in adv.get("allow") or []]
    if cfg.get("network") == "allowlist":
        allow = [*(cfg.get("allow") or []), *loop, *(asked if cfg.get("users_add") else [])]
        if cfg.get("endpoints"):
            for url in _endpoints(env):
                allow.append(urlsplit(url).hostname or "")
        allow = [a for a in dict.fromkeys(a.strip() for a in allow) if a]
        env["FLUX_SANDBOX_NET"] = "allowlist"
        env["FLUX_SANDBOX_ALLOW"] = ",".join(allow)
        return _net_said(allow)
    allow = [a for a in dict.fromkeys(str(x).strip() for x in [*loop, *asked]) if a]
    if allow:
        env["FLUX_SANDBOX_ALLOW"] = ",".join(allow)
        return _net_said(allow)
    return ""


def _endpoints(env: dict[str, str]) -> list[str]:
    """The endpoints a run's model and agents call: Flux's, Ollama's, each agent's own (D807)."""
    out = [env[k] for k in ("FLUX_REMOTE_BASE_URL", "OLLAMA_BASE_URL", "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL") if env.get(k)]
    for k, v in env.items():
        if re.fullmatch(r"FLUX_[A-Z0-9_]+_ENV", k):
            try:
                own = json.loads(v)
                oc = json.loads(own.get("OPENCODE_CONFIG_CONTENT") or "{}")
            except (ValueError, AttributeError):
                continue
            out += [own[x] for x in ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL") if own.get(x)]
            out += [p.get("options", {}).get("baseURL") for p in (oc.get("provider") or {}).values() if isinstance(p, dict)]
    return [u for u in out if u]


def _net_said(allow: list[str]) -> str:
    """The network in the run's log, which its users read: limited, and by how many entries --
    never which hosts (D716: the admin's list is the admin's)."""
    return f"network: an allowlist of {len(allow)} entr{'y' if len(allow) == 1 else 'ies'}" if allow else "network: none (an empty allowlist)"


#: A loop's settings only an admin sets (D697), with what each does to its runs.
ADVANCED = {"sandbox": "run in the sandbox (off: on the host)", "memory": "memory limit (e.g. 16g)", "cpus": "CPUs (e.g. 8)",
            "pids": "processes at most", "tmp_size": "scratch /tmp size (e.g. 20g)",
            "allow": "hosts this loop may reach as well (D698)",
            "parallel": "parallel work allowed: the document's workers and parts at once (off: one at a time, D741)"}


def advanced(store: Store, user_name: str, app: str) -> dict[str, Any]:
    return store.server_get(f"adv:{user_name}:{app}") or {}


def sandbox_env(env: dict[str, str], server_sandbox: bool, adv: dict[str, Any]) -> None:
    """The sandbox as the server and the loop's advanced settings say (D697)."""
    for k in ("FLUX_SANDBOX", "FLUX_SANDBOX_MEMORY", "FLUX_SANDBOX_CPUS", "FLUX_SANDBOX_PIDS", "FLUX_SANDBOX_TMP_SIZE"):
        env.pop(k, None)
    if not server_sandbox:
        env["FLUX_SANDBOX"] = "0"                    # D704: a --no-sandbox server says so (the command's own default is on)
        if env.get("FLUX_SANDBOX_HOME"):
            env["HOME"] = env["FLUX_SANDBOX_HOME"]        # D744: on the host too, the user's agents use their home
        return
    if adv.get("sandbox") is False:
        env["FLUX_SANDBOX"] = "0"                    # an admin's choice for this loop: on the host
        if env.get("FLUX_SANDBOX_HOME"):
            env["HOME"] = env["FLUX_SANDBOX_HOME"]        # D744
        return
    env["FLUX_SANDBOX"] = "1"                        # a shared server runs nothing on the host
    for key, var in (("memory", "FLUX_SANDBOX_MEMORY"), ("cpus", "FLUX_SANDBOX_CPUS"), ("pids", "FLUX_SANDBOX_PIDS"),
                     ("tmp_size", "FLUX_SANDBOX_TMP_SIZE")):
        if adv.get(key) not in (None, ""):
            env[var] = str(adv[key])


def _agents(store: Store, user: User, env: dict[str, str], server: dict[str, str], mine: dict[str, str],
            flux: dict[str, str], names: list[str]) -> None:
    """Each agent the server offers, as its runs get it (D807): its program (its folder on PATH, so
    the sandbox mounts it, D705), the admin's arguments and login files (D756, D760), and its own
    variables -- its endpoint, key and model as its kind reads them, and its variables, the server's
    then the user's -- in `FLUX_<NAME>_ENV`, which only that agent is given; an added agent's kind
    in `FLUX_AGENTS`."""
    from .agents import found, run_settings, visible

    added: dict[str, str] = {}
    for a in visible(store).values():
        exe = found(a, store)
        if a.bin or "/" in exe:
            env[f"FLUX_{a.up}_BIN"] = exe
            folder = os.path.dirname(exe)
            if folder and folder not in env.get("PATH", "").split(os.pathsep):
                env["PATH"] = os.pathsep.join([folder, *[d for d in env.get("PATH", "").split(os.pathsep) if d]])
        if not a.builtin:
            added[a.name] = a.kind
        variables = {**({} if user.external else {n: x["value"] for n, x in store.env(f"agent:{a.name}", reveal=True).items()}),
                     **{n: x["value"] for n, x in store.env(f"agent:{a.name}:user:{user.id}", reveal=True).items()}}
        own, args = run_settings(a, server, mine, flux, variables, env)
        if own:
            env[f"FLUX_{a.up}_ENV"] = json.dumps(own)
            names.append(f"FLUX_{a.up}_ENV")
        extra = " ".join(x for x in (a.args, env.get(f"FLUX_{a.up}_ARGS", ""), " ".join(shlex.quote(t) for t in args)) if x)
        if extra:
            env[f"FLUX_{a.up}_ARGS"] = extra
        if a.login_files:
            env[f"FLUX_{a.up}_LOGIN_FILES"] = ",".join(a.login_files)
    if added:
        env["FLUX_AGENTS"] = json.dumps(added)


def loop_files(app_dir: Path) -> dict[str, Path]:
    """The loop's one log, answer and inbox, whatever the number of starts."""
    d = app_dir / "runs"
    return {"log": d / "loop.log", "answer": d / "answer.json", "inbox": d / "inbox.jsonl"}


class RunManager:
    def __init__(self, store: Store, *, sandbox: bool = True, max_running: int = 4) -> None:
        self.store, self.sandbox, self.max_running = store, sandbox, max_running

    # ---- start: the loop resumes from its record
    def start(self, user: User, app: str, app_dir: Path, document: str, doc_id: str, options: dict[str, Any],
              by: User | None = None) -> None:
        """`user`: whose loop (its record, settings, limits); `by`: who started it, when another (D701)."""
        by = by or user
        paused = self.store.server_get("paused")
        if paused:
            raise ValueError(f"starts are paused by an admin: {paused}")
        mine = self.store.runs(user)
        if any(r["app"] == app and self.live(r) for r in mine):
            raise ValueError(f"{app} is running")
        limit = self.limit(user)
        if sum(1 for r in mine if self.live(r)) >= limit:
            raise ValueError(f"at most {limit} loop(s) running at once for {user.name}")
        (app_dir / "out").mkdir(exist_ok=True)
        files = loop_files(app_dir)
        files["log"].parent.mkdir(exist_ok=True)
        files["inbox"].touch()
        db = app_dir / "out" / f"{doc_id}.db"
        flux = shutil.which("flux") or sys.argv[0]
        argv = [flux, "task", "run", str(app_dir / document), "--db", str(db), "--json", str(files["answer"])]
        passes = options.get("passes")
        if passes is not None:
            argv += ["--passes", str(int(passes))]
        if options.get("screen_only"):
            argv.append("--screen-only")
        home_ready(self.store, user)                          # D744: started before anything runs in it, not on a page's look
        # D769: the owner's loop runs as the owner's -- their agents' logins too, whoever starts it
        env = {**run_env(self.store, user, app), "FLUX_SANDBOX_APP": f"{user.name}.{app}", "PYTHONUNBUFFERED": "1",
               "FLUX_FEEDBACK_INBOX": str(files["inbox"])}                  # D684: notes and answers from the page
        adv = advanced(self.store, user.name, app)
        if adv.get("parallel"):                       # D741: an admin allows it; the document says how much
            env.pop("FLUX_PARALLEL_MAX", None)
        else:
            env["FLUX_PARALLEL_MAX"] = "1"
        sandbox_env(env, self.sandbox, adv)
        machine_env(env, sandbox_config(self.store), adv, list(options.get("allow") or []))
        if adv.get("sandbox") is False and self.sandbox:
            options = {**options, "host": True}
        said = [f"{passes} pass(es)" if passes else "until stopped"] + (["screen only"] if options.get("screen_only") else []) \
            + (["on the host, no sandbox (an admin's setting)"] if options.get("host") else [])   # D720: not the network
        with open(files["log"], "a") as fh:
            fh.write(f"\n── started {time.strftime('%Y-%m-%d %H:%M:%S')} by {by.name} · {', '.join(said)} ──\n")
        run_id = self.store.add_run(user, app, str(db), str(files["log"]), argv, options)
        # D732: through the stamper, which writes each line with its time
        proc = subprocess.Popen([sys.executable, "-m", "flux_web.stamp", str(files["log"]), "--", *argv], cwd=str(app_dir),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                env=env, start_new_session=True)
        self.store.set_run(run_id, pid=proc.pid)
        threading.Thread(target=self._wait, args=(run_id, proc), daemon=True).start()

    def limit(self, user: User) -> int:
        """The user's loops running at once: an admin's setting for them (D695), else the server's."""
        got = self.store.server_get(f"max_running:{user.name}")
        return int(got) if got is not None else self.max_running

    def _wait(self, run_id: int, proc: subprocess.Popen) -> None:
        rc = proc.wait()
        self.store.set_run(run_id, ended=time.time(), rc=rc)

    # ---- state
    def live(self, run: dict[str, Any]) -> bool:
        if run.get("ended"):
            return False
        pid = run.get("pid")
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            self.store.set_run(run["id"], ended=time.time())      # gone while the server was away
            return False
        except PermissionError:
            return True
        return True

    def latest(self, user: User, app: str) -> dict[str, Any] | None:
        runs = self.store.runs(user, app)
        return runs[0] if runs else None

    def campaign(self, run: dict[str, Any] | None) -> tuple[str | None, str | None]:
        """(campaign id, its run directory) from the record's run pointer, once a start made one."""
        if not run:
            return None, None
        try:
            pointer = json.loads(Path(f"{run['db']}.runs.json").read_text())
        except (OSError, ValueError):
            return None, None
        if not pointer:
            return None, None
        cid, rdir = list(pointer.items())[-1]
        return cid, rdir

    #: A log line that says what went wrong (D757), as the page's own log marks problems.
    _PROBLEM = re.compile(r"\b(error|errors|traceback|exception|failed|failure|refused|missing|cannot|not answerable|"
                          r"did not build|timed out|killed|no such|not found|not on path|not installed|exited \d)\b", re.I)

    def failure(self, run: dict[str, Any], n: int = 4) -> list[str]:
        """The last lines of a failed start's log that say what went wrong (its last lines when
        none does), without their time stamps."""
        try:
            with open(run["log"], "rb") as fh:
                fh.seek(max(0, os.path.getsize(run["log"]) - 64 * 1024))
                tail = fh.read().decode("utf-8", "replace").splitlines()
        except (OSError, KeyError, TypeError):
            return []
        at = max((i for i, ln in enumerate(tail) if "── started" in ln), default=-1)
        lines = [re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} ", "", ln).rstrip() for ln in tail[at + 1:]]
        lines = [ln for ln in lines if ln.strip() and not re.match(r"flux [a-z ]+: in the \w+ sandbox ", ln)]   # where it ran, not why
        said = [ln for ln in lines if self._PROBLEM.search(ln)]
        return [ln[:400] for ln in (said or lines)[-n:]]

    def state(self, user: User, app: str) -> dict[str, Any]:
        """The loop's state: running or not, since when, its last activity, and while it runs its
        pass, whether a stop is asked, its sandbox and an open question."""
        from flux_loop import ops

        run = self.latest(user, app)
        info: dict[str, Any] = {"app": app, "user": user.name, "running": False, "since": None, "last_active": None,
                                "failed": False, "stopped": False, "question": None, "events": False, "options": {}}
        if run is None:
            return info
        running = self.live(run)
        info.update(running=running, since=run["started"] if running else None,
                    last_active=(time.time() if running else (run.get("ended") or run["started"])),
                    failed=(not running and run.get("rc") not in (0, None, 130)), stopped=(not running and run.get("rc") == 130),
                    options=json.loads(run.get("options") or "{}"))
        if info["failed"]:
            info["error"] = self.failure(run)              # D757: why, in the run's own words
        cid, rdir = self.campaign(run)
        info["campaign"] = cid
        if cid and running:
            st = ops.status(cid, run["db"])
            if st.get("started") and abs(float(st["started"]) - float(run["started"])) < 300:   # this start's registration
                info.update(passes=st.get("passes"), at_rest=st.get("at_rest"), stop_requested=bool(st.get("stop")),
                            container=st.get("container"))
        info["events"] = bool(rdir and os.path.exists(os.path.join(rdir, "events.jsonl")))
        info["question"] = self.open_question(run, rdir) if running else None
        return info

    def open_question(self, run: dict[str, Any], rdir: str | None) -> dict[str, Any] | None:
        """The agent's question still waiting for the operator (D688): the journal's last
        `question` mark of this start, when no note came after it and its time is not up."""
        if not rdir:
            return None
        path = os.path.join(rdir, "events.jsonl")
        try:
            with open(path, "rb") as fh:
                fh.seek(max(0, os.path.getsize(path) - 256 * 1024))
                tail = fh.read().decode("utf-8", "replace").splitlines()
        except OSError:
            return None
        asked = None
        for line in reversed(tail):
            if '"question"' not in line:
                continue
            try:
                e = json.loads(line)
                if e.get("ev") == "mark" and e.get("name") == "question" and e.get("t", 0) >= run["started"] - 1:
                    asked = json.loads(e["why"])
                    break
            except ValueError:
                continue
        if not asked:
            return None
        if time.time() > float(asked.get("asked", 0)) + float(asked.get("wait_s", 0)):
            return None
        if any(float(n.get("t", 0)) >= float(asked.get("asked", 0)) for n in self.notes(Path(run["log"]).parent)):
            return None
        return asked

    def events_path(self, run: dict[str, Any] | None) -> str | None:
        _cid, rdir = self.campaign(run)
        return os.path.join(rdir, "events.jsonl") if rdir else None

    def turns_path(self, run: dict[str, Any] | None) -> str | None:
        _cid, rdir = self.campaign(run)
        return os.path.join(rdir, "turns.jsonl") if rdir else None

    # ---- notes, into the loop's inbox (D684)
    def note(self, runs_dir: Path, user: User, text: str) -> None:
        import secrets

        with open(runs_dir / "inbox.jsonl", "a") as fh:
            fh.write(json.dumps({"id": secrets.token_hex(6), "text": text, "by": user.name, "t": time.time()}) + "\n")

    def forget_note(self, runs_dir: Path, user: User, ident: str) -> bool:
        """D808: a note removed from the page -- a line saying so, never the note's line taken out:
        a running loop reads the file from where it stopped. One it has not read yet it skips; what
        it read already stays in its record."""
        if ident not in {n["id"] for n in self.notes(runs_dir)}:
            return False
        with open(runs_dir / "inbox.jsonl", "a") as fh:
            fh.write(json.dumps({"forget": ident, "by": user.name, "t": time.time()}) + "\n")
        return True

    def notes(self, runs_dir: Path) -> list[dict[str, Any]]:
        """The notes on the page, each with its `id` (an old one's is its time), the removed left out."""
        try:
            lines = (runs_dir / "inbox.jsonl").read_text().splitlines()
        except OSError:
            return []
        docs = []
        for ln in lines:
            try:
                doc = json.loads(ln)
            except ValueError:
                continue
            if isinstance(doc, dict):
                docs.append(doc)
        gone = {str(d["forget"]) for d in docs if d.get("forget") is not None}
        out = []
        for d in docs:
            if d.get("forget") is None and "text" in d:
                d = {**d, "id": str(d.get("id") or d.get("t"))}
                if d["id"] not in gone:
                    out.append(d)
        return out

    # ---- stop
    def stop(self, run: dict[str, Any] | None, now: bool = False, why: str = "stopped from the web") -> str:
        from flux_loop import ops

        if not run or not self.live(run):
            return "not running"
        cid, _rdir = self.campaign(run)
        if cid:
            ops.request_stop(cid, why, db=run["db"])
            if now and ops.interrupt(cid, run["db"]):
                return "stopping now: the pass ends, the record keeps what was judged"
            if not now:
                return "it stops at the end of this pass"
        try:                                                  # before it registered, or no answer: the process group
            os.killpg(int(run["pid"]), signal.SIGINT)
            return "stopping now"
        except (ProcessLookupError, PermissionError, TypeError):
            return "not running"
