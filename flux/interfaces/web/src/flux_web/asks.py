"""Questions about a loop, answered by an agent from the web (D705): each one `flux consult` in
the sandbox -- the loop read-only, the question's folder `runs/asks/<id>/` writable -- with the
owner's model settings and network. Kept with the loop: the question, who asked, who answered,
the answer (`answer.md`), the log. One at a time per loop."""

from __future__ import annotations

import json
import os
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


class Asks:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    @staticmethod
    def root(app_dir: Path) -> Path:
        return app_dir / "runs" / "asks"

    def _read(self, d: Path) -> dict[str, Any]:
        try:
            st = json.loads((d / "ask.json").read_text())
        except (OSError, ValueError):
            return {}
        st["id"] = d.name
        st["running"] = st.get("ended") is None and _alive(st.get("pid"))
        if st.get("ended") is None and not st["running"]:
            st = self._finish(d, None)
        if (d / "answer.md").is_file() and not st["running"]:
            st["answer"] = (d / "answer.md").read_text(errors="replace")
        try:
            st["log"] = (d / "ask.log").read_text(errors="replace").splitlines()[-40:]
        except OSError:
            st["log"] = []
        return st

    def list(self, app_dir: Path) -> list[dict[str, Any]]:
        r = self.root(app_dir)
        if not r.is_dir():
            return []
        return [x for x in (self._read(d) for d in sorted(r.iterdir(), reverse=True) if d.is_dir()) if x]

    def running(self, app_dir: Path) -> bool:
        return any(a.get("running") for a in self.list(app_dir))

    def start(self, *, app_dir: Path, question: str, author: str, env: dict[str, str], by: str) -> str:
        with self._lock:
            if self.running(app_dir):
                raise ValueError("an agent is answering a question about this loop already")
            ident = time.strftime("%Y%m%d-%H%M%S")
            d = self.root(app_dir) / ident
            n = 1
            while d.exists():
                n += 1
                d = self.root(app_dir) / f"{ident}-{n}"
            d.mkdir(parents=True)
            flux = shutil.which("flux") or sys.argv[0]
            argv = [flux, "consult", question, "--loop", str(app_dir), "--out", str(d), "--author", author]
            log = open(d / "ask.log", "ab")
            proc = subprocess.Popen(argv, cwd=str(d), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=env, start_new_session=True)
            log.close()
            (d / "ask.json").write_text(json.dumps({"question": question.strip()[:8000], "author": author, "by": by,
                                                    "pid": proc.pid, "started": time.time(), "ended": None}))
        threading.Thread(target=lambda: self._finish(d, proc.wait()), daemon=True).start()
        return d.name

    def _finish(self, d: Path, rc: int | None) -> dict[str, Any]:
        with self._lock if rc is not None else _Nothing():
            try:
                st = json.loads((d / "ask.json").read_text())
            except (OSError, ValueError):
                st = {}
            if st.get("ended") is None:
                st.update(ended=time.time(), rc=rc, ok=(d / "answer.md").is_file() and rc in (0, None))
                (d / "ask.json").write_text(json.dumps(st))
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
        d = self.root(app_dir) / ident
        if not d.is_dir() or d.parent != self.root(app_dir):
            raise ValueError("no such question")
        if self._read(d).get("running"):
            raise ValueError("it is being answered: stop it first")
        shutil.rmtree(d)


class _Nothing:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_a: Any) -> None:
        return None
