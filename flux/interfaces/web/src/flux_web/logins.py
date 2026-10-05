"""A user's agent logins from the web (D734; every user's since D744/D747): `flux login --home <their home> -- <the
agent's login command>`, sandboxed as a run is (their network rules, the admin's programs). `flux
login` gives the agent a terminal of its own, so a login made for a person -- a menu, a prompt, a
link to open -- works as in a terminal; the server drives it through plain pipes. What it writes
stays in their home, where their runs use it. One session per user at a time, ended after 15
minutes; the page reads its output from an offset and types into it."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

__all__ = ["Logins", "logged_in"]

#: Each kind's login command, where it keeps its login and what it prints instead are the kind's
#: (`agents.KINDS`, D807); an agent's own command is the admin's. D748: a login that prints its
#: secret instead of keeping it -- `claude setup-token` prints a year-long token -- is taken from
#: the output into the user's own settings (encrypted, the agent's `FLUX_<NAME>_OAUTH_TOKEN`), and
#: never shown again: the transcript has it masked.
KEYS = {"enter": "\r", "up": "\x1b[A", "down": "\x1b[B", "left": "\x1b[D", "right": "\x1b[C", "tab": "\t", "escape": "\x1b",
        "ctrl-c": "\x03", "backspace": "\x7f", "space": " "}
_RIGHT = re.compile(r"\x1b\[(\d*)C")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]|\r(?!\n)")
LIMIT_S = 15 * 60


def logged_in(home: Path, agents: dict[str, Any]) -> dict[str, bool]:
    """Each agent logged in: its kind's login file in `home`, or one the admin names for it (D760)."""
    from .agents import KINDS

    return {n: any((home / p).is_file() and (home / p).stat().st_size > 0 for p in [*KINDS[a.kind]["credentials"], *a.login_files])
            for n, a in agents.items()}


class _Session:
    def __init__(self, agent: str, proc: subprocess.Popen, fd: int) -> None:
        self.agent, self.proc, self.fd = agent, proc, fd
        self.started, self.ended, self.rc = time.time(), None, None
        self.out: list[str] = []
        self.size = 0
        self.pending = ""                       # D748: a line that may still be a secret being printed
        self.on_secret: Any = None
        self.on_end: Any = None                 # D768: told the exit once the login ends
        self.printed: Any = None                # D748: (pattern, setting) of a secret it prints
        self.lock = threading.Lock()

    def text(self) -> str:
        with self.lock:
            return "".join(self.out)


class Logins:
    def __init__(self) -> None:
        self._by: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def start(self, user: str, agent: str, home: Path, cmd: list[str], env: dict[str, str], on_secret: Any = None,
              on_end: Any = None, printed: Any = None) -> None:
        with self._lock:
            old = self._by.get(user)
            if old and old.ended is None:
                raise ValueError("a login is going on already: finish it, or stop it")
            home.mkdir(parents=True, exist_ok=True)
            flux = shutil.which("flux", path=env.get("PATH")) or None
            argv = ([flux] if flux else [sys.executable, "-m", "flux_cli"]) + ["login", "--home", str(home), "--", *cmd]
            # plain pipes: `flux login` gives the agent its terminal (D734), inside the sandbox
            proc = subprocess.Popen(argv, cwd=str(home), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    env={**env, "TERM": "xterm-256color"}, start_new_session=True, close_fds=True)
            sess = self._by[user] = _Session(agent, proc, proc.stdout.fileno())
            sess.on_secret = on_secret
            sess.on_end = on_end
            sess.printed = printed
        threading.Thread(target=self._read, args=(sess,), daemon=True).start()
        threading.Thread(target=self._limit, args=(sess,), daemon=True).start()

    def _read(self, sess: _Session) -> None:
        while True:
            try:
                data = os.read(sess.fd, 4096)
            except OSError:
                break
            if not data:
                break
            text = data.decode("utf-8", "replace")
            # D748: a cursor moved right is the spaces it skipped (Claude Code writes its words so)
            text = _RIGHT.sub(lambda m: " " * int(m.group(1) or 1), text)
            text = _ANSI.sub("", text)
            text = self._kept(sess, text)
            if not text:
                continue
            with sess.lock:
                sess.out.append(text)
                sess.size += len(text)
                if sess.size > 400_000:                          # a long session: its end kept
                    keep = "".join(sess.out)[-200_000:]
                    sess.out, sess.size = [keep], len(keep)
        rest = self._kept(sess, "", end=True)
        if rest:
            with sess.lock:
                sess.out.append(rest)
                sess.size += len(rest)
        sess.rc = sess.proc.wait()
        sess.ended = time.time()
        if sess.on_end is not None:
            try:
                sess.on_end(sess.rc)
            except Exception:  # noqa: BLE001 -- the login has ended either way
                pass

    @staticmethod
    def _kept(sess: _Session, text: str, end: bool = False) -> str:
        """What the transcript shows of `text` (D748): a secret the agent printed, saved through
        `on_secret` and masked; a line that may still be one held back until it ends."""
        found = sess.printed
        if found is None:
            return text
        text = sess.pending + text
        sess.pending = ""
        cut = text.rfind("\n") + 1
        if not end and "sk-" in text[cut:]:
            text, sess.pending = text[:cut], text[cut:]
        pattern, name = found
        for secret in set(pattern.findall(text)):
            if sess.on_secret is not None:
                try:
                    sess.on_secret(name, secret)
                    said = f"(saved to your settings as {name}; not shown)"
                except Exception:  # noqa: BLE001 -- not saved: still never shown
                    said = "(a token was printed here; it could not be saved)"
            else:
                said = "(a token; not shown)"
            text = text.replace(secret, said)
        return text

    def _limit(self, sess: _Session) -> None:
        while sess.ended is None and time.time() - sess.started < LIMIT_S:
            time.sleep(2)
        if sess.ended is None:
            self._kill(sess)

    @staticmethod
    def _kill(sess: _Session) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):      # D768: the last one is not asked
            try:
                os.killpg(sess.proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            for _ in range(20):
                if sess.proc.poll() is not None:
                    return
                time.sleep(0.1)

    def state(self, user: str, since: int = 0) -> dict[str, Any]:
        sess = self._by.get(user)
        if sess is None:
            return {"running": False}
        text = sess.text()
        return {"running": sess.ended is None, "agent": sess.agent, "rc": sess.rc, "started": sess.started,
                "text": text[since:] if since <= len(text) else text, "offset": len(text)}

    def send(self, user: str, text: str | None = None, key: str | None = None) -> None:
        sess = self._by.get(user)
        if sess is None or sess.ended is not None:
            raise ValueError("no login is going on")
        if key and key not in KEYS:
            raise ValueError(f"a key is one of {', '.join(KEYS)}")
        try:
            sess.proc.stdin.write((str(text or "") + (KEYS[key] if key else "")).encode())
            sess.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise ValueError("the login has ended") from exc

    def stop(self, user: str) -> None:
        sess = self._by.get(user)
        if sess is not None and sess.ended is None:
            self._kill(sess)
