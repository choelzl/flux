"""The tools a model turn may call (D505): what the loop can offer any problem, built from the
problem's own hooks -- a computation, the problem's own check, the part's history, a look-up in
the knowledge -- so the model measures inside its turn instead of guessing and submitting.

WHY. Before D505 the model's only instrument was `"compute": [...]` in its reply, run AFTER the
turn (D-compute), one hop per turn: a numeric check cost a whole turn of thinking, and D501
counted 211 unmeasurable and 583 wrong designs in a day that the model could have checked
itself first. With tools the check happens where the model is: it writes, calls `check`, reads
"512 over: the sign is flipped for negative inputs", edits, checks again, and SUBMITS what it
has already measured. The loop's own arbitration stays: every check inside a turn is a measured
attempt for the record and the gradient (`Checked`), the best of the turn is what the stage
judges, and the tolerance rule of D504 decides what happens next.

Opt-in: `LoopRequest.tools` (the demo's `--agent tools`). Off, nothing here is called.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from flux_llm import Tool, ToolBudget

from .compute import ALLOWED_TEXT, run_compute

if TYPE_CHECKING:  # pragma: no cover
    from .problem import Problem
    from .types import LoopState, Verdict

__all__ = ["Checked", "budget_for", "hops_summary", "loop_tools", "orchestrator_tools"]


@dataclass
class Checked:
    """What the turn's `check` calls measured (D505): every code the model checked with its
    verdict, and the best of them -- lower score is better, a finite score beats a refusal.
    The stage reads it after the turn: a check that already passed is the answer whatever
    the model wrote afterwards, and the best checked code seeds the next attempt when the
    model submitted nothing usable."""

    attempts: list[tuple[str, "Verdict"]] = field(default_factory=list)

    def note(self, code: str, v: "Verdict") -> None:
        self.attempts.append((code, v))

    @property
    def best(self) -> tuple[str, "Verdict"] | None:
        finite = [(c, v) for c, v in self.attempts if v.score == v.score and v.score != float("inf")]
        pool = finite or self.attempts
        return min(pool, key=lambda cv: cv[1].score) if pool else None

    def verdict_for(self, code: str) -> "Verdict | None":
        for c, v in reversed(self.attempts):
            if c == code:
                return v
        return None


def budget_for(state: "LoopState") -> ToolBudget:
    return ToolBudget(hops=max(1, int(state.request.tool_hops)),
                      seconds_per_call=float(state.request.compute_timeout_s),
                      hop_share=(float(state.request.hop_share) or None),
                      compact_share=float(getattr(state.request, "compact_share", 0.6) or 0.0),
                      compact=str(getattr(state.request, "compact", "rules") or "rules"),
                      result_chars=max(1000, int(getattr(state.request, "tool_result_chars", 40000))))


def loop_tools(problem: "Problem", subgoal: str | None, state: "LoopState", *,
               checked: Checked | None = None, stage: str = "prototype") -> list[Tool]:
    """The generic tools for one turn, from the problem's hooks. `checked` collects what
    `check` measured; without it (an RTL turn, a planning turn) there is no `check`."""
    tools: list[Tool] = [_compute_tool(state)]
    if checked is not None and stage == "prototype":
        tools.append(_check_tool(problem, subgoal, state, checked))
    if subgoal is not None and problem.prototype() is not None:
        tools.append(_history_tool(problem, subgoal, state))
    if subgoal is not None and getattr(state.part(subgoal), "timing", None):
        tools.append(_timing_tool(subgoal, state))          # D526: the placed path, when there is one
    know = getattr(problem, "knowledge", None)
    if callable(know):
        tools.append(_knowledge_tool(problem, state))
    return tools


# ---------------------------------------------------------------------------- the tools
def _compute_tool(state: "LoopState") -> Tool:
    timeout = float(state.request.compute_timeout_s)

    def run(args: dict[str, Any]) -> str:
        code = str(args.get("code") or "")
        if not code.strip():
            return "error: `code` is empty"
        (_name, out), = run_compute([{"name": "tool", "code": code}], timeout_s=timeout,
                                    max_chars=max(1000, int(getattr(state.request, "tool_result_chars", 40000))))
        return out

    return Tool(
        "compute",
        f"Run a short Python snippet in a sandbox ({ALLOWED_TEXT}; no other import, no files, no "
        "network, no exec/eval, a few seconds of CPU) and get back what it prints. Use it for constants, "
        "tables, error bounds, checking a formula on sample inputs -- never derive numbers by hand. "
        "Write the computation in the snippet itself: a refused snippet is a round lost.",
        {"type": "object", "properties": {"code": {"type": "string", "description": "the Python to run"}},
         "required": ["code"]}, run)


def _check_tool(problem: "Problem", subgoal: str | None, state: "LoopState", checked: Checked) -> Tool:
    tag = subgoal or problem.name

    def run(args: dict[str, Any]) -> str:
        code = str(args.get("prototype") or "")
        if not code.strip():
            return "error: `prototype` is empty; send the whole prototype text"
        from .prototype import check_for

        v = check_for(problem.prototype(), state, subgoal)(code)
        checked.note(code, v)
        why = problem.describe_failure(subgoal, v) if not v.ok else (v.why or "")
        head = ("PASSES: 0 over on the full domain" if v.ok else f"refused, score {v.score:g}")
        return head + ("\n" + why if why else "")

    return Tool(
        "check",
        f"Run THE test of {tag} on a complete prototype text: every input, exactly as the gate will. "
        "Returns PASSES or the score with the failure report (which inputs, what pattern). Check "
        "before you submit; a prototype that passes here is the answer.",
        {"type": "object", "properties": {"prototype": {"type": "string",
                                                        "description": "the complete prototype text"}},
         "required": ["prototype"]}, run)


def _history_tool(problem: "Problem", subgoal: str, state: "LoopState") -> Tool:
    def run(args: dict[str, Any]) -> str:
        from .prototype import history as _history

        n = int(args.get("limit") or 8)
        return _history(state, subgoal, limit=max(1, min(n, 30))) or "(nothing on record for this part yet)"

    return Tool(
        "history",
        f"What this campaign already tried for {subgoal}: the record's attempts, each with its "
        "score and the diagnosis of what was wrong. Read it before repeating an approach.",
        {"type": "object", "properties": {"limit": {"type": "integer", "description": "how many rows (default 8)"}}},
        run)


def _timing_tool(subgoal: str, state: "LoopState") -> Tool:
    def run(args: dict[str, Any]) -> str:
        from .timing import describe

        n = int(args.get("steps") or 8)
        path = state.part(subgoal).timing
        text = describe(path, top=max(1, min(n, 40)))
        if not text:
            return f"(no placed timing path for {subgoal} yet)"
        steps = [s for s in (path.get("steps") or []) if isinstance(s, dict) and s.get("delay_ps") is not None]
        table = "\n".join(f"  {s.get('time_ps', 0):8.0f} ps  +{s.get('delay_ps', 0):6.0f}  {str(s.get('cell') or '').split('_ASAP7')[0]:<14} "
                          f"fanout {s.get('fanout') or 0:<3} {s.get('pin') or ''}" + (f"  -> {s['net']}" if s.get("net") else "")
                          for s in steps[:max(1, min(n, 40))])
        return text + ("\n\nthe path, in order (arrival, the step's delay, the cell, its fanout, the pin):\n" + table if table else "")

    return Tool(
        "timing",
        f"The PLACED critical path of {subgoal}'s design as it stands: arrival against the clock, the slack, "
        "and the costliest steps with their cells and fanout. Read it before a depth pass: the proxy counts "
        "levels, this is what the tools measured.",
        {"type": "object", "properties": {"steps": {"type": "integer", "description": "how many steps of the path to list (default 8)"}}},
        run)


def _knowledge_tool(problem: "Problem", state: "LoopState") -> Tool:
    def run(args: dict[str, Any]) -> str:
        query = str(args.get("query") or "").strip()
        if not query:
            return "error: `query` is empty"
        mentor = problem.knowledge()  # type: ignore[attr-defined]
        if mentor is None:
            return "(this problem declares no knowledge)"
        terms = [t.lower() for t in query.replace(",", " ").split() if len(t) > 2]
        hits: list[str] = []
        try:
            sections = mentor.sections(state)
        except Exception as exc:  # noqa: BLE001
            return f"error: the knowledge could not be read ({exc!s:.100})"
        for title, text in sections:
            lines = text.splitlines()
            for i, line in enumerate(lines):
                low = line.lower()
                if terms and sum(t in low for t in terms) >= max(1, (len(terms) + 1) // 2):
                    lo, hi = max(0, i - 1), min(len(lines), i + 3)
                    hits.append(f"[{title}] " + "\n".join(lines[lo:hi]).strip())
                    if len(hits) >= 12:
                        break
            if len(hits) >= 12:
                break
        return "\n\n".join(hits) if hits else f"nothing in the knowledge matches {query!r}"

    return Tool(
        "knowledge",
        "Search the problem's knowledge (the method sheet, the operator's papers) for a phrase: "
        "returns the matching passages with a line of context.",
        {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, run)


def orchestrator_tools(problem: "Problem", state: "LoopState") -> list[Tool]:
    """What an orchestrating turn may read (D505): the standings, a part's history, the
    decisions already taken, the knowledge. Nothing here changes the state."""
    def standings(_args: dict[str, Any]) -> str:
        try:
            obj = problem.standing(state) or {}
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc!s:.100}"
        lines = [f"GOAL: {obj.get('goal', '')}", f"NOW: {obj.get('now', '')}"]
        if obj.get("composed"):
            lines.append(f"THE WHOLE: {obj['composed']}")
        for part, text in (obj.get("parts") or {}).items():
            lines.append(f"  {part}: {text}")
        for part, cand in sorted((state.admitted or {}).items()):
            lines.append(f"  admitted {part}: {cand.name}")
        if state.improve:
            lines.append("sent back to improve: " + ", ".join(i.candidate.name for i in state.improve))
        return "\n".join(l for l in lines if l.strip())

    def history(args: dict[str, Any]) -> str:
        part = str(args.get("part") or "")
        if not part:
            return "error: name the part"
        from .prototype import history as _history

        try:
            return _history(state, part) or "(nothing on record)"
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc!s:.100}"

    def decisions(_args: dict[str, Any]) -> str:
        rec = getattr(state, "records", None)
        if rec is None or getattr(rec, "store", None) is None:
            return "(no record)"
        try:
            rows = [e for e in rec.store.events(rec.campaign_id) if e.get("kind") == "decision"]
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc!s:.100}"
        if not rows:
            return "(no decision taken yet this campaign)"
        out = []
        for e in rows[-20:]:
            d = e.get("detail") or {}
            out.append(f"[{d.get('what')}] {d.get('pick')} -- {str(d.get('why') or '')[:160]}")
        return "\n".join(out)

    tools = [
        Tool("standings", "The campaign's standings: the goal, what is being worked on now, the whole's "
             "numbers, every part's numbers and constraint, what is admitted, what was sent back.",
             {"type": "object", "properties": {}}, standings),
        Tool("history", "What this campaign already tried for one part: attempts with their scores and "
             "the diagnosis of each failure.",
             {"type": "object", "properties": {"part": {"type": "string"}}, "required": ["part"]}, history),
        Tool("decisions", "The orchestration decisions already taken this campaign, with their reasons.",
             {"type": "object", "properties": {}}, decisions),
    ]
    if callable(getattr(problem, "knowledge", None)):
        tools.append(_knowledge_tool(problem, state))
    return tools


def hops_summary(hops: list | None) -> str:
    """One line per hop of a reply (`Reply.hops`), for the record and the log."""
    lines = []
    for h in hops or []:
        args = json.dumps(h.arguments or {}, ensure_ascii=False)
        lines.append(f"{h.tool}({args[:60]}{'...' if len(args) > 60 else ''}) -> "
                     f"{str(h.result or '')[:100].replace(chr(10), ' ')}")
    return "\n".join(lines)
