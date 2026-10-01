"""The server's own database (D683): users, sessions, runs, and an audit trail. SQLite beside
the users' data; every function opens its own connection (FastAPI serves from threads)."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
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
"""

#: The model settings of runs (D684, D696), by what uses them: Flux's own model calls, and each
#: coding agent. The admin sets them for the server; a user's own override theirs. Keys are
#: secret: stored encrypted, never sent back, only handed to runs. `endpoint`: a user who names
#: their own gets none of the server's values of that group.
GROUPS: dict[str, dict[str, Any]] = {
    "model": {"label": "Flux's own model (OpenAI-compatible)", "endpoint": "FLUX_REMOTE_BASE_URL",
              "public": ("FLUX_REMOTE_BASE_URL", "FLUX_REMOTE_MODEL", "FLUX_LLM_TIMEOUT_S", "FLUX_LLM_MODEL", "OLLAMA_BASE_URL"),
              "secret": ("FLUX_REMOTE_API_KEY", "OPENROUTER_API_KEY")},
    "opencode": {"label": "OpenCode", "endpoint": "FLUX_OPENCODE_BASE_URL",
                 "public": ("FLUX_OPENCODE_BASE_URL", "FLUX_OPENCODE_MODEL"), "secret": ("FLUX_OPENCODE_API_KEY",),
                 "hint": "Empty: Flux's own model's endpoint, model and key; with neither, OpenCode's own configuration."},
    "claude": {"label": "Claude Code", "endpoint": "ANTHROPIC_BASE_URL",
               "public": ("ANTHROPIC_BASE_URL", "FLUX_CLAUDE_MODEL"), "secret": ("ANTHROPIC_API_KEY",),
               "hint": "Empty: Claude Code's own login and model."},
    "codex": {"label": "Codex", "endpoint": "OPENAI_BASE_URL",
              "public": ("OPENAI_BASE_URL", "FLUX_CODEX_MODEL"), "secret": ("OPENAI_API_KEY",),
              "hint": "Empty: Codex's own login and model."},
}
PUBLIC_SETTINGS = tuple(k for g in GROUPS.values() for k in g["public"])
SECRET_SETTINGS = tuple(k for g in GROUPS.values() for k in g["secret"])


#: Names a run's variables never take (D697): the sandbox and the loop's own plumbing, the
#: process's basics, and the model settings (set under Models).
_RESERVED_ENV = re.compile(r"(FLUX_SANDBOX.*|FLUX_CONFIG|FLUX_FEEDBACK_INBOX|FLUX_RUN_LOG|FLUX_TRACE_ROOT|FLUX_SANDBOXED|"
                           r"FLUX_LLM_REMOTE|FLUX_[A-Z]+_ARGS|FLUX_[A-Z]+_BIN|OPENCODE_CONFIG_CONTENT|PATH|HOME|PWD|USER|SHELL|"
                           r"TMPDIR|TMP|TEMP|LD_.*|PYTHON.*|XDG_.*|NIX_.*)")


def check_env_name(name: str) -> str:
    name = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
        raise ValueError(f"{name!r}: a variable's name is letters, digits and _, not starting with a digit")
    if _RESERVED_ENV.fullmatch(name.upper()):
        raise ValueError(f"{name} is the sandbox's or the loop's own; it cannot be set here")
    if name in PUBLIC_SETTINGS + SECRET_SETTINGS:
        raise ValueError(f"{name} is a model setting: set it under Models")
    return name


@dataclass
class User:
    id: int
    name: str
    role: str
    disabled: bool

    @property
    def admin(self) -> bool:
        return self.role == "admin"


