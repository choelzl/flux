"""A question about a loop, answered by an agent that reads it and changes nothing (D705):
`flux consult "why does it stall at 2 GHz?" --loop <folder> --out <answer folder> --author opencode`.

The agent works in the answer folder. It reads the loop's folder -- its document, its files,
its log, its answer -- and a snapshot of its record (a copy of the SQLite file, so it may query
it as it likes), and writes its answer as Markdown to `answer.md`. Flux's own model answers too,
from what this module gives it inline: the document, a summary of the record, the log's end.
Sandboxed, the loop's folder is mounted read-only and only the answer folder is writable."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

__all__ = ["consult", "snapshot", "summary"]

ANSWER = "answer.md"


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
        (f"THE RECORD: a copy at {record}, yours to query -- sqlite3 {record} \".tables\", then SELECTs "
         "(python3's sqlite3 works too)." if record else "THE RECORD: none yet."),
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
        reply = proposer.propose(text)
        answer, ok, by = reply.text.strip(), bool(reply.text.strip()), "the model"
    else:
        from .agent import agent_spec, run_turn

        spec = agent_spec(author)
        subs = {"prompt": text, "prompt_file": str(out / "BRIEF.md"), "artifact": str(out / ANSWER), "workdir": str(out),
                "part": "answer", "name": "answer", "python": sys.executable, "home": str(loop)}
        turn = run_turn(spec, spec.argv, subs, workdir=out)
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
