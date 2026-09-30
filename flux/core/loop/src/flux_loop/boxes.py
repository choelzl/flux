"""A box of the drawing answered by a coding agent (D640): `flow: {critique: {agent: claude}}`.

The loop writes the box's question as `BRIEF.md` in a work directory of its own, the agent writes
its answer as `out.json`, and the loop checks the answer against the box's schema and rules. A
refused answer is sent back once with the reason; a second refusal, a missing agent or a timeout
falls back to the box's rules half. Every turn is an `agent_turn` row on the record.

`session: turn` (the default) is a fresh agent in its own directory every turn; `session: pass`
(D669) is one agent per box for the pass, in `agents/<box>/pass/`: its first turn reads the full
brief, later turns resume it with the new question and a new `out-NNN.json`.

The agent decides; it never measures: the gate and the stages stay the loop's (D460).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

__all__ = ["AgentLessons", "DELEGABLE", "NEVER", "agent_of", "box_turn", "flush_turns", "record_turn"]

#: The boxes an agent may answer, and the ones that establish facts and never are.
DELEGABLE = frozenset({"validate", "orchestrate", "plan", "dse", "generate", "critique", "extract", "select"})
NEVER = frozenset({"test", "calibrate"})

_TYPES = {"boolean": bool, "string": str, "array": list, "object": dict, "integer": int, "number": (int, float)}


def agent_of(flow: dict[str, Any], box: str) -> Any | None:
    """The agent spec a document names for `box`, or None."""
    v = flow.get(box)
    return v["agent"] if isinstance(v, dict) and "agent" in v else None


def _refusal(doc: Any, schema: dict[str, Any], check: Callable[[dict], str | None] | None) -> str | None:
    """Why this answer is refused, or None: not an object, a required key missing, a value of the
    wrong type, or the box's own rule."""
    if not isinstance(doc, dict):
        return "out.json is not a JSON object"
    missing = [k for k in schema.get("required", ()) if k not in doc]
    if missing:
        return f"out.json lacks {', '.join(missing)}"
    for k, prop in (schema.get("properties") or {}).items():
        want = _TYPES.get(str(prop.get("type")))
        if k in doc and want is not None and not isinstance(doc[k], want):
            return f"out.json's {k} is not a {prop['type']}"
    return check(doc) if check is not None else None


def _read(out: Path) -> Any:
    try:
        return json.loads(out.read_text())
    except (OSError, ValueError):
        return None


def flush_turns(state: Any) -> None:
    """The agent_turn rows drafted on worker threads, written by the loop's thread."""
    rec = getattr(state, "records", None)
    pending = getattr(state, "pending_turns", None)
    while pending:
        row = pending.pop(0)
        if rec is not None:
            rec.remember("agent_turn", row)


def record_turn(state: Any, row: dict[str, Any]) -> None:
    """An agent turn on the record (D640, D669): written now on the loop's thread, queued for
    it from a worker (the record's connection belongs to the loop's thread)."""
    import threading

    owner = getattr(state, "loop_thread", None)
    if owner is not None and owner != threading.get_ident():
        state.pending_turns.append(row)
        return
    flush_turns(state)
    rec = getattr(state, "records", None)
    if rec is not None:
        rec.remember("agent_turn", row)


def _pass_session(state: Any, box: str, tool: str, root: Path) -> Any:
    """The box's `session: pass` session (D669), made on its first turn: one directory for the
    pass (the first free `pass`, `pass-2`, ... when the scratch directory is shared)."""
    from .types import AgentSession

    sessions = state.__dict__.setdefault("agent_sessions", {})
    sess = sessions.get(box)
    if sess is None or sess.tool != tool:
        n, workdir = 1, root / "pass"
        while workdir.exists():
            n += 1
            workdir = root / f"pass-{n}"
        workdir.mkdir(parents=True)
        sess = sessions[box] = AgentSession(tool, workdir)
    return sess


