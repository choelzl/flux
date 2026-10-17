"""A loop's problem written, or revised, by an agent from the web (D704): `flux ask --no-run` in
the loop's own folder, sandboxed as a run is, with the user's (the owner's) model settings. The
author -- OpenCode, Claude Code, Codex, or Flux's own model -- writes the document and the files
it names and the document is checked; nothing runs. The job is the loop's: one at a time, not
while the loop runs, followed through `runs/author.log` and `runs/author.json`, so it outlives
the server like a run does.

A revision keeps the loop's document name: the author is told to edit it in place, and a
`problem.yaml` it writes instead is moved onto that name.

D1125: the page follows the author's work as short lines (`activity`: what it read, wrote and ran, its check, a
question and its answer), answers a question it asks (`answer`: a line in the inbox beside its journal), and
reviews its decisions (`decisions`: its `decisions.json`, else read from the document). The author's skills are
placed in its folder before it starts (D1123); they, its brief and its decisions never become the loop's files."""

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
#: The author's own, in its folder: its skills (flux_loop.skills.AGENT_DIRS), its brief, its decisions (D1123, D1124)
_THE_AUTHORS = {".claude", ".opencode", ".agents", "AUTHOR-BRIEF.md", "decisions.json", ".flux-steps", ".flux-probes"}
ACTIVITY_KEPT = 400                                     # the lines the page is sent


def _work_directory(app_dir: Path, state: dict[str, Any]) -> Path:
    """Locate new jobs and jobs started before named authoring folders were introduced."""
    folder = state.get("work_directory")
    if isinstance(folder, str) and folder not in ("", ".", "..") and Path(folder).name == folder:
        return app_dir / WORK / folder
    return app_dir / WORK


def _progress(app_dir: Path, work: Path | None = None) -> dict[str, Any] | None:
    """Bounded, confined journal reads: live agent output and the current author/check phase."""
    from .confine import open_read

    root = (work or app_dir / WORK) / "runs" / "author"

    def tail(path, limit):
        try:
            with open_read(path, app_dir) as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - limit))
                return fh.read().decode("utf-8", "replace")
        except (OSError, ValueError):
            return ""

    active, latest, fields = {}, None, {}
    for line in tail(root / "events.jsonl", 512 * 1024).splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("ev") == "start":
            active[str(row.get("id"))] = row
            latest = row
        elif row.get("ev") == "end":
            active.pop(str(row.get("id")), None)
            latest = row
            if isinstance(row.get("output"), dict) and row["output"]:
                fields = row["output"]
    if latest is None:
        return None
    phase = next(reversed(active.values())) if active else latest
    updated = latest.get("t")
    try:
        live = json.loads(tail(root / "live.json", 512 * 1024))
        updates = live.get("updates", {})
        for ident in active:
            if isinstance(updates.get(ident), dict):
                fields = updates[ident]
                updated = live.get("t", updated)
    except (ValueError, AttributeError):
        pass
    allowed = {"elapsed", "agent", "output", "status", "rate limit", "stderr", "tool calls", "last tool output",
               "thinking (live tail)", "thinking", "reply (live tail)", "steps", "steps total", "tokens (reported)"}
    return {"phase": str(phase.get("name") or "author"), "updated": updated,
            "fields": {k: v for k, v in fields.items() if k in allowed}}


def _journal(app_dir: Path, st: dict[str, Any]) -> Path:
    """Where the running author's activity and inbox are: its journal folder in its work copy."""
    return _work_directory(app_dir, st) / "runs" / "author"


def _tail_lines(path: Path, root: Path, limit: int = 256 * 1024) -> list[str]:
    from .confine import open_read

    try:
        with open_read(path, root) as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - limit))
            return fh.read().decode("utf-8", "replace").splitlines()
    except (OSError, ValueError):
        return []


def activity(app_dir: Path, st: dict[str, Any]) -> list[dict[str, Any]]:
    """The author's work as lines (D1125): from its journal while it runs, from the copy kept once it ended."""
    path = _journal(app_dir, st) / "activity.jsonl" if st.get("ended") is None else app_dir / "runs" / "author-activity.jsonl"
    rows = []
    for line in _tail_lines(path, app_dir):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("k"), str) and isinstance(row.get("text"), str):
            rows.append({k: row[k] for k in ("t", "k", "text", "tone", "by", "options", "wait_s") if k in row})
    return rows[-ACTIVITY_KEPT:]


