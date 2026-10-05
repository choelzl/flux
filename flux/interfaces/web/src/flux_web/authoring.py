"""A loop's problem written, or revised, by an agent from the web (D704): `flux ask --no-run` in
the loop's own folder, sandboxed as a run is, with the user's (the owner's) model settings. The
author -- OpenCode, Claude Code, Codex, or Flux's own model -- writes the document and the files
it names and the document is checked; nothing runs. The job is the loop's: one at a time, not
while the loop runs, followed through `runs/author.log` and `runs/author.json`, so it outlives
the server like a run does.

A revision keeps the loop's document name: the author is told to edit it in place, and a
`problem.yaml` it writes instead is moved onto that name."""

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

__all__ = ["AUTHORS", "Authoring", "available"]

#: The built-in agents' and Flux's own model's names; the server's agents are its own (D807).
AUTHORS = {"opencode": "OpenCode", "claude": "Claude Code", "codex": "Codex", "model": "Flux's own model"}
ATTACHED = ".attachments"
WORK = ".author-work"                                   # the agent's copy of the loop's own files (D704)
_NOT_THE_PROBLEMS = {"out", "runs", "workbench", ATTACHED, WORK, ".git", "__pycache__", ".flux-app.json"}


def available(env: dict[str, str], agents: dict[str, Any]) -> list[dict[str, Any]]:
    """Each author: the agents the server offers (D807: their program found), and Flux's own
    model, available when one is set."""
    out = [{"id": n, "label": a.label, "available": True, "why": ""} for n, a in agents.items()]
    ok = bool(env.get("FLUX_REMOTE_BASE_URL") or env.get("FLUX_LLM_MODEL") or env.get("OLLAMA_BASE_URL"))
    out.append({"id": "model", "label": AUTHORS["model"], "available": ok, "why": "" if ok else "no model is set (Models)"})
    return out


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


