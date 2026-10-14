"""A question about a loop, answered by an agent that reads it and changes nothing (D705):
`flux consult "why does it stall at 2 GHz?" --loop <folder> --out <answer folder> --author opencode`.

The agent works in the answer folder. It reads the loop's folder -- its document, its files,
its log, its answer -- and a snapshot of its record (a copy of the SQLite file, so it may query
it as it likes), and writes its answer as Markdown to `answer.md`. Flux's own model answers too,
from what this module gives it inline: the document, a summary of the record, the log's end --
and the record itself through its `sql` tool (D1074), read-only.
Sandboxed, the loop's folder is mounted read-only and only the answer folder is writable."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

__all__ = ["consult", "record_tool", "snapshot", "summary"]

ANSWER = "answer.md"
AGENT = "agent.json"                 # D993: the agent's turn as Live shows one (thinking, commands, replies)


class _Watch:
    """The agent's turn, written to `AGENT` as it goes (at most once a second, as the turn reports)
    and whole at its end: the fields Live's agent view reads."""

    def __init__(self, path: Path) -> None:
        self.path, self.fields = path, {}

    def phase_start(self, name, why, params):
        return name

    def phase_update(self, token, name, output):
        self.save(output)

    def phase_end(self, token, name, seconds, failed, output):
        self.save({**(output or {}), "seconds": round(seconds, 1)})

    def save(self, output: Any, **more: Any) -> None:
        if isinstance(output, dict):
            self.fields.update({k: v for k, v in output.items() if isinstance(v, (str, int, float, bool, list))})
        self.fields.update(more)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.fields))
        tmp.replace(self.path)


def snapshot(loop: Path, out: Path) -> Path | None:
    """The loop's record copied, whole and consistent (SQLite's backup), into `out/record.db`; it
    opens the original read-only and immutable, so a read-only mount does it no harm."""
    dbs = sorted((loop / "out").glob("*.db"), key=lambda p: p.stat().st_mtime)
    if not dbs:
        return None
    dest = out / "record.db"
    dest.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{dbs[-1]}?mode=ro&immutable=1", uri=True)
    try:
        dst = sqlite3.connect(dest)
        src.backup(dst)
        dst.close()
    finally:
        src.close()
    return dest