def box_turn(box: str, spec: Any, question: str, schema: dict[str, Any], state: Any,
             check: Callable[[dict], str | None] | None = None, home: str = "",
             problem: Any = None) -> dict[str, Any] | None:
    """The agent's answer to `question` for `box`, checked; None when it fell back (said, and
    on the record). Given the `problem`, the brief carries its LIBRARY section (D648)."""
    from .agent import DECIDE, agent_spec, converse, library_section, run_turn, workbench_link, workbench_section

    a = agent_spec(spec)
    # before the pass has its trace directory (validate runs first), the scratch directory
    root = Path(state.workdir or Path(tempfile.gettempdir()) / "flux-agents").resolve() / "agents" / box
    root.mkdir(parents=True, exist_ok=True)
    sess = _pass_session(state, box, a.tool, root) if a.session == "pass" else None
    if sess is not None:
        sess.turns += 1
        workdir = sess.workdir
        out = workdir / f"out-{sess.turns:03d}.json"
    else:
        n = 1 + max((int(d.name) for d in root.iterdir() if d.name.isdigit()), default=0)   # every turn its own directory
        workdir = root / f"{n:03d}"
        workdir.mkdir()
        out = workdir / "out.json"
    library = library_section(problem, question, state) if problem is not None else ""
    bench = str(getattr(getattr(problem, "task", None), "workbench", "") or state.__dict__.get("workbench") or "")  # D677
    workbench_link(bench, workdir)
    shelf = workbench_section(bench)
    brief = (f"{question.strip()}\n\n" + (f"{library}\n\n" if library else "") + f"HOW TO ANSWER. You are a coding agent answering the `{box}` box of a "
             f"design-space exploration loop. "
             + (f"THE PROBLEM'S FILES are in `{home}` (its document, scripts and golden model): read those"
                + (" and the LIBRARY files above" if library else "") + (" and your workbench" if shelf else "")
                + " and nothing else; what is being judged is quoted above. " if home else
                "Everything you need is quoted above. ")
             + f"Do not run "
             f"the gate or the measurement stages: the loop runs them. Write your answer to `{out}` as ONE JSON "
             f"object matching this schema, then reply with one line saying so:\n"
             f"{json.dumps(schema, indent=1)}\n" + (f"\n{shelf}\n" if shelf else ""))
    prompt_file = workdir / (f"BRIEF-{sess.turns:03d}.md" if sess is not None else "BRIEF.md")
    subs = {"prompt": brief, "prompt_file": str(prompt_file), "artifact": str(out), "workdir": str(workdir),
            "part": box, "name": box, "python": sys.executable, "workbench": bench}
    resume = sess.id if sess is not None and a.resume else None
    shape = json.dumps(schema, indent=1)
    message = ""
    if resume:
        # the session holds the brief: the new question, and where this turn's answer goes
        message = (f"THE NEXT QUESTION for the `{box}` box (the same rules as before):\n\n{question.strip()}\n\n"
                   f"Write your answer to `{out}` (a new file) as ONE JSON object matching "
                   + ("the same schema as before" if shape == sess.schema else f"this schema:\n{shape}\n")
                   + ", then reply with one line saying so.\n")
    prompt_file.write_text(message or brief)
    t0 = time.monotonic()
    turn, _asked = converse(a, subs, workdir=workdir, artifact=out, answer=lambda _q: (DECIDE, "decide"),
                            say=state.say, prompt_file=prompt_file, session=resume, message=message)
    if sess is not None:
        sess.id = turn.session or sess.id
        if turn.began == "fresh":
            sess.schema = shape                   # a fresh session read the full brief
    began, session_id = turn.began, turn.session or ""
    doc = _read(out)
    why = _refusal(doc, schema, check) if turn.ok or out.is_file() else (turn.stderr.strip()[-300:] or f"the agent exited {turn.rc}")
    if why and (turn.ok or out.is_file()):
        # one more try, in the same session when the agent can resume
        again = f"Your {out.name} was refused: {why}. Rewrite {out} so it follows the schema and the rules."
        out.unlink(missing_ok=True)
        if a.resume and turn.session:
            turn = run_turn(a, a.resume, {**subs, "session": turn.session, "answer": again}, workdir=workdir)
        else:
            turn = run_turn(a, a.argv, {**subs, "prompt": brief + "\n" + again}, workdir=workdir)
        doc = _read(out)
        why = _refusal(doc, schema, check)
    seconds = round(time.monotonic() - t0, 1)
    record_turn(state, {"box": box, "agent": a.tool, "ok": why is None, "why": why or "", "seconds": seconds,
                        "answer": doc if why is None else None, "session": began, "session_id": session_id,
                        "message_chars": len(message or brief)})
    said = f"{began} session" + (f" {session_id}" if session_id else "")
    if why:
        state.say(f"  {box}: agent {a.tool} fell back to the rules half ({why[:160]}; {said})")
        return None
    state.say(f"  {box}: agent {a.tool} answered in {seconds:g}s ({said}, {len(message or brief)} chars sent)")
    return doc


class AgentLessons:
    """`flow: {extract: {agent: ...}}` (D640): once a pass, a coding agent reads this campaign's
    measured rows and writes lessons, each citing the rows (by record seq) it rests on; a lesson
    citing a row that does not exist is refused. The lessons join the prompts as knowledge."""

    key = "lessons"
    title = "Lessons an agent drew from this campaign's record (each cites its rows)"
    static = False
    shown = 60

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    def render(self, state: Any) -> str:
        if "_agent_lessons" in state.__dict__:
            return state.__dict__["_agent_lessons"]
        state.__dict__["_agent_lessons"] = ""               # once a pass, even when it falls back
        rec = getattr(state, "records", None)
        if rec is None or getattr(rec, "store", None) is None:
            return ""
        rows = {}
        for t in rec.store.trials(rec.campaign_id):
            if t.result is None:
                continue
            values = {m: t.result.value_of(m) for m in t.result.metrics}
            rows[t.seq] = f"row {t.seq}: {t.candidate.get('name', '?')} on {t.stage or t.phase}: " + ", ".join(
                f"{m}={v:g}" for m, v in values.items() if isinstance(v, (int, float)))
        if len(rows) < 2:
            return ""
        shown = list(rows.items())[-self.shown:]
        question = ("THE MEASURED ROWS of this campaign (the newest last):\n" + "\n".join(r for _s, r in shown)
                    + "\n\nWrite at most five lessons a designer should know before the next draft: what the numbers "
                      "say works, what does not, and where the trade-off sits. Each lesson cites the rows it rests on; "
                      "a lesson no row supports is not a lesson.")
        schema = {"type": "object", "required": ["lessons"],
                  "properties": {"lessons": {"type": "array", "items": {"type": "object", "properties": {
                      "text": {"type": "string"}, "rows": {"type": "array", "items": {"type": "integer"}}}}}}}

        def cited(d: dict) -> str | None:
            for i, les in enumerate(d.get("lessons") or []):
                if not isinstance(les, dict) or not str(les.get("text") or "").strip():
                    return f"lesson {i + 1} has no text"
                bad = [r for r in les.get("rows") or [] if r not in rows]
                if not les.get("rows") or bad:
                    return f"lesson {i + 1} cites " + (f"rows {bad} that do not exist" if bad else "no row")
            return None

        doc = box_turn("extract", self.agent, question, schema, state, check=cited)
        text = "\n".join(f"- {les['text'].strip()} (rows {', '.join(map(str, les['rows']))})"
                         for les in (doc or {}).get("lessons") or [])
        state.__dict__["_agent_lessons"] = text
        return text