class Authoring:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._finishing = threading.Lock()              # the waiting thread and a status read may both see it end

    @staticmethod
    def files(app_dir: Path) -> dict[str, Path]:
        d = app_dir / "runs"
        return {"log": d / "author.log", "state": d / "author.json"}

    def state(self, app_dir: Path, workspace: Any = None, name: str = "") -> dict[str, Any]:
        """The job: running or not, how it ended, the log's last lines."""
        f = self.files(app_dir)
        try:
            st = json.loads(f["state"].read_text())
        except (OSError, ValueError):
            return {"running": False, "ever": False}
        st["running"] = bool(st.get("ended") is None and _alive(st.get("pid")))
        if st.get("ended") is None and not st["running"] and workspace is not None:
            st = self._finish(app_dir, workspace, name, None)           # it ended while the server was away
        try:
            with open(f["log"], "rb") as fh:
                fh.seek(max(0, os.path.getsize(f["log"]) - 48 * 1024))
                st["log"] = fh.read().decode("utf-8", "replace").splitlines()[-80:]
        except OSError:
            st["log"] = []
        st["ever"] = True
        return st

    def start(self, *, app_dir: Path, workspace: Any, name: str, prompt: str, author: str, env: dict[str, str],
              attachments: list[Path], revise: str | None, by: str) -> None:
        """`revise`: the loop's document name, when it has one to revise."""
        with self._lock:
            if self.state(app_dir).get("running"):
                raise ValueError("an agent is writing this loop's problem already")
            f = self.files(app_dir)
            f["log"].parent.mkdir(exist_ok=True)
            ask = prompt.strip()
            before = ""
            if revise:
                before = (app_dir / revise).read_text(errors="replace") if (app_dir / revise).is_file() else ""
                ask = (f"REVISE the problem this folder already holds: the document `{revise}` and the files it names. "
                       f"Edit `{revise}` itself, in place; keep everything the instruction does not ask to change.\n\n"
                       f"THE INSTRUCTION:\n{prompt.strip()}\n\nTHE CURRENT `{revise}`:\n```yaml\n{before}\n```")
            # the agent works on a copy of the loop's own files: only that copy is writable in the
            # sandbox -- the record (out/), the log (runs/) and the workbench stay out of its reach
            work = app_dir / WORK
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir()
            for p in app_dir.iterdir():
                if p.name in _NOT_THE_PROBLEMS:
                    continue
                if p.is_dir() and not p.is_symlink():
                    shutil.copytree(p, work / p.name, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", ".git"))
                elif p.is_file():
                    shutil.copy2(p, work / p.name)
            flux = shutil.which("flux") or sys.argv[0]
            argv = [flux, "ask", ask, "--dir", str(work), "--no-run", "--author", author]
            for a in attachments:
                argv += ["--file", str(a)]
            with open(f["log"], "a") as fh:
                fh.write(f"\n── {'revision' if revise else 'authoring'} {time.strftime('%Y-%m-%d %H:%M:%S')} by {by} · "
                         f"{AUTHORS.get(author, author)} ──\n")
            log = open(f["log"], "ab")
            proc = subprocess.Popen(argv, cwd=str(work), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    env=env, start_new_session=True)
            log.close()
            f["state"].write_text(json.dumps({"pid": proc.pid, "started": time.time(), "ended": None, "author": author,
                                              "prompt": prompt.strip()[:4000], "by": by, "revise": revise, "before": before}))
        threading.Thread(target=self._wait, args=(proc, app_dir, workspace, name), daemon=True).start()

    def _wait(self, proc: subprocess.Popen, app_dir: Path, workspace: Any, name: str) -> None:
        self._finish(app_dir, workspace, name, proc.wait())

    def _finish(self, app_dir: Path, workspace: Any, name: str, rc: int | None) -> dict[str, Any]:
        """The document the author left, made the loop's: a revision keeps the loop's name for it."""
        with self._finishing:
            return self._finish_once(app_dir, workspace, name, rc)

    def _finish_once(self, app_dir: Path, workspace: Any, name: str, rc: int | None) -> dict[str, Any]:
        from flux_loop.author import document_path

        f = self.files(app_dir)
        try:
            st = json.loads(f["state"].read_text())
        except (OSError, ValueError):
            st = {}
        if st.get("ended") is not None:                 # done already, by the other
            st["running"] = False
            return st
        work = app_dir / WORK
        if work.is_dir():
            doc = document_path(work)
            revise = st.get("revise")
            if revise and doc.is_file() and doc.name != revise:
                (work / revise).write_text(doc.read_text())          # the loop keeps its document's name
                doc.unlink(missing_ok=True)
            for p in sorted(work.rglob("*")):                        # the agent's files back into the loop
                rel = p.relative_to(work)
                if not p.is_file() or rel.parts[0] in _NOT_THE_PROBLEMS or p.name.endswith(".part-upload"):
                    continue
                target = app_dir / rel
                if target.is_file() and target.read_bytes() == p.read_bytes():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.unlink(missing_ok=True)                       # a linked file is replaced, not written through
                shutil.copy2(p, target)
            shutil.rmtree(work, ignore_errors=True)
        doc = document_path(app_dir)
        revise = st.get("revise")
        if revise and (app_dir / revise).is_file():
            doc = app_dir / revise
        ok = rc in (0, None) and doc.is_file()                     # None: it ended while the server was away
        if doc.is_file():
            workspace.set_meta(name, document=doc.name, id=name)      # D786: the loop's name is the id
        shutil.rmtree(app_dir / ATTACHED, ignore_errors=True)
        st.update(ended=time.time(), rc=rc, ok=ok, document=doc.name if doc.is_file() else None,
                  after=doc.read_text(errors="replace") if doc.is_file() else "")
        f["state"].write_text(json.dumps(st))
        st["running"] = False
        return st

    def stop(self, app_dir: Path) -> str:
        st = self.state(app_dir)
        if not st.get("running"):
            return "no agent is writing"
        try:
            os.killpg(int(st["pid"]), signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            pass
        return "stopping the agent"