def open_question(rows: list[dict[str, Any]], running: bool) -> dict[str, Any] | None:
    """The author's question still waiting: its last `ask` with no answer after it, while it runs."""
    if not running:
        return None
    for row in reversed(rows):
        if row["k"] == "answer":
            return None
        if row["k"] == "ask":
            if time.time() > float(row.get("t") or 0) + float(row.get("wait_s") or 0):
                return None
            return {"question": row["text"], "options": row.get("options") or [], "asked": row.get("t"), "wait_s": row.get("wait_s")}
    return None


def _now(progress: dict[str, Any] | None, question: dict[str, Any] | None) -> str:
    """What the author is doing this moment, in a few words."""
    if question:
        return "waiting for your answer"
    if not progress:
        return "starting the agent"
    phase = str(progress.get("phase") or "")
    if phase.startswith("check"):
        return "flux task check: the document, its files and tools"
    steps = (progress.get("fields") or {}).get("steps") or []
    last = steps[-1] if steps and isinstance(steps[-1], dict) else {}
    if last.get("k") == "tool" and "out" not in last:
        from flux_loop.author import _what

        k, text = _what(last)
        return {"read": "reading", "write": "writing", "run": "running", "list": "listing", "search": "searching"}.get(k, "using") + f" {text}"
    return "thinking" if last.get("k") == "think" else "writing" if last.get("k") == "text" else "working"


def _problem_files(root: Path) -> list[str]:
    """The loop's own files under `root`, as the author leaves them: not its record, log, skills or brief."""
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if (p.is_file() and not p.is_symlink() and rel.parts[0] not in _NOT_THE_PROBLEMS and rel.parts[0] not in _THE_AUTHORS
                and "__pycache__" not in rel.parts and not p.name.endswith(".part-upload")):
            out.append(rel.as_posix())
        if len(out) >= 300:
            break
    return out


def _hidden_said(root: Path, st: dict[str, Any]) -> list[str]:
    """The paths hidden from AI (D1159) as the job knows them: those given hidden, and those its document says."""
    from .routes_hidden import said

    doc = root / (st.get("revise") or "problem.yaml")
    try:
        text = doc.read_text(errors="replace") if doc.is_file() else ""
    except OSError:
        text = ""
    return list(dict.fromkeys([*(str(p) for p in st.get("hidden") or []), *said(text)]))


def files_of(app_dir: Path, st: dict[str, Any]) -> list[dict[str, Any]]:
    """The new loop's files and who made them (D1125): `yours` the files given to read, `written` what the author
    wrote or changed; a revision's untouched files are left out. A file hidden from AI says so (`hidden`, D1159)."""
    from flux_knowledge.hidden import is_hidden

    yours = set(st.get("yours") or [])
    running = st.get("ended") is None
    root = _work_directory(app_dir, st) if running else app_dir
    pats = [str(root.resolve() / p.strip().rstrip("/")) for p in _hidden_said(root, st) if p.strip()]
    if running:
        rows = []
        for rel in _problem_files(root) if root.is_dir() else []:
            mine = app_dir / rel
            if rel in yours:
                rows.append({"path": rel, "by": "yours"})
            elif not mine.is_file() or mine.read_bytes() != (root / rel).read_bytes():
                rows.append({"path": rel, "by": "written"})
    else:
        rows = ([{"path": p, "by": "written"} for p in st.get("written") or [] if p not in yours]
                + [{"path": p, "by": "yours"} for p in sorted(yours) if (app_dir / p).is_file()])
    for r in rows:
        if pats and is_hidden(root / r["path"], pats):
            r["hidden"] = True
    return rows


def decisions(app_dir: Path, document: str | None) -> dict[str, Any]:
    """The draft's decisions (D1124): the author's `decisions.json` (kept as runs/author-decisions.json), else
    read from the document -- `from` says which."""
    import yaml

    from flux_loop.author import decisions_of_document

    got, why = read_decisions_at(app_dir / "runs" / "author-decisions.json")
    if got:
        return {"decisions": got, "from": "agent", "why": ""}
    doc = None
    if document and (app_dir / document).is_file():
        try:
            doc = yaml.safe_load((app_dir / document).read_text(errors="replace"))
        except yaml.YAMLError:
            doc = None
    return {"decisions": decisions_of_document(doc), "from": "document", "why": why}


def read_decisions_at(path: Path) -> tuple[list[dict[str, str]], str]:
    """flux_loop.author.read_decisions on a file of any name."""
    import tempfile

    from flux_loop.author import DECISIONS, read_decisions

    if not path.is_file() or path.is_symlink():
        return [], f"no {DECISIONS}"
    with tempfile.TemporaryDirectory() as d:
        shutil.copyfile(path, Path(d) / DECISIONS)
        return read_decisions(Path(d))


