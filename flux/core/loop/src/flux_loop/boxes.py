"""A box of the drawing answered by a coding agent (D640): `flow: {critique: {agent: claude}}`.

The loop writes the box's question as `BRIEF.md` in a work directory of its own, the agent writes
its answer as `out.json`, and the loop checks the answer against the box's schema and rules. A
refused answer is sent back once with the reason; a second refusal, a missing agent or a timeout
falls back to the box's rules half. Every turn is an `agent_turn` row on the record.

The agent decides; it never measures: the gate and the stages stay the loop's (D460).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

__all__ = ["AgentLessons", "DELEGABLE", "NEVER", "agent_of", "box_turn"]

#: The boxes an agent may answer, and the ones that establish facts and never are.
DELEGABLE = frozenset({"validate", "orchestrate", "plan", "dse", "generate", "critique", "extract", "select"})
NEVER = frozenset({"test", "analytical", "simulation", "calibrate", "records"})

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


def box_turn(box: str, spec: Any, question: str, schema: dict[str, Any], state: Any,
             check: Callable[[dict], str | None] | None = None) -> dict[str, Any] | None:
    """The agent's answer to `question` for `box`, checked; None when it fell back (said, and
    on the record)."""
    from .agent import DECIDE, agent_spec, converse, run_turn

    a = agent_spec(spec)
    # before the pass has its trace directory (validate runs first), the scratch directory
    root = Path(state.workdir or Path(tempfile.gettempdir()) / "flux-agents").resolve() / "agents" / box
    root.mkdir(parents=True, exist_ok=True)
    n = 1 + max((int(d.name) for d in root.iterdir() if d.name.isdigit()), default=0)   # every turn its own directory
    workdir = root / f"{n:03d}"
    workdir.mkdir()
    out = workdir / "out.json"
    brief = (f"{question.strip()}\n\nHOW TO ANSWER. You are a coding agent answering the `{box}` box of a "
             f"design-space exploration loop. Read anything in this directory or the repository you need. Do not run "
             f"the gate or the measurement stages: the loop runs them. Write your answer to `{out}` as ONE JSON "
             f"object matching this schema, then reply with one line saying so:\n"
             f"{json.dumps(schema, indent=1)}\n")
    prompt_file = workdir / "BRIEF.md"
    prompt_file.write_text(brief)
    subs = {"prompt": brief, "prompt_file": str(prompt_file), "artifact": str(out), "workdir": str(workdir),
            "part": box, "name": box, "python": sys.executable}
    t0 = time.monotonic()
    turn, _asked = converse(a, subs, workdir=workdir, artifact=out, answer=lambda _q: (DECIDE, "decide"),
                            say=state.say, prompt_file=prompt_file)
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
    rec = getattr(state, "records", None)
    if rec is not None:
        rec.remember("agent_turn", {"box": box, "agent": a.tool, "ok": why is None, "why": why or "",
                                    "seconds": seconds, "answer": doc if why is None else None})
    if why:
        state.say(f"  {box}: agent {a.tool} fell back to the rules half ({why[:160]})")
        return None
    state.say(f"  {box}: agent {a.tool} answered in {seconds:g}s")
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
