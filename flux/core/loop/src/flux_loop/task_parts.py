"""`PromptProblem`'s parts and sub-loops (D431, D455, D801): the division into parts (listed, or
asked of the model and remembered), the sub-tasks each a loop of its own, and the whole composed
from them. A mixin of `flux_loop.task.PromptProblem` (D891).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from .types import Candidate, LoopState, SubLoop
from .document import Part, TaskSpec, _leaf


class PartsMixin:
    """The parts, the sub-loops and the composition, for `PromptProblem` (D891)."""

    # ---- orchestrator
    def subgoals(self) -> list[str]:
        return [p.name for p in self.parts]

    def decompose(self, state: LoopState,
                  critique: str | None = None) -> list[str | SubLoop]:
        """The parts to make (D431/D455): the document's sub-tasks (each its own loop), else its
        listed `parts`, else, with "decompose", a division asked of the model.

        A model division is validated (unique identifier names, 1..max_parts) and remembered
        so a resume reuses it; no model raises. With a `critique` (D433) the previous division
        and the objection are shown and a new one is asked for; only the standing one is kept."""
        t = self.task
        if t.subtasks or t.split:
            return list(self.subproblems(state))
        if not t.decompose:
            return [p.name for p in self.parts]
        records = state.records
        earlier = records.recall("decomposition") if records is not None else []
        if earlier and critique is None:
            doc = earlier[-1]
            self.parts = tuple(Part(p["name"], p.get("statement", "")) for p in doc["parts"])
            state.say(f"decompose: {len(self.parts)} part(s) resumed from the record: "
                      + ", ".join(p.name for p in self.parts))
            return [p.name for p in self.parts]
        if state.proposer is None:
            raise RuntimeError(f"task {t.id} asks to be decomposed but no model is available")
        from .model import _ask, _json

        objection = ""
        if critique:
            previous = "; ".join(f"{p.name}: {p.statement}" for p in self.parts)
            objection = (f"Your previous division was: {previous}\n\nA critic objected: "
                         f"{critique}\n\nDivide it again, answering the objection.")
        prompt = "\n\n".join(x for x in (
            f"TASK {t.id}: {t.statement}",
            f"CONTRACT:\n{t.contract}" if t.contract else "",
            f"KNOWLEDGE:\n{t.knowledge}" if t.knowledge else "",
            objection,
            f"Divide this task into 1 to {t.max_parts} parts that can be written and checked "
            "one at a time and then joined in order into the whole. Each part has a short "
            "identifier name (letters, digits, underscores) and a one-line statement of exactly "
            "what it must contain. Fewer parts is better when the task is small.",
            'Reply with ONLY JSON: {"parts": [{"name": "<identifier>", "statement": "<one line>"}], '
            '"why": "<one line>"}') if x)
        schema = {"type": "object",
                  "properties": {"parts": {"type": "array", "minItems": 1, "maxItems": t.max_parts,
                                           "items": {"type": "object",
                                                     "properties": {"name": {"type": "string"},
                                                                    "statement": {"type": "string"}},
                                                     "required": ["name", "statement"]}},
                                 "why": {"type": "string"}},
                  "required": ["parts"]}
        reply = _ask(state, prompt, schema).text
        doc = _json(reply)
        why = self._check_decomposition(doc)
        if why:
            raise RuntimeError(f"task {t.id}: the decomposition was refused: {why}")
        self.parts = tuple(Part(str(p["name"]).strip(), str(p.get("statement") or "").strip())
                           for p in doc["parts"])
        self._division_why = str(doc.get("why") or "")[:200]
        # Remember it when it stands: at once without a critic or when answering a critique
        # (the last remembered wins on resume), otherwise on the critic's acceptance.
        if critique is not None or not (t.critique and state.request.critique_rounds > 0):
            self._remember_division(state)
        state.say(f"decompose: {len(self.parts)} part(s): " + ", ".join(p.name for p in self.parts))
        return [p.name for p in self.parts]

    def _remember_division(self, state: LoopState) -> None:
        if state.records is not None:
            state.records.remember("decomposition", {
                "parts": [{"name": p.name, "statement": p.statement} for p in self.parts],
                "why": getattr(self, "_division_why", "")})

    def _check_decomposition(self, doc: Any) -> str:
        if not isinstance(doc, dict) or not isinstance(doc.get("parts"), list) or not doc["parts"]:
            return "the reply carried no parts"
        if len(doc["parts"]) > self.task.max_parts:
            return f"{len(doc['parts'])} parts, at most {self.task.max_parts} allowed"
        names = []
        for p in doc["parts"]:
            name = str((p or {}).get("name") or "").strip() if isinstance(p, dict) else ""
            if not name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
                return f"part name {name!r} is not an identifier"
            if name in names:
                return f"part name {name!r} repeats"
            names.append(name)
        return ""

    def subproblems(self, state: LoopState) -> list[SubLoop]:
        """The document's sub-tasks, each its own loop (D455).

        Either nested documents, or with `"subtasks": "decompose"` a split asked of the model:
        children inherit this task's contract, gate, stages and objectives with their own
        statement. An asked-for split is remembered so a resume reuses it.
        """
        from .task import PromptProblem              # D891: task.py imports this module

        t = self.task
        if t.subtasks:
            return [SubLoop(name=_leaf(c.id), problem=PromptProblem(c, roles=self._caller_roles), statement=c.statement)
                    for c in t.subtasks]
        if not t.split:
            return []
        if self._children is None:
            self._children = self._ask_for_subtasks(state)
        return [SubLoop(name=_leaf(c.id), problem=PromptProblem(c, roles=self._caller_roles), statement=c.statement)
                for c in self._children]

    def _ask_for_subtasks(self, state: LoopState) -> tuple["TaskSpec", ...]:
        t = self.task
        records = state.records
        earlier = records.recall("subtasks") if records is not None else []
        if earlier:
            named = [(str(c["name"]), str(c.get("statement") or "")) for c in earlier[-1]["subtasks"]]
            state.say(f"subtasks: {len(named)} resumed from the record: "
                      + ", ".join(n for n, _s in named))
            return self._children_from(named)
        if state.proposer is None:
            raise RuntimeError(f"task {t.id} asks to be split into sub-tasks but no model is "
                               "available to divide it")
        from .model import _ask, _json

        prompt = "\n\n".join(x for x in (
            f"TASK {t.id}: {t.statement}",
            f"CONTRACT:\n{t.contract}" if t.contract else "",
            f"KNOWLEDGE:\n{t.knowledge}" if t.knowledge else "",
            f"Divide this into 1 to {t.max_subtasks} SUB-TASKS. A sub-task is not a piece of one "
            "artifact: it is a problem of its own, designed, built and judged on its own, whose "
            "answer the others do not contain. Each has a short identifier name (letters, "
            "digits, underscores) and a one-line statement of exactly what it must answer. "
            "Fewer is better; one means the task should not be split at all.",
            'Reply with ONLY JSON: {"subtasks": [{"name": "<identifier>", "statement": '
            '"<one line>"}], "why": "<one line>"}') if x)
        schema = {"type": "object",
                  "properties": {"subtasks": {
                      "type": "array", "minItems": 1, "maxItems": t.max_subtasks,
                      "items": {"type": "object",
                                "properties": {"name": {"type": "string"},
                                               "statement": {"type": "string"}},
                                "required": ["name", "statement"]}},
                      "why": {"type": "string"}},
                  "required": ["subtasks"]}
        doc = _json(_ask(state, prompt, schema).text)
        named: list[tuple[str, str]] = []
        if isinstance(doc, dict):
            for child in doc.get("subtasks") or ():
                name = str(child.get("name") or "").strip()
                if name.isidentifier() and name not in {n for n, _s in named}:
                    named.append((name, str(child.get("statement") or "").strip()))
        if not named:
            raise RuntimeError(f"task {t.id}: the split into sub-tasks was refused: no usable "
                               f"names in {str(doc)[:200]}")
        state.say(f"subtasks: {len(named)}: " + ", ".join(n for n, _s in named))
        if records is not None:
            records.remember("subtasks", {
                "subtasks": [{"name": n, "statement": st} for n, st in named],
                "why": str(doc.get("why") or "")[:200] if isinstance(doc, dict) else ""})
        return self._children_from(named)

    def _children_from(self, named: list[tuple[str, str]]) -> tuple["TaskSpec", ...]:
        """One child spec per sub-task: this task, restated, and never splitting again."""
        from dataclasses import replace

        return tuple(replace(self.task, id=f"{self.task.id}/{name}", statement=statement,
                             subtasks=(), split=False, parts=(),
                             decompose=self.task.decompose)
                     for name, statement in named)

    def compose(self, admitted: dict[str, Candidate], state: LoopState) -> Candidate | None:
        if self.task.subtasks or self.task.split:
            # the sub-loops' decisions, joined in declared order (D455)
            names = [_leaf(c.id) for c in (self.task.subtasks or self._children or ())]
            ordered = [admitted[n] for n in names if n in admitted]
            if not ordered or len(ordered) != len(names):
                return None
            command = (self.task.generator or {}).get("command")
            if command:                                 # D801: the parent's generate composes them
                return self._composed(dict(zip(names, ordered)), tuple(command), state)
            return None                                 # D802: no generate, no whole -- each is its own answer
        if not self.parts:
            return super().compose(admitted, state)
        ordered = [admitted[p.name] for p in self.parts if p.name in admitted]
        if len(ordered) != len(self.parts):
            return None
        return Candidate(self.task.id, self.task.joiner.join(c.artifact for c in ordered),
                         knobs={"task": self.task.id, "parts": [c.name for c in ordered]})

    def _composed(self, parts: dict[str, Candidate], command: tuple[str, ...], state: LoopState) -> Candidate | None:
        """The whole, as the parent's `generate: {command}` writes it from its sub-loops'
        decisions (D801): `{parts}` is a JSON file of each part's name and the path of its
        artifact; the command writes `{artifact}`."""
        workdir = Path(state.workdir or ".") / "compose"
        workdir.mkdir(parents=True, exist_ok=True)
        files = {}
        for name, cand in parts.items():
            f = workdir / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}{self.task.extension}"
            f.write_text(cand.artifact)
            files[name] = str(f)
        (workdir / "parts.json").write_text(json.dumps(files, indent=1))
        path = workdir / f"{_leaf(self.task.id)}{self.task.extension}"
        path.unlink(missing_ok=True)
        subs = {"artifact": str(path), "workdir": str(workdir), "name": _leaf(self.task.id), "parts": str(workdir / "parts.json"),
                "part": "", "python": sys.executable, "home": self.task.home or ".", "point": ""}
        run = self._run(command, subs, self.task.gate.timeout_s, "generate: compose")
        if not run.ok or not path.is_file():
            tail = ((run.stdout or "") + "\n" + (run.stderr or "")).strip()[-300:]
            state.not_established.append(f"the parts were not composed: the generate command exited {run.returncode}: {tail}")
            return None
        return Candidate(self.task.id, path.read_text(), knobs={"task": self.task.id, "subtasks": list(parts)})