class Store:
    def __init__(self, data: str | Path) -> None:
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data, 0o700)
        self.path = self.data / "flux-web.db"
        with self._db() as db:
            db.executescript(_SCHEMA)

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

    def add_user(self, name: str, password: str, role: str = "user") -> User:
        if not name or not name.replace("-", "").replace("_", "").isalnum() or len(name) > 40:
            raise ValueError("a user name is letters, digits, - and _ (at most 40)")
        if len(password) < 6:
            raise ValueError("a password has at least 6 characters")
        if role not in ("user", "admin"):
            raise ValueError("role is user or admin")
        with self._db() as db:
            try:
                cur = db.execute("INSERT INTO users(name, pw, role, created) VALUES (?, ?, ?, ?)",
                                 (name, self.hash_password(password), role, time.time()))
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"user {name!r} exists") from exc
            return User(cur.lastrowid, name, role, False)

    def users(self) -> list[User]:
        with self._db() as db:
            return [User(r["id"], r["name"], r["role"], bool(r["disabled"]))
                    for r in db.execute("SELECT * FROM users ORDER BY name")]

    def user(self, user_id: int | None = None, name: str | None = None) -> User | None:
        with self._db() as db:
            r = (db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) if user_id is not None
                 else db.execute("SELECT * FROM users WHERE name = ?", (name,))).fetchone()
        return User(r["id"], r["name"], r["role"], bool(r["disabled"])) if r else None

    def set_user(self, name: str, *, password: str | None = None, disabled: bool | None = None,
                 role: str | None = None) -> None:
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
                db.execute("UPDATE users SET role = ? WHERE name = ?", (role, name))

    # ---- login and sessions
    def login(self, name: str, password: str) -> str | None:
        """A session token, or None; five failures in ten minutes lock the name for that long."""
        now = time.time()
        with self._db() as db:
            db.execute("DELETE FROM failures WHERE t < ?", (now - 600,))
            failed = db.execute("SELECT COUNT(*) FROM failures WHERE name = ?", (name,)).fetchone()[0]
            r = db.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchone()
            ok = (failed < 5 and r is not None and not r["disabled"] and self.check_password(password, r["pw"]))
            if not ok:
                db.execute("INSERT INTO failures(name, t) VALUES (?, ?)", (name, now))
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

    @staticmethod
    def _checked(key: str, value: str | None) -> str | None:
        if key not in PUBLIC_SETTINGS + SECRET_SETTINGS:
            raise ValueError(f"{key} is not a setting; settings: {', '.join(PUBLIC_SETTINGS + SECRET_SETTINGS)}")
        if value is None or not str(value).strip():
            return None
        value = str(value).strip()
        if key.endswith("_BASE_URL") and not value.startswith(("http://", "https://")):
            raise ValueError(f"{key}: an endpoint is an http(s) URL")
        return value

    def set_setting(self, user: User, key: str, value: str | None) -> None:
        value = self._checked(key, value)
        with self._db() as db:
            if value is None:
                db.execute("DELETE FROM settings WHERE user_id = ? AND key = ?", (user.id, key))
                return
            stored = self._fernet().encrypt(value.encode()).decode() if key in SECRET_SETTINGS else value
            db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?, ?)", (user.id, key, stored))

    def set_server_setting(self, key: str, value: str | None) -> None:
        """The server's model settings (D696): what every run gets unless its user sets their own."""
        value = self._checked(key, value)
        stored = None if value is None else (self._fernet().encrypt(value.encode()).decode() if key in SECRET_SETTINGS else value)
        self.server_set(f"setting:{key}", stored)

    def server_settings(self, reveal: bool = False) -> dict[str, str]:
        with self._db() as db:
            rows = db.execute("SELECT key, value FROM server WHERE key LIKE 'setting:%'").fetchall()
        import json

        out = {}
        for r in rows:
            k, v = r["key"].split(":", 1)[1], json.loads(r["value"])
            if k in SECRET_SETTINGS:
                out[k] = self._fernet().decrypt(v.encode()).decode() if reveal else "set"
            elif k in PUBLIC_SETTINGS:
                out[k] = v
        return out

    def settings(self, user: User, reveal: bool = False) -> dict[str, str]:
        """The user's settings; a secret as "set" unless `reveal` (for their runs only)."""
        with self._db() as db:
            rows = db.execute("SELECT key, value FROM settings WHERE user_id = ?", (user.id,)).fetchall()
        out = {}
        for r in rows:
            if r["key"] in SECRET_SETTINGS:
                out[r["key"]] = self._fernet().decrypt(r["value"].encode()).decode() if reveal else "set"
            else:
                out[r["key"]] = r["value"]
        return out

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
        name = check_env_name(name)
        got = self.server_get(f"env:{scope}") or {}
        if value is None:
            got.pop(name, None)
        else:
            if len(value) > 20000:
                raise ValueError("a value of at most 20000 characters")
            got[name] = {"value": self._fernet().encrypt(value.encode()).decode() if secret else value, "secret": bool(secret)}
        self.server_set(f"env:{scope}", got or None)

    # ---- audit
    def audit(self, user: str | None, action: str, detail: str = "") -> None:
        with self._db() as db:
            db.execute("INSERT INTO audit(t, user, action, detail) VALUES (?, ?, ?, ?)", (time.time(), user, action, detail))

    def audit_log(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
