"""Questions about a loop, answered by an agent from the web (D705): each one `flux consult` in
the sandbox -- the loop read-only, the question's folder `runs/asks/<id>/` writable -- with the
owner's model settings and network. Kept with the loop: the question, who asked, who answered,
the answer (`answer.md`), the log. One at a time per loop."""

from __future__ import annotations

import json
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

__all__ = ["Asks"]


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _loop(d: Path) -> str:
    return str(d.parent.parent.parent)                  # <loop>/runs/asks/<id>


def _text(d: Path, name: str) -> str:
    """D852: an ask's folder is its agent's to write -- read without following a link out of the loop."""
    from .confine import open_read

    with open_read(d / name, _loop(d)) as fh:
        return fh.read().decode("utf-8", "replace")


def _write(d: Path, name: str, text: str) -> None:
    from .confine import replace

    replace(d / name, text, _loop(d))


class Asks:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    @staticmethod
    def root(app_dir: Path) -> Path:
        return app_dir / "runs" / "asks"

    def _read(self, d: Path) -> dict[str, Any]:
        try:
            st = json.loads(_text(d, "ask.json"))
        except (OSError, ValueError):
            return {}
        st["id"] = d.name
        st["running"] = st.get("ended") is None and _alive(st.get("pid"))
        if st.get("ended") is None and not st["running"]:
            st = self._finish(d, None)
        if (d / "answer.md").is_file() and not st["running"]:
            try:
                st["answer"] = _text(d, "answer.md")
            except (OSError, ValueError):
                st["answer"] = ""
        try:
            st["log"] = _text(d, "ask.log").splitlines()[-40:]
        except (OSError, ValueError):
            st["log"] = []
        return st

    def list(self, app_dir: Path) -> list[dict[str, Any]]:
        r = self.root(app_dir)
        if not r.is_dir():
            return []
        return [x for x in (self._read(d) for d in sorted(r.iterdir(), reverse=True) if d.is_dir()) if x]

    def running(self, app_dir: Path) -> bool:
        return any(a.get("running") for a in self.list(app_dir))

    def start(self, *, app_dir: Path, question: str, author: str, env: dict[str, str], by: str,
              parent_id: str | None = None) -> str:
        with self._lock:
            if not app_dir.is_dir():
                raise ValueError("this loop has moved or been removed; reload it before asking")
            if self.running(app_dir):
                raise ValueError("an agent is answering a question about this loop already")
            history, thread_id = [], None
            if parent_id is not None:
                if not re.fullmatch(r"\d{8}-\d{6}(-\d+)?", parent_id):
                    raise ValueError("no such answer to reply to")
                parent_dir = self.root(app_dir) / parent_id
                parent = self._read(parent_dir) if parent_dir.is_dir() else {}
                if not parent.get("answer") or parent.get("running"):
                    raise ValueError("reply to a completed answer in this loop")
                if (parent_dir / "conversation.json").exists():
                    history = json.loads(_text(parent_dir, "conversation.json"))
                    if not isinstance(history, list):
                        raise ValueError("the conversation could not be read")
                history.append({k: parent.get(k, "") for k in ("question", "answer", "author", "by")})
                thread_id = parent.get("thread_id") or parent_id
            ident = time.strftime("%Y%m%d-%H%M%S")
            d = self.root(app_dir) / ident
            n = 1
            while d.exists():
                n += 1
                d = self.root(app_dir) / f"{ident}-{n}"
            d.mkdir(parents=True)
            if history:
                # Snapshot the selected branch, rather than including unrelated questions or later replies.
                _write(d, "conversation.json", json.dumps(history))
            flux = shutil.which("flux") or sys.argv[0]
            argv = [flux, "consult", question, "--loop", str(app_dir), "--out", str(d), "--author", author]
            log = open(d / "ask.log", "ab")
            proc = subprocess.Popen(argv, cwd=str(d), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=env, start_new_session=True)
            log.close()
            _write(d, "ask.json", json.dumps({"question": question.strip(), "author": author, "by": by,
                                              "parent_id": parent_id, "thread_id": thread_id or d.name,
                                              "pid": proc.pid, "started": time.time(), "ended": None}))
        threading.Thread(target=lambda: self._finish(d, proc.wait()), daemon=True).start()
        return d.name

    def _finish(self, d: Path, rc: int | None) -> dict[str, Any]:
        with self._lock if rc is not None else _Nothing():
            try:
                st = json.loads(_text(d, "ask.json"))
            except (OSError, ValueError):
                return {}                              # a reset/forget may precede the waiting thread
            if st.get("ended") is None:
                st.update(ended=time.time(), rc=rc, ok=(d / "answer.md").is_file() and rc in (0, None))
                _write(d, "ask.json", json.dumps(st))
            (d / "record.db").unlink(missing_ok=True)        # the snapshot the agent read: not kept
            st["id"], st["running"] = d.name, False
            return st

    def stop(self, app_dir: Path, ident: str) -> str:
        d = self.root(app_dir) / ident
        st = self._read(d) if d.is_dir() else {}
        if not st.get("running"):
            return "not answering"
        try:
            os.killpg(int(st["pid"]), signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            pass
        return "stopping the agent"

    def forget(self, app_dir: Path, ident: str) -> None:
        """Remove the conversation containing this turn, including its saved context."""
        with self._lock:
            d = self.root(app_dir) / ident
            if not d.is_dir() or d.parent != self.root(app_dir):
                raise ValueError("no such question")
            chosen = self._read(d)
            thread = chosen.get("thread_id") or ident
            turns = [a for a in self.list(app_dir) if (a.get("thread_id") or a["id"]) == thread]
            if any(a.get("running") for a in turns):
                raise ValueError("it is being answered: stop it first")
            for turn in turns:
                shutil.rmtree(self.root(app_dir) / turn["id"])


class _Nothing:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_a: Any) -> None:
        return None