def first_stage(app_dir: Path, document: str | None) -> str:
    """The cheap pass's stage, in a word: `synth` when the first stage synthesises, else its name."""
    import yaml

    try:
        doc = yaml.safe_load((app_dir / str(document)).read_text(errors="replace")) if document else None
    except (OSError, yaml.YAMLError):
        return ""
    flow = doc.get("flow") if isinstance(doc, dict) and isinstance(doc.get("flow"), dict) else {}
    measure = flow.get("measure")
    if not isinstance(measure, dict) or not measure:
        return ""
    name, stage = next(iter(measure.items()))
    command = stage.get("command") if isinstance(stage, dict) else stage
    return "synth" if "synth" in str(command) else str(name)


def available(env: dict[str, str], agents: dict[str, Any]) -> list[dict[str, Any]]:
    """Each author: the agents the server offers (D807: their program found), and Flux's own
    model, available when one is set."""
    out = [{"id": n, "label": a.label, "available": True, "why": ""} for n, a in agents.items()]
    ok = bool(env.get("FLUX_REMOTE_BASE_URL") or env.get("FLUX_LLM_MODEL") or env.get("OLLAMA_BASE_URL"))
    out.append({"id": "model", "label": AUTHORS["model"], "available": ok, "why": "" if ok else "no model is set (Models)"})
    return out


