"""The server's own database (D683): users, sessions, runs, and an audit trail. SQLite beside
the users' data; every function opens its own connection (FastAPI serves from threads)."""

from __future__ import annotations

import hashlib
import json
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SESSION_DAYS = 7
_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, pw TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
    created REAL NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created REAL NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, app TEXT NOT NULL, db TEXT NOT NULL, log TEXT NOT NULL,
    pid INTEGER, argv TEXT NOT NULL, started REAL NOT NULL, ended REAL, rc INTEGER, options TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, t REAL NOT NULL, user TEXT, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS failures (name TEXT NOT NULL, t REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (user_id, key));
CREATE TABLE IF NOT EXISTS server (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS invites (
    token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL);
"""
#: D818: how long an invitation or a reset link opens
INVITE_DAYS = 7
#: a password set from a link: as the page asks
MIN_PASSWORD = 10
#: the password of an account invited and not yet set: no password matches it
_UNSET = "unset"

#: The model settings of runs (D684, D696), by what uses them: Flux's own model calls, the agent by
#: default, other providers; each coding agent's are its own (D807: `agents.py`, a group per
#: agent). The admin sets them for the server; a user's own override theirs. Keys are
#: secret: stored encrypted, never sent back, only handed to runs. `endpoint`: a user who names
#: their own gets none of the server's values of that group.
#: D721: each group shows on a tab of its own (`tab`); the agent by default beside Flux's model.
GROUPS: dict[str, dict[str, Any]] = {
    "model": {"label": "Flux's own model (OpenAI-compatible)", "tab": "Flux", "endpoint": "FLUX_REMOTE_BASE_URL",
              "public": ("FLUX_REMOTE_BASE_URL", "FLUX_REMOTE_MODEL", "FLUX_LLM_TIMEOUT_S"),
              "secret": ("FLUX_REMOTE_API_KEY",),
              "hint": "The endpoint Flux's own model calls go to (a proposer, a critic, an Ask answered by the model): "
                      "any OpenAI-compatible one -- a hosted one, OpenRouter's, a local Ollama's /v1 (D817)."},
    "agent": {"label": "The agent by default", "tab": "Flux", "endpoint": "FLUX_DEFAULT_AGENT", "public": ("FLUX_DEFAULT_AGENT",), "secret": (),
              "hint": "Who writes a problem and answers questions about a loop unless chosen otherwise: an agent's name, or model."},
}
PUBLIC_SETTINGS = tuple(k for g in GROUPS.values() for k in g["public"])
SECRET_SETTINGS = tuple(k for g in GROUPS.values() for k in g["secret"])


#: Names a run's variables never take (D697): the sandbox and the loop's own plumbing, the
#: process's basics, and the model settings (set under Models).
_RESERVED_ENV = re.compile(r"(FLUX_SANDBOX.*|FLUX_CONFIG|FLUX_FEEDBACK_INBOX|FLUX_RUN_LOG|FLUX_TRACE_ROOT|FLUX_SANDBOXED|"
                           r"FLUX_LLM_REMOTE|FLUX_[A-Z0-9_]+_ARGS|FLUX_[A-Z0-9_]+_BIN|FLUX_[A-Z0-9_]+_ENV|FLUX_[A-Z0-9_]+_LOGIN_FILES|"
                           r"FLUX_AGENTS|FLUX_SHARED_VARS|FLUX_AGENT_API_KEY|OPENCODE_CONFIG_CONTENT|PATH|HOME|PWD|USER|SHELL|"
                           r"TMPDIR|TMP|TEMP|LD_.*|PYTHON.*|XDG_.*|NIX_.*)")


#: What an agent's own variables may set besides (D807): its kind's configuration, for that agent alone.
_AGENT_OWN = ("OPENCODE_CONFIG_CONTENT",)


def check_env_name(name: str, agent: bool = False) -> str:
    name = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
        raise ValueError(f"{name!r}: a variable's name is letters, digits and _, not starting with a digit")
    if _RESERVED_ENV.fullmatch(name.upper()) and not (agent and name in _AGENT_OWN):
        raise ValueError(f"{name} is the sandbox's or the loop's own; it cannot be set here")
    if name in PUBLIC_SETTINGS + SECRET_SETTINGS:
        raise ValueError(f"{name} is a model setting: set it under Models")
    return name


#: D734: the kinds of user. Internal users' runs inherit the server's (and the machine's) model,
#: agent and environment settings; external users bring their own, and their agents' logins live
#: in a home folder of their own. An admin is internal. The old "user" reads as internal.
ROLES = ("admin", "internal", "external")


def _role(r: str | None) -> str:
    return "internal" if r in (None, "", "user") else str(r)


@dataclass
class User:
    id: int
    name: str
    role: str
    disabled: bool

    def __post_init__(self) -> None:
        self.role = _role(self.role)

    @property
    def admin(self) -> bool:
        return self.role == "admin"

    @property
    def external(self) -> bool:
        return self.role == "external"


class Store:
    def __init__(self, data: str | Path) -> None:
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data, 0o700)
        self.path = self.data / "flux-web.db"
        with self._db() as db:
            db.executescript(_SCHEMA)
            cols = [r[1] for r in db.execute("PRAGMA table_info(failures)")]
            if "ip" not in cols:                      # D702: failures by name and address
                db.execute("ALTER TABLE failures ADD COLUMN ip TEXT NOT NULL DEFAULT ''")

    def _db(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    # ---- users
    @staticmethod
    def hash_password(password: str, salt: bytes | None = None) -> str:
        salt = salt or secrets.token_bytes(16)
        dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
        return f"scrypt${salt.hex()}${dk.hex()}"

    @staticmethod
    def check_password(password: str, stored: str) -> bool:
        try:
            _kind, salt, want = stored.split("$")
        except ValueError:
            return False
        got = Store.hash_password(password, bytes.fromhex(salt)).split("$")[2]
        return hmac.compare_digest(got, want)

    def add_user(self, name: str, password: str | None, role: str = "internal") -> User:
        """`password` None (D818): the account is unusable until its invitation is used."""
        name = (name or "").strip()
        if self.user(name=name) is not None:              # D699: names are one whatever their case
            raise ValueError(f"user {name!r} exists")
        if not name or not name.replace("-", "").replace("_", "").isalnum() or len(name) > 40:
            raise ValueError("a user name is letters, digits, - and _ (at most 40)")
        if password is not None and len(password) < 6:
            raise ValueError("a password has at least 6 characters")
        role = _role(role)
        if role not in ROLES:
            raise ValueError("a user is admin, internal or external")
        with self._db() as db:
            try:
                cur = db.execute("INSERT INTO users(name, pw, role, created) VALUES (?, ?, ?, ?)",
                                 (name, self.hash_password(password) if password is not None else _UNSET, role, time.time()))
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"user {name!r} exists") from exc
            return User(cur.lastrowid, name, role, False)

    def home_of(self, user: User) -> Path:
        """An external user's own home (D734): what their runs mount and copy from, where their
        agents' logins are written. `<data>/users/<name>/home`, theirs only (0700)."""
        d = self.data / "users" / user.name / "home"
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        return d

    def users(self) -> list[User]:
        with self._db() as db:
            return [User(r["id"], r["name"], r["role"], bool(r["disabled"]))
                    for r in db.execute("SELECT * FROM users ORDER BY name")]

    def user(self, user_id: int | None = None, name: str | None = None) -> User | None:
        with self._db() as db:
            r = (db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) if user_id is not None
                 else db.execute("SELECT * FROM users WHERE name = ? COLLATE NOCASE", ((name or "").strip(),))).fetchone()
        return User(r["id"], r["name"], r["role"], bool(r["disabled"])) if r else None

    def set_user(self, name: str, *, password: str | None = None, disabled: bool | None = None,
                 role: str | None = None) -> None:
        found = self.user(name=name)
        if found is None:
            raise ValueError(f"no user {name!r}")
        name = found.name
        with self._db() as db:
            if password is not None:
                if len(password) < 6:
                    raise ValueError("a password has at least 6 characters")
                db.execute("UPDATE users SET pw = ? WHERE name = ?", (self.hash_password(password), name))
            if disabled is not None:
                db.execute("UPDATE users SET disabled = ? WHERE name = ?", (int(disabled), name))
                if disabled:
                    db.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE name = ?)", (name,))
            if role is not None:
                if _role(role) not in ROLES:
                    raise ValueError("a user is admin, internal or external")
                db.execute("UPDATE users SET role = ? WHERE name = ?", (_role(role), name))

    # ---- invitations and reset links (D818): one-time, kept as a digest, a week long
    def invite(self, name: str) -> tuple[str, str]:
        """(a new link's token, its kind: "invite" for an account whose password is not set yet, else
        "reset"); an earlier link of that user stops working."""
        u = self.user(name=name)
        if u is None:
            raise ValueError(f"no user {name!r}")
        kind = "invite" if self.pending(u.name) else "reset"
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._db() as db:
            db.execute("DELETE FROM invites WHERE user_id = ?", (u.id,))
            db.execute("INSERT INTO invites VALUES (?, ?, ?, ?, ?)", (_digest(token), u.id, kind, now, now + INVITE_DAYS * 86400))
        return token, kind

    def pending(self, name: str) -> bool:
        """Invited, its password not set yet."""
        with self._db() as db:
            r = db.execute("SELECT pw FROM users WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
        return bool(r) and r["pw"] == _UNSET

    def invite_of(self, token: str) -> dict[str, Any] | None:
        """{name, kind, expires} of a link that still opens, else None."""
        with self._db() as db:
            r = db.execute("SELECT u.name, i.kind, i.expires FROM invites i JOIN users u ON u.id = i.user_id "
                           "WHERE i.token = ? AND i.expires > ? AND u.disabled = 0", (_digest(token or ""), time.time())).fetchone()
        return {"name": r["name"], "kind": r["kind"], "expires": r["expires"]} if r else None

    def use_invite(self, token: str, password: str) -> tuple[User, str]:
        """The link's password set, the link spent, every session of the user ended; (the user, a new session's token)."""
        got = self.invite_of(token)
        if got is None:
            raise ValueError("this link has been used or has expired: ask an admin for a new one")
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"a password has at least {MIN_PASSWORD} characters")
        u = self.user(name=got["name"])
        now = time.time()
        session = secrets.token_urlsafe(32)
        with self._db() as db:
            db.execute("UPDATE users SET pw = ? WHERE id = ?", (self.hash_password(password), u.id))
            db.execute("DELETE FROM invites WHERE user_id = ?", (u.id,))
            db.execute("DELETE FROM sessions WHERE user_id = ?", (u.id,))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (_digest(session), u.id, now, now + SESSION_DAYS * 86400))
        return u, session

    # ---- login and sessions
    #: D702: failures in ten minutes that lock a name from one address, and from everywhere
    LOCK_FROM_ONE, LOCK_FROM_ALL = 5, 50

    def login(self, name: str, password: str, address: str = "") -> str | None:
        """A session token, or None. Five failures in ten minutes lock the name from that address
        for that long, fifty from all addresses together lock it everywhere (D702: before, five
        from anyone locked a user out). D699: the name as typed on a phone -- capitalised, a
        space after it -- is the same name; the password is as it is."""
        now = time.time()
        name = (name or "").strip().lower()
        with self._db() as db:
            db.execute("DELETE FROM failures WHERE t < ?", (now - 600,))
            here = db.execute("SELECT COUNT(*) FROM failures WHERE name = ? AND ip = ?", (name, address)).fetchone()[0]
            anywhere = db.execute("SELECT COUNT(*) FROM failures WHERE name = ?", (name,)).fetchone()[0]
            r = db.execute("SELECT * FROM users WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
            ok = (here < self.LOCK_FROM_ONE and anywhere < self.LOCK_FROM_ALL and r is not None and not r["disabled"]
                  and self.check_password(password, r["pw"]))
            if not ok:
                db.execute("INSERT INTO failures(name, t, ip) VALUES (?, ?, ?)", (name, now, address))
                return None
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                       (_digest(token), r["id"], now, now + SESSION_DAYS * 86400))
            return token

    def session_user(self, token: str | None) -> User | None:
        if not token:
            return None
        with self._db() as db:
            r = db.execute("SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                           "WHERE s.token = ? AND s.expires > ? AND u.disabled = 0",
                           (_digest(token), time.time())).fetchone()
        return User(r["id"], r["name"], r["role"], False) if r else None

    def logout(self, token: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token = ?", (_digest(token),))

    # ---- runs
    def add_run(self, user: User, app: str, db_path: str, log: str, argv: list[str], options: dict[str, Any]) -> int:
        import json

        with self._db() as db:
            cur = db.execute("INSERT INTO runs(user_id, app, db, log, argv, started, options) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (user.id, app, db_path, log, json.dumps(argv), time.time(), json.dumps(options)))
            return int(cur.lastrowid)

    def set_run(self, run_id: int, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._db() as db:
            db.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))

    def runs(self, user: User | None = None, app: str | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT r.*, u.name AS user FROM runs r JOIN users u ON u.id = r.user_id", []
        where = []
        if user is not None:
            where.append("r.user_id = ?")
            args.append(user.id)
        if app is not None:
            where.append("r.app = ?")
            args.append(app)
        if where:
            q += " WHERE " + " AND ".join(where)
        with self._db() as db:
            return [dict(r) for r in db.execute(q + " ORDER BY r.id DESC", args)]

    def run(self, run_id: int) -> dict[str, Any] | None:
        with self._db() as db:
            r = db.execute("SELECT r.*, u.name AS user FROM runs r JOIN users u ON u.id = r.user_id WHERE r.id = ?",
                           (run_id,)).fetchone()
        return dict(r) if r else None

    # ---- a user's model settings (D684)
    def _fernet(self):
        from cryptography.fernet import Fernet

        keyfile = self.data / "secret.key"
        if not keyfile.exists():
            keyfile.write_bytes(Fernet.generate_key())
            os.chmod(keyfile, 0o600)
        return Fernet(keyfile.read_bytes())

    def _keys(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """(public, secret): Flux's own settings and every agent's (D807)."""
        from .agents import setting_keys

        a = setting_keys(self)
        return PUBLIC_SETTINGS + a["public"], SECRET_SETTINGS + a["secret"]

    def _checked(self, key: str, value: str | None) -> str | None:
        public, secret = self._keys()
        if key not in public + secret:
            raise ValueError(f"{key} is not a setting; settings: {', '.join(public + secret)}")
        if value is None or not str(value).strip():
            return None
        value = str(value).strip()
        if key.endswith("_BASE_URL") and not value.startswith(("http://", "https://")):
            raise ValueError(f"{key}: an endpoint is an http(s) URL")
        if key == "FLUX_DEFAULT_AGENT":
            from .agents import registry

            if value not in (*registry(self), "model"):
                raise ValueError(f"the agent by default is one of {', '.join([*registry(self), 'model'])}")
        return value

    def set_setting(self, user: User, key: str, value: str | None) -> None:
        value = self._checked(key, value)
        with self._db() as db:
            if value is None:
                db.execute("DELETE FROM settings WHERE user_id = ? AND key = ?", (user.id, key))
                return
            stored = self._fernet().encrypt(value.encode()).decode() if key in self._keys()[1] else value
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?, ?)", (user.id, key, stored))

    def set_server_setting(self, key: str, value: str | None) -> None:
        """The server's model settings (D696): what every run gets unless its user sets their own."""
        value = self._checked(key, value)
        stored = None if value is None else (self._fernet().encrypt(value.encode()).decode() if key in self._keys()[1] else value)
        self.server_set(f"setting:{key}", stored)

    def server_settings(self, reveal: bool = False) -> dict[str, str]:
        with self._db() as db:
            rows = db.execute("SELECT key, value FROM server WHERE key LIKE 'setting:%'").fetchall()
        import json

        out = {}
        public, secret = self._keys()
        for r in rows:
            k, v = r["key"].split(":", 1)[1], json.loads(r["value"])
            if k in secret:
                out[k] = self._fernet().decrypt(v.encode()).decode() if reveal else "set"
            elif k in public:
                out[k] = v
        return out

    def settings(self, user: User, reveal: bool = False) -> dict[str, str]:
        """The user's settings; a secret as "set" unless `reveal` (for their runs only)."""
        with self._db() as db:
            rows = db.execute("SELECT key, value FROM settings WHERE user_id = ?", (user.id,)).fetchall()
        out = {}
        secret = self._keys()[1]
        for r in rows:
            if r["key"] in secret:
                out[r["key"]] = self._fernet().decrypt(r["value"].encode()).decode() if reveal else "set"
            else:
                out[r["key"]] = r["value"]
        return out

    def move_paths(self, moves: dict[str, str]) -> int:
        """Runs whose record or document moved (a migrated loop, D811) follow it; returns how many."""
        if not moves:
            return 0
        n = 0
        with self._db() as db:
            for rid, dbp, argv in db.execute("SELECT id, db, argv FROM runs").fetchall():
                new_db, new_argv = moves.get(dbp, dbp), argv
                for a, b in moves.items():
                    new_argv = new_argv.replace(json.dumps(a), json.dumps(b))
                if (new_db, new_argv) != (dbp, argv):
                    db.execute("UPDATE runs SET db = ?, argv = ? WHERE id = ?", (new_db, new_argv, rid))
                    n += 1
        return n

    def forget_settings(self, keys: tuple[str, ...]) -> None:
        """These settings, the server's and every user's (an agent removed, D807)."""
        with self._db() as db:
            for k in keys:
                db.execute("DELETE FROM settings WHERE key = ?", (k,))
                db.execute("DELETE FROM server WHERE key = ?", (f"setting:{k}",))

    # ---- the server's own settings (D695): starts paused, a user's running limit
    def server_get(self, key: str, default: Any = None) -> Any:
        import json

        with self._db() as db:
            row = db.execute("SELECT value FROM server WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def server_set(self, key: str, value: Any) -> None:
        import json

        with self._db() as db:
            if value is None:
                db.execute("DELETE FROM server WHERE key = ?", (key,))
            else:
                db.execute("INSERT OR REPLACE INTO server VALUES (?, ?)", (key, json.dumps(value)))

    # ---- environment variables of runs (D697): the server's, a user's, a loop's
    def env(self, scope: str, reveal: bool = False) -> dict[str, dict[str, Any]]:
        """{name: {value, secret}} of a scope ("global", "user:<id>", "loop:<user>:<app>"); a
        secret's value is "set" unless `reveal` (for runs only)."""
        got = self.server_get(f"env:{scope}") or {}
        out = {}
        for name, x in got.items():
            val = x["value"]
            if x.get("secret"):
                val = self._fernet().decrypt(val.encode()).decode() if reveal else "set"
            out[name] = {"value": val, "secret": bool(x.get("secret"))}
        return out

    def set_env(self, scope: str, name: str, value: str | None, secret: bool = False) -> None:
        name = check_env_name(name, agent=scope.startswith("agent:"))
        if name in sum(self._keys(), ()):
            raise ValueError(f"{name} is an agent's setting: set it on its tab under Models")
        got = self.server_get(f"env:{scope}") or {}
        if value is None:
            got.pop(name, None)
        else:
            if len(value) > 20000:
                raise ValueError("a value of at most 20000 characters")
            got[name] = {"value": self._fernet().encrypt(value.encode()).decode() if secret else value, "secret": bool(secret)}
        self.server_set(f"env:{scope}", got or None)

    # ---- a loop shared with other users (D701): "watch" sees its runs and outputs, "edit" also
    #      changes and runs it
    def shares(self, owner: str, app: str) -> dict[str, str]:
        return dict(self.server_get(f"share:{owner}:{app}") or {})

    def set_share(self, owner: str, app: str, user: str, perm: str | None) -> dict[str, str]:
        if perm not in (None, "watch", "edit"):
            raise ValueError("a share is watch or edit")
        got = self.shares(owner, app)
        if perm is None:
            got.pop(user, None)
        else:
            got[user] = perm
        self.server_set(f"share:{owner}:{app}", got or None)
        return got

    # ---- notices for a user (D702): told at their next look, then gone
    def notify(self, user: str, text: str, href: str = "", kind: str = "info") -> None:
        got = list(self.server_get(f"notices:{user}") or [])
        got.append({"text": text, "href": href, "kind": kind, "t": time.time()})
        self.server_set(f"notices:{user}", got[-50:])

    def take_notices(self, user: str) -> list[dict[str, Any]]:
        got = list(self.server_get(f"notices:{user}") or [])
        if got:
            self.server_set(f"notices:{user}", None)
        return got

    def shared_with(self, user: str) -> list[tuple[str, str, str]]:
        """(owner, app, permission) of every loop shared with `user`."""
        import json

        with self._db() as db:
            rows = db.execute("SELECT key, value FROM server WHERE key LIKE 'share:%'").fetchall()
        out = []
        for r in rows:
            _k, owner, app = r["key"].split(":", 2)
            perm = json.loads(r["value"]).get(user)
            if perm:
                out.append((owner, app, perm))
        return out

    # ---- audit
    def audit(self, user: str | None, action: str, detail: str = "") -> None:
        with self._db() as db:
            db.execute("INSERT INTO audit(t, user, action, detail) VALUES (?, ?, ?, ?)", (time.time(), user, action, detail))

    @property
    def refusals_file(self) -> Path:
        """Where the sandboxes' proxies write the hosts they refused (D708)."""
        return self.data / "network-refused.jsonl"

    def take_refusals(self) -> int:
        """The hosts refused since the last read, into the audit: who (the loop's owner), "network
        refused", the loop, the host and port, the command. Returns how many."""
        with _REFUSALS:                                 # the sampler and an admin's page: one reader at a time
            return self._take_refusals()

    def _take_refusals(self) -> int:
        import json

        f = self.refusals_file
        try:
            st = f.stat()
        except OSError:
            return 0
        seen = self.server_get("refusals_read") or {}
        offset = seen.get("offset", 0) if seen.get("ino") == st.st_ino and seen.get("offset", 0) <= st.st_size else 0
        with open(f, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
        end = data.rfind(b"\n") + 1                    # a line being written: read next time
        n = 0
        with self._db() as db:
            for raw in data[:end].splitlines():
                try:
                    x = json.loads(raw)
                except ValueError:
                    continue
                owner, _, app = str(x.get("app") or "").rpartition(".")
                where = f"{x.get('host')} (a name lookup" if x.get("how") == "lookup" else f"{x.get('host')}:{x.get('port')} ("
                db.execute("INSERT INTO audit(t, user, action, detail) VALUES (?, ?, ?, ?)",
                           (float(x.get("t") or time.time()), owner or None, "network refused",
                            f"{app or '?'}: {where}{', ' if x.get('how') == 'lookup' else ''}{x.get('command') or 'run'})"))
                n += 1
        self.server_set("refusals_read", {"ino": st.st_ino, "offset": offset + end})
        return n

    def audit_log(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]


_REFUSALS = threading.Lock()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