def _tail(p: Path, lines: int) -> str:
    try:
        return "\n".join(p.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def summary(record: Path | None) -> str:
    """What the record says, in a few lines: the objective, the passes and the last one's
    conclusion, the measurements, the newest notes."""
    if record is None:
        return "The loop has no record yet: it never ran."
    try:
        from .report import load

        rep = load(str(record))
    except Exception as exc:  # noqa: BLE001 -- a record the summary cannot read: the agent can still query it
        return f"(the record could not be summarised: {type(exc).__name__}: {exc}; query it directly)"
    out = [f"Objective: {rep.objectives.describe()}", f"Passes: {len(rep.passes)}; measurements: {len(rep.rows)}"]
    if rep.passes:
        when, conclusion = rep.passes[-1]
        out.append("The last pass concluded:\n" + json.dumps(conclusion, indent=1, default=str)[:4000])
    best: dict[str, Any] = {}
    for o in rep.objectives:
        vals = [(r.metrics.get(o.metric), r) for r in rep.rows if isinstance(r.metrics.get(o.metric), (int, float))]
        if vals:
            v, r = (min if o.direction == "minimize" else max)(vals, key=lambda x: x[0])
            best[o.metric] = f"{v} ({r.name}, at {r.stage})"
    if best:
        out.append("Best measured: " + "; ".join(f"{k} {v}" for k, v in best.items()))
    if rep.notes:
        out.append("Notes on record: " + " | ".join(str(n)[:200] for n in rep.notes[-5:]))
    return "\n".join(out)


def _listing(loop: Path) -> str:
    out = []
    for p in sorted(loop.iterdir()):
        if p.name.startswith(".") and p.name != ".gitignore":
            continue
        if p.is_dir():
            n = sum(1 for q in p.rglob("*") if q.is_file())
            out.append(f"  {p.name}/  ({n} file(s))")
        else:
            out.append(f"  {p.name}  ({p.stat().st_size} bytes)")
    return "\n".join(out)


SQL_ROWS = 200                       # a query's rows shown; the rest counted
SQL_CELL = 2000                      # a value's characters shown (a design's text is long)
SQL_SECONDS = 10.0


def record_tool(record: Path) -> Any:
    """D1074: the record for a model that runs nothing itself -- one SELECT (or PRAGMA, `.tables`,
    `.schema [table]`) at a time on the snapshot, opened read-only; nothing written, nothing attached,
    a query stopped after `SQL_SECONDS`; rows and long values cut, said so."""
    import time

    from flux_llm.tools import Tool

    def allow(action: int, *_args: Any) -> int:
        ok = (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_PRAGMA,
              getattr(sqlite3, "SQLITE_RECURSIVE", -1))
        return sqlite3.SQLITE_OK if action in ok else sqlite3.SQLITE_DENY

    def run(args: dict[str, Any]) -> str:
        q = str(args.get("query") or "").strip().rstrip(";").strip()
        if not q:
            return "error: give a query"
        dot = q.split()
        if dot[0] == ".tables":
            q = "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        elif dot[0] == ".schema":
            q = ("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"
                 + (f" AND tbl_name = '{dot[1].replace(chr(39), '')}'" if len(dot) > 1 else "") + " ORDER BY name")
        con = sqlite3.connect(f"file:{record}?mode=ro", uri=True, timeout=5)
        try:
            con.set_authorizer(allow)
            end = time.monotonic() + SQL_SECONDS
            con.set_progress_handler(lambda: int(time.monotonic() > end), 10000)
            cur = con.execute(q)
            cols = [d[0] for d in cur.description or ()]
            rows = cur.fetchmany(SQL_ROWS + 1)
            more = len(rows) > SQL_ROWS
            rows = rows[:SQL_ROWS]
        except sqlite3.Error as exc:
            return f"error: {exc}" + (" (stopped: over the time a query may take)" if "interrupted" in str(exc) else "")
        finally:
            con.close()

        def cell(v: Any) -> str:
            t = "NULL" if v is None else (f"<{len(v)} bytes>" if isinstance(v, bytes) else str(v))
            return t if len(t) <= SQL_CELL else t[:SQL_CELL] + f"... ({len(t)} characters; substr() reads the rest)"

        lines = [" | ".join(cols)] + [" | ".join(cell(v) for v in r) for r in rows]
        if more:
            lines.append(f"(more than {SQL_ROWS} rows: narrow it, LIMIT/OFFSET or count(*))")
        return "\n".join(lines) if rows else " | ".join(cols) + "\n(no rows)"

    return Tool("sql", "Query the loop's record (SQLite, read-only): one SELECT, PRAGMA, `.tables` or `.schema [table]`. "
                "trials: one row a candidate measured at a stage (candidate_json, its design's text in artifacts.text by "
                "trials.artifact_key, result_id -> results.result_json with the numbers); campaign_events: decisions, "
                "conclusions and notes (kind, detail_json). json_extract() reads the JSON.",
                {"type": "object", "properties": {"query": {"type": "string", "description": "the SQL"}}, "required": ["query"]},
                run)


def brief(question: str, loop: Path, out: Path, record: Path | None, *, inline: bool) -> str:
    """What the answerer reads: its role, the loop, where things are, the summary, the question."""
    doc = ""
    try:
        meta = json.loads((loop / ".flux-app.json").read_text())
        doc = meta.get("document") or ""
    except (OSError, ValueError):
        pass
    if not doc:
        doc = "problem.yaml" if (loop / "problem.yaml").is_file() else ""
    parts = [
        "YOU ANSWER A QUESTION ABOUT A DESIGN LOOP. You read; you change nothing in the loop. A Flux loop "
        "generates candidate designs, checks each with its gate, measures the survivors at its stages and decides "
        "by its objectives, pass after pass; its record holds every candidate, measurement and decision.",
        f"THE LOOP'S FOLDER (read only): {loop}\n{_listing(loop)}",
        f"  The problem document: {loop / doc}" if doc else "  (no problem document)",
        f"  Its log: {loop / 'runs' / 'loop.log'}; its last answer: {loop / 'runs' / 'answer.json'}",
        (("THE RECORD: query it with your `sql` tool -- `.tables`, `.schema trials`, then SELECTs." if inline else
          f"THE RECORD: a copy at {record}, yours to query -- sqlite3 {record} \".tables\", then SELECTs "
          "(python3's sqlite3 works too).") if record else "THE RECORD: none yet."),
        "WHAT THE RECORD SAYS, IN SHORT:\n" + summary(record),
    ]
    if inline:                                     # a model reads nothing itself: the document and the log's end, here
        if doc:
            parts.append(f"THE DOCUMENT `{doc}`:\n```yaml\n{(loop / doc).read_text(errors='replace')[:20000]}\n```")
        parts.append("THE LOG'S END:\n```\n" + _tail(loop / "runs" / "loop.log", 120) + "\n```")
        parts.append("Answer in Markdown: plainly, with the numbers that support it.")
    else:
        parts.append(f"Answer in Markdown, plainly, with the numbers and lines that support it, in the file "
                     f"`{out / ANSWER}` (your working directory); then end your turn with one line saying so. Read the "
                     "files and query the record as you need; do not run the design tools, and do not write in the loop's folder.")
    conversation = out / "conversation.json"
    if conversation.is_file():
        history = json.loads(conversation.read_text())
        parts.append("PREVIOUS CONVERSATION (context for the follow-up below):\n" + json.dumps(history, ensure_ascii=False, indent=2))
    parts.append(f"THE QUESTION:\n{question.strip()}")
    return "\n\n".join(parts) + "\n"


def consult(question: str, loop: Path, out: Path, author: Any = "model", proposer: Any = None,
            say: Callable[[str], None] = print) -> dict[str, Any]:
    """The answer: {"ok", "answer", "by"}; written to `out/answer.md` as well."""
    out.mkdir(parents=True, exist_ok=True)
    record = snapshot(loop, out)
    inline = author in (None, "", "model")
    text = brief(question, loop, out, record, inline=inline)
    (out / "BRIEF.md").write_text(text)
    if inline:
        if proposer is None:
            raise ValueError("no model to answer: give one, or an agent")
        from flux_llm.tools import ToolBudget

        try:
            reply = proposer.propose(text, tools=[record_tool(record)] if record else None,
                                     budget=ToolBudget(hops=12, result_chars=20000))
        except RuntimeError as exc:
            if not record:
                raise
            # D1086: a model that cannot work with the tool (an empty reply) answers as it did before D1074,
            # from what the brief gives it inline
            say(f"the model could not answer with the record tool ({str(exc)[:160]}); asking without it")
            reply = proposer.propose(text)
        answer, ok, by = reply.text.strip(), bool(reply.text.strip()), "the model"
    else:
        from .agent import agent_spec, run_turn

        spec = agent_spec(author)
        subs = {"prompt": text, "prompt_file": str(out / "BRIEF.md"), "artifact": str(out / ANSWER), "workdir": str(out),
                "part": "answer", "name": "answer", "python": sys.executable, "home": str(loop)}
        from flux_profile import add_listener, remove_listener

        watch = _Watch(out / AGENT)
        add_listener(watch)
        try:
            turn = run_turn(spec, spec.argv, subs, workdir=out)
        finally:
            remove_listener(watch)
        watch.save(None, steps=turn.steps or [], **{"steps total": len(turn.steps or []), "status": "done" if turn.ok else "failed"})
        (out / "agent.out").write_text((turn.stdout or "")[-200000:])           # what it printed: kept to read a failure
        written = (out / ANSWER).read_text(errors="replace").strip() if (out / ANSWER).is_file() else ""
        answer = written or (turn.text or "").strip()
        ok, by = turn.ok and bool(answer), f"{spec.tool}" + (f" ({turn.about})" if turn.about else "")
        if not answer:
            said = [ln for ln in (turn.stdout or "").splitlines() if '"error"' in ln][-1:] or [(turn.stderr or "")[-400:]]
            answer = f"(the agent {spec.tool} answered nothing: exit {turn.rc}; {said[0][-600:]})"
    (out / ANSWER).write_text(answer + "\n")
    say(f"answered by {by}")
    return {"ok": ok, "answer": answer, "by": by}