def _outcome(path: Path) -> dict[str, Any] | None:
    """A small JSON file the author wrote beside its journal (outcome.json, session.json), or None."""
    try:
        if path.is_symlink() or path.stat().st_size > 64 * 1024:
            return None
        got = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


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

    def running(self, app_dir: Path) -> bool:
        """Whether an agent writes the loop's problem now -- the state's `running` alone, cheap enough for every poll
        of a loop's state (its Start waits for it)."""
        try:
            st = json.loads(self.files(app_dir)["state"].read_text())
        except (OSError, ValueError):
            return False
        return bool(st.get("ended") is None and _alive(st.get("pid")))

    def state(self, app_dir: Path, workspace: Any = None, name: str = "") -> dict[str, Any]:
        """The job: running or not, how it ended, the log's last lines."""
        from .confine import open_read

        f = self.files(app_dir)
        try:
            st = json.loads(f["state"].read_text())
        except (OSError, ValueError):
            return {"running": False, "ever": False}
        st["running"] = bool(st.get("ended") is None and _alive(st.get("pid")))
        if st.get("ended") is None and not st["running"] and workspace is not None:
            st = self._finish(app_dir, workspace, name, None)           # it ended while the server was away
        try:
            with open_read(f["log"], app_dir) as fh:
                info = os.fstat(fh.fileno())
                st["log_at"] = info.st_mtime
                fh.seek(max(0, info.st_size - 48 * 1024))
                st["log"] = fh.read().decode("utf-8", "replace").splitlines()[-80:]
        except (OSError, ValueError):
            st["log"] = []
            st["log_at"] = None
        st["ever"] = True
        work = _work_directory(app_dir, st)
        st["progress"] = _progress(app_dir, work) or st.get("progress")
        st["activity"] = activity(app_dir, st)
        st["question"] = open_question(st["activity"], st["running"])
        st["now"] = _now(st["progress"], st["question"]) if st["running"] else ""
        st["files"] = files_of(app_dir, st)
        st["observed"] = time.time()
        st["elapsed_s"] = max(0, (st.get("ended") or st["observed"]) - st.get("started", st["observed"]))
        st["resumable"] = bool(st.pop("session", None))           # D1156: the page needs no session's id, nor the ask again
        st.pop("ask", None)
        return st

    def start(self, *, app_dir: Path, workspace: Any, name: str, prompt: str, author: str, env: dict[str, str],
              attachments: list[Path], revise: str | None, by: str, ask_me: bool = False, hidden: list[str] = ()) -> None:
        """`revise`: the loop's document name, when it has one to revise; `ask_me`: the agent may ask (D1125); `hidden`:
        paths in the loop hidden from its agents and the author, which the document gets (D1159)."""
        with self._lock:
            if not app_dir.is_dir():
                raise ValueError("this loop has moved or been removed; reload it before starting an agent")
            if self.state(app_dir).get("running"):
                raise ValueError("an agent is writing this loop's problem already")
            ask = prompt.strip()
            before = ""
            if revise:
                before = (app_dir / revise).read_text(errors="replace") if (app_dir / revise).is_file() else ""
                ask = (f"REVISE the problem this folder already holds: the document `{revise}` and the files it names. "
                       f"Edit `{revise}` itself, in place; keep everything the instruction does not ask to change.\n\n"
                       f"THE INSTRUCTION:\n{prompt.strip()}\n\nTHE CURRENT `{revise}`:\n```yaml\n{before}\n```")
            for old in ("author-activity.jsonl", "author-decisions.json", "author-outcome.json"):
                (self.files(app_dir)["log"].parent / old).unlink(missing_ok=True)
            self._launch(app_dir, workspace, name, ask=ask, author=author, env=env, attachments=attachments, by=by,
                         ask_me=ask_me, extra=[], header="revision" if revise else "authoring",
                         state={"prompt": prompt.strip()[:4000], "revise": revise, "before": before,
                                "yours": sorted({f"library/{a.name}" for a in attachments}), "hidden": list(hidden)})

    def retry(self, *, app_dir: Path, workspace: Any, name: str, env: dict[str, str], by: str, note: str = "") -> str:
        """D1158: another round of repairs on a draft its author's check refused -- the same agent, its session
        continued where it can be (D1156), the person's `note` told it first. The draft as it was left is the round's
        start; its work so far stays on the page."""
        with self._lock:
            st = self.state(app_dir)
            if st.get("running"):
                raise ValueError("an agent is writing this loop's problem already")
            if not st.get("ever") or st.get("ok"):
                raise ValueError("no refused draft to repair")
            try:                                                     # its session and ask, which the page is not sent
                raw = json.loads(self.files(app_dir)["state"].read_text())
            except (OSError, ValueError):
                raw = {}
            st = {**st, **{k: raw[k] for k in ("session", "ask") if k in raw}}
            logs = self.files(app_dir)["log"].parent
            extra = ["--repair"]
            if st.get("session"):
                extra += ["--session", str(st["session"])]
            if note.strip():
                extra += ["--note", note.strip()]
            ask = st.get("ask") or st.get("prompt") or ""
            self._launch(app_dir, workspace, name, ask=ask, author=str(st.get("author") or "model"), env=env, attachments=[],
                         by=by, ask_me=bool(st.get("ask_me")), extra=extra, header="repair",
                         state={k: st.get(k) for k in ("prompt", "revise", "before", "yours", "session", "hidden")}
                         | {"rounds": int(st.get("rounds") or 1) + 1},
                         keep=[(logs / "author-activity.jsonl", Path("runs/author/activity.jsonl"))])
        return "the agent repairs the draft" + (" as told" if note.strip() else "")

    def _launch(self, app_dir: Path, workspace: Any, name: str, *, ask: str, author: str, env: dict[str, str],
                attachments: list[Path], by: str, ask_me: bool, extra: list[str], header: str, state: dict[str, Any],
                keep: list[tuple[Path, Path]] = ()) -> None:
        """`flux ask --no-run` on a copy of the loop's own files (the only writable folder in the sandbox: the record
        (out/), the log (runs/) and the workbench stay out of its reach), followed by a thread till it ends. `keep`:
        files of the log copied into the copy first (a repair's earlier work lines)."""
        f = self.files(app_dir)
        f["log"].parent.mkdir(exist_ok=True)
        root = app_dir / WORK
        shutil.rmtree(root, ignore_errors=True)
        work = root / app_dir.name  # preserve the loop ID on the host and in /sandbox/<id>
        work.mkdir(parents=True)
        for p in app_dir.iterdir():
            if p.name in _NOT_THE_PROBLEMS:
                continue
            if p.is_dir() and not p.is_symlink():
                shutil.copytree(p, work / p.name, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", ".git"))
            elif p.is_file():
                shutil.copy2(p, work / p.name)
        from flux_loop.author import DECISIONS, install_author_skills

        kept = f["log"].parent / "author-decisions.json"
        if (state.get("revise") or "--repair" in extra) and kept.is_file() and not kept.is_symlink():   # D1124: kept current
            shutil.copyfile(kept, work / DECISIONS)
        for src, rel in keep:
            if src.is_file() and not src.is_symlink():
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, work / rel)
        skills = install_author_skills(work)                          # D1123: placed here, seen in the sandbox
        flux = shutil.which("flux") or sys.argv[0]
        argv = [flux, "ask", ask, "--dir", str(work), "--no-run", "--author", author, *extra]
        if ask_me:
            argv += ["--questions", "operator"]
        for a in attachments:
            argv += ["--file", str(a)]
        for h in state.get("hidden") or []:
            argv += ["--hidden", str(h)]
        with open(f["log"], "a") as fh:
            fh.write(f"\n── {header} {time.strftime('%Y-%m-%d %H:%M:%S')} by {by} · {AUTHORS.get(author, author)} ──\n")
        log = open(f["log"], "ab")
        proc = subprocess.Popen(argv, cwd=str(work), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env={**env, "FLUX_ASK_RUN_HINT": "Start, on the loop's page"}, start_new_session=True)   # not the copy, removed after
        log.close()
        f["state"].write_text(json.dumps({**state, "pid": proc.pid, "started": time.time(), "ended": None, "author": author,
                                          "ask": ask, "by": by, "work_directory": work.name, "ask_me": bool(ask_me),
                                          "skills": skills}))
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
            return {"running": False, "ever": False}   # reset may have removed a completed job
        if st.get("ended") is not None:                 # done already, by the other
            st["running"] = False
            return st
        work = _work_directory(app_dir, st)
        st["progress"] = _progress(app_dir, work) or st.get("progress")  # keep the last evidence after work is removed
        written: list[str] = []
        if work.is_dir():
            doc = document_path(work)
            revise = st.get("revise")
            if revise and doc.is_file() and doc.name != revise:
                (work / revise).write_text(doc.read_text())          # the loop keeps its document's name
                doc.unlink(missing_ok=True)
            from flux_loop.author import DECISIONS

            journal = work / "runs" / "author"
            for src, kept in ((work / DECISIONS, "author-decisions.json"), (journal / "activity.jsonl", "author-activity.jsonl"),
                              (journal / "outcome.json", "author-outcome.json")):
                if src.is_file() and not src.is_symlink():          # D1124, D1125, D1158: the author's, kept in the log
                    shutil.copyfile(src, f["log"].parent / kept)
            outcome = _outcome(f["log"].parent / "author-outcome.json")
            if outcome:
                st["error"] = str(outcome.get("error") or "")[:6000]          # D1158: the refusal its repairs left
                if outcome.get("session"):
                    st["session"] = str(outcome["session"])                   # D1156: continued by Try again
            elif (journal / "session.json").is_file():
                st["session"] = str((_outcome(journal / "session.json") or {}).get("session") or "") or st.get("session")
            for rel in _problem_files(work):                         # the agent's files back into the loop
                p, target = work / rel, app_dir / rel
                if target.is_file() and target.read_bytes() == p.read_bytes():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.unlink(missing_ok=True)                       # a linked file is replaced, not written through
                shutil.copy2(p, target)
                written.append(rel)
            shutil.rmtree(app_dir / WORK, ignore_errors=True)
        doc = document_path(app_dir)
        revise = st.get("revise")
        if revise and (app_dir / revise).is_file():
            doc = app_dir / revise
        ok = rc in (0, None) and doc.is_file()                     # None: it ended while the server was away
        if doc.is_file():
            workspace.set_meta(name, document=doc.name, id=name)      # D786: the loop's name is the id
        shutil.rmtree(app_dir / ATTACHED, ignore_errors=True)
        st.update(ended=time.time(), rc=rc, ok=ok, document=doc.name if doc.is_file() else None, written=written or st.get("written") or [],
                  after=doc.read_text(errors="replace") if doc.is_file() else "")
        f["state"].write_text(json.dumps(st))
        st["running"] = False
        return st

    def answer(self, app_dir: Path, by: str, text: str) -> str:
        """The answer to the author's open question (D1125): a line in the inbox beside its journal, read as a run's
        notes are (flux_feedback.InboxChannel)."""
        import secrets

        from .confine import append

        st = self.state(app_dir)
        if not st.get("running"):
            raise ValueError("no agent is writing")
        if not st.get("question"):
            raise ValueError("the agent asks nothing now")
        inbox = _journal(app_dir, st) / "inbox.jsonl"
        append(inbox, json.dumps({"id": secrets.token_hex(6), "text": text.strip(), "by": by, "t": time.time()}) + "\n", app_dir)
        return "answered: the agent carries on"

    def made(self, app_dir: Path) -> None:
        """D1122: the draft made a loop -- its authoring is history, no longer shown on the loop's Overview."""
        f = self.files(app_dir)
        try:
            st = json.loads(f["state"].read_text())
        except (OSError, ValueError):
            return
        st["made"] = time.time()
        f["state"].write_text(json.dumps(st))

    def stop(self, app_dir: Path) -> str:
        st = self.state(app_dir)
        if not st.get("running"):
            return "no agent is writing"
        try:
            os.killpg(int(st["pid"]), signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            pass
        return "stopping the agent"
