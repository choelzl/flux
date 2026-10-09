"""Who drafts for `PromptProblem` (D456): the model, or the document's command, catalog or coding
agent -- the agent's sessions per part (D669), its turns on a design and on the prototype (D575,
D618), who answers its questions (D585), and what it printed instead of a file (D848). A mixin of
`flux_loop.task.PromptProblem` (D891).
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from .model import _json
from .types import Candidate, LoopState
from .document import _leaf, _substitute
from .task_knowledge import library_queries


class DraftMixin:
    """Who drafts and the coding agent's turns, for `PromptProblem` (D891)."""

    # ------------------------------------------------------------ who drafts (D456)
    def generator(self, subgoal: str | None, state: LoopState):
        """Who drafts: the model, unless the document names a command, a catalog of existing
        designs, or a coding agent. The generate/build/fast-check sub-loop is the loop's."""
        from .sources import Catalog, Template

        chosen = self.roles().generator
        if chosen is not None:
            return chosen                 # a rig (a flag, a caller) named the generator
        spec = self.task.generator
        if not spec:
            return None
        if "catalog" in spec:
            return Catalog(list(spec["catalog"]), self._from_catalog)
        if "agent" in spec:
            from .agent import agent_spec

            agent = agent_spec(spec["agent"])
            return Template(lambda attempt: self._agent_draft(attempt, agent), name=f"agent:{agent.tool}")
        command = tuple(spec["command"])
        return Template(lambda attempt: self._rendered(attempt, command))

    def _part_session(self, state: LoopState, sg: str | None, kind: str, tool: str):
        """The part's `kind` ("generate" | "prototype") agent session (D669), made on first use in
        a directory of its own: `agents/<kind>/<part>/`, then `<part>-2`, ... for a later fresh
        session of the same part (an improve after admission)."""
        from .types import AgentSession

        ps = state.part(sg)
        sess = ps.sessions.get(kind)
        if sess is None or sess.tool != tool:
            root = Path(state.workdir or ".").resolve() / "agents" / kind
            base = re.sub(r"[^A-Za-z0-9_.-]+", "_", sg or _leaf(self.task.id))
            n, workdir = 1, root / base
            while workdir.exists():
                n += 1
                workdir = root / f"{base}-{n}"
            workdir.mkdir(parents=True)
            sess = ps.sessions[kind] = AgentSession(tool, workdir)
        return sess

    def _agent_draft(self, attempt: Any, agent: Any):
        """A coding agent's turn (D575): brief on disk, agent run in the part's session directory,
        its questions answered by the document's policy (D585). The candidate is the file it
        wrote, or its printed reply parsed like a model's. A repair or a send-back of a part not
        yet admitted resumes the part's session with a short message (D669); without a session
        to resume, the full brief carries the prior draft and the failure."""
        from dataclasses import asdict

        from .agent import DENIED, agent_brief, converse, library_section, workbench_link, workbench_section
        from .probe import probe_context, probe_line

        state = attempt.state
        sg = attempt.subgoal
        sess = self._part_session(state, sg, "generate", agent.tool)
        workdir = sess.workdir
        self._count += 1
        name = f"{sg or _leaf(self.task.id)}#{self._count}"
        safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', name)
        path = workdir / f"draft-{re.sub(r'[^A-Za-z0-9_.-]+', '_', sg or _leaf(self.task.id))}{self.task.extension}"
        prior, failure = attempt.prior, attempt.failure
        budget = dict(agent.probe) if agent.probe is not None else None          # D678
        if prior is None and (sg or "*") in state.best:
            # a part the gate refused or the critic sent back on an earlier step (D669)
            _score, prior, failure = state.best[sg or "*"]
        if prior is not None and failure:
            body, _schema = self.rewrite_prompt(sg, prior, failure, state)
        else:
            body, _schema = self.design_prompt(sg, "", state, getattr(attempt, "brief", "") or None, prior, failure)
        from .ideas import bind, capture, instructions, sidecar

        body += "\n\n" + instructions(path)
        brief = agent_brief(body=body, prefix=self.prompt_prefix(sg, state, agent=True) or "", artifact=path, workdir=workdir,
                            language=self.task.language or "text", part=sg or self.task.id,
                            prior=prior.artifact if prior is not None else None, failure=failure,
                            questions=agent.questions,
                            library=library_section(self, library_queries(self.task, [p for p in self.parts if p.name == sg]), state),
                            workbench=workbench_section(self.task.workbench),
                            probes=probe_line([s.name for s in self.task.stages], budget, allowed=agent.allowed),
                            denied=set(agent.allowed) < set(DENIED))
        workbench_link(self.task.workbench, workdir)
        probe_ctx = probe_context(self.task, workdir, sg or "", budget)
        resume = sess.id if agent.resume and sess.id and prior is not None and failure else None
        message = ""
        if resume:
            # the session holds the brief: what failed, the file, fix it (D669)
            path.write_text(prior.artifact)
            message = (f"THE LOOP RAN YOUR DRAFT AND REFUSED IT:\n{failure.strip()[:4000]}\n\nThe refused draft is in "
                       f"`{path}`. Edit or replace it to test a reasoned improvement"
                       + ("; `flux probe` has a new budget for this turn. " if budget is not None else ". ")
                       + "Then reply with one line saying the file is written.\n")
            from .direction import guidance

            message += guidance(self, state, sg)
            message += "\n\n" + instructions(path)
        else:
            path.unlink(missing_ok=True)               # a fresh session creates the file
        prompt_file = workdir / f"PROMPT-{safe}.md"
        prompt_file.write_text(message or brief)
        subs = {"prompt": brief, "prompt_file": str(prompt_file), "artifact": str(path), "workdir": str(workdir),
                "part": sg or "", "name": name, "python": sys.executable, "home": self.task.home or ".",
                "workbench": self.task.workbench, "probe": probe_ctx}
        if self.skill_list() and not resume:           # install skills where the agent looks (D588)
            from .skills import install

            install(self.skill_list(), workdir)
        t0 = time.monotonic()
        path.with_name(path.name + ".ideas.json").unlink(missing_ok=True)
        turn, asked = converse(agent, subs, workdir=workdir, artifact=path, prompt_file=prompt_file,
                               answer=self._agent_answerer(agent, brief, state), say=state.say,
                               session=resume, message=message)
        self._session_turn(state, sess, turn, "generate", sg, agent.tool, path.is_file(),
                           f"exited {turn.rc}, wrote no {path.name}", message or brief, t0, probe_ctx)
        knobs = {"task": self.task.id, "part": sg or "", "generator": f"agent:{agent.tool}"}
        meta = {"questions": [asdict(e) for e in asked]} if asked else {}
        notes = sidecar(path)
        ids = capture(state, sg, notes, inherited=(prior.meta.get("idea_ids") or []) if prior is not None else [])
        if ids:
            meta["idea_ids"] = ids
        if path.is_file():
            cand = Candidate(name, path.read_text(), knobs=knobs, meta=meta, subgoal=sg)
            bind(state, cand)
            return cand, ""
        from .agent import question_in

        printed = _printed_artifact(turn.text) if turn.ok and question_in(turn.text) is None else None
        if printed is not None:                                   # the agent printed the artifact instead (D848: only
            return Candidate(name, printed, knobs=knobs, meta=meta, subgoal=sg), ""   # code it fenced, not its prose)
        tail = ((turn.text or turn.stdout or "") + "\n" + (turn.stderr or "")).strip()[-2000:]
        still = (f" (still asking after {len(asked)} answer(s))" if question_in(turn.text) is not None and asked
                 else " (it asked, and its questions are answered by nobody here)" if question_in(turn.text) is not None else "")
        return None, (f"the coding agent {agent.tool} exited {turn.rc} and wrote no {path.name}{still}"
                      + (f": {tail}" if tail else ""))

    def _session_turn(self, state: LoopState, sess: Any, turn: Any, kind: str, sg: str | None, tool: str,
                      ok: bool, why: str, sent: str, t0: float, probe_ctx: str = "") -> None:
        """The session after an agent turn (D669): its id kept, the turn said and on the record,
        with the probes it ran (D678)."""
        from .boxes import record_turn
        from .probe import probes_done

        sess.id = turn.session or sess.id
        sess.turns += 1
        state.say(f"  {kind} {sg or self.task.id}: agent {tool}, {turn.began} session"
                  + (f" {sess.id}" if sess.id else "") + f", turn {sess.turns}, {len(sent)} chars sent")
        probes = probes_done(probe_ctx)
        if probes:
            by: dict[str, int] = {}
            for p in probes:
                by[p["key"]] = by.get(p["key"], 0) + 1
            state.say(f"  {kind} {sg or self.task.id}: the agent probed " + ", ".join(f"{k} x{n}" for k, n in by.items())
                      + f"; last: {probes[-1].get('result', '')[:120]}")
        record_turn(state, {"box": kind, "part": sg or "", "agent": tool, "ok": ok, "why": "" if ok else why,
                            "seconds": round(time.monotonic() - t0, 1), "session": turn.began,
                            "session_id": sess.id or "", "message_chars": len(sent),
                            **({"probes": probes} if probes else {})})

    def prototype_agent(self) -> Any | None:
        """The coding agent that writes the prototype (D618), when `flow.generate: {agent: ...}`
        is set and a Python prototype stage exists; the loop spells the RTL from its output."""
        if not self.task.generator.get("agent"):
            return None
        cap = self.prototype()
        if cap is None or getattr(cap, "language", "") != "python":
            return None
        from .agent import agent_spec

        return agent_spec(self.task.generator["agent"])

    def prototype_agent_turn(self, agent: Any, prompt: str, code: str | None, failure: str,
                             subgoal: str | None, state: LoopState) -> str:
        """One agent turn on the prototype (D618): it edits a file; the loop runs the stage's own
        check (`flux rtl proto`) and comes back with what failed (D673). Returns the file as a
        `{"prototype": ...}` reply, or "" when nothing new was written."""
        from .agent import DENIED, agent_brief, converse, library_section, workbench_link, workbench_section
        from .document import _command
        from .golden_proto import TABLE_MAX, golden_path
        from .probe import probe_context, probe_line

        sess = self._part_session(state, subgoal, "prototype", agent.tool)       # D669: until the prototype passes
        workdir = sess.workdir
        self._count += 1
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{subgoal or _leaf(self.task.id)}_{self._count}")
        path = workdir / f"prototype-{re.sub(r'[^A-Za-z0-9_.-]+', '_', subgoal or _leaf(self.task.id))}.py"
        if code:
            path.write_text(code)
        else:
            path.unlink(missing_ok=True)
        budget = dict(agent.probe) if agent.probe is not None else None          # D678: the prototype's check
        proto = list(_substitute(_command(
            ["flux", "rtl", "proto", "{artifact}", "--golden", str(golden_path(self.task)),
             "--table-max", str(int(self.task.budget.get("prototype_table_max") or TABLE_MAX))], "the prototype check") or (),
            {"python": sys.executable}))
        brief = agent_brief(body=prompt, prefix="", artifact=path, workdir=workdir, language="Python",
                            part=f"{subgoal or self.task.id} (the prototype `design(...)`)", prior=None,
                            failure=failure, questions=agent.questions,
                            library=library_section(self, library_queries(self.task, [p for p in self.parts if p.name == subgoal]), state),
                            workbench=workbench_section(self.task.workbench),
                            probes=probe_line([], budget, proto=True, allowed=agent.allowed),
                            denied=set(agent.allowed) < set(DENIED))
        from .ideas import capture, instructions, sidecar

        brief += "\n" + instructions(path) + "\n"
        workbench_link(self.task.workbench, workdir)
        probe_ctx = probe_context(self.task, workdir, subgoal or "", budget, proto=proto)
        resume = sess.id if agent.resume and sess.id and code else None
        message = ""
        if resume:
            # the session holds the brief: what the check said, the file, fix it (D669)
            message = (f"THE LOOP RAN YOUR PROTOTYPE AND REFUSED IT:\n{(failure or 'see the check').strip()[:4000]}\n\n"
                       f"It is in `{path}`. Edit or replace it to test a reasoned improvement"
                       + ("; `flux probe gate` has a new budget for this turn. " if budget is not None else ". ")
                       + "Then reply with one line saying the file is written.\n")
            from .direction import guidance

            message += guidance(self, state, subgoal)
        prompt_file = workdir / f"PROMPT-{safe}.md"
        prompt_file.write_text(message or brief)
        subs = {"prompt": brief, "prompt_file": str(prompt_file), "artifact": str(path), "workdir": str(workdir),
                "part": subgoal or "", "name": safe, "python": sys.executable, "home": self.task.home or ".",
                "workbench": self.task.workbench, "probe": probe_ctx}
        t0 = time.monotonic()
        path.with_name(path.name + ".ideas.json").unlink(missing_ok=True)
        turn, _asked = converse(agent, subs, workdir=workdir, artifact=path, prompt_file=prompt_file,
                                answer=self._agent_answerer(agent, brief, state), say=state.say,
                                session=resume, message=message)
        text = path.read_text() if path.is_file() else ""
        notes = sidecar(path)
        capture(state, subgoal, notes)
        self._session_turn(state, sess, turn, "prototype", subgoal, agent.tool, bool(text.strip()) and text != code,
                           f"exited {turn.rc}, left no new {path.name}", message or brief, t0, probe_ctx)
        if not text.strip() or (code and text == code):
            tail = ((turn.text or turn.stdout or "") + "\n" + (turn.stderr or "")).strip()[-400:]
            state.say(f"  prototype {subgoal or self.task.id}: the coding agent {agent.tool} exited {turn.rc} "
                      f"and left no new prototype" + (f": {tail}" if tail else ""))
            return ""
        return json.dumps({"prototype": text, "why": (turn.text or "")[-600:],
                           **{k: notes[k] for k in ("idea", "ideas") if k in notes}})

    def _agent_answerer(self, agent: Any, brief: str, state: LoopState):
        """Who answers the agent's questions (D585), as the document's `questions:` allows: the
        operator within `wait_s`, then the loop's model as the brief's author, else "decide"."""
        from .agent import DECIDE

        def answer(question: str) -> tuple[str, str]:
            if agent.questions == "operator" and state.feedback is not None:
                import time

                state.say(f"QUESTION from the coding agent (answer at the prompt line within {agent.wait_s:.0f}s):\n{question}")
                from flux_profile import mark

                mark("question", json.dumps({"question": question, "wait_s": agent.wait_s, "asked": time.time()}))   # D684
                until = time.monotonic() + agent.wait_s
                while time.monotonic() < until:
                    before = len(state.human_notes)
                    state.drain()                       # records the note as every operator line is (D388)
                    fresh = [n.text for n in state.human_notes[before:] if str(n.text).strip()]
                    if fresh:
                        return "\n".join(fresh).strip(), "operator"
                    time.sleep(2.0)
            if agent.questions in ("model", "operator") and state.proposer is not None:
                from .model import _ask

                prompt = ("A coding agent working from the brief below stopped to ask a question. Answer it as the "
                          "designer who wrote the brief: short and decisive. Where the brief does not settle it, "
                          "choose what is most likely to pass the gate and say why in one line.\n\nTHE BRIEF:\n"
                          + brief[-12000:] + "\n\nTHE AGENT ASKS:\n" + question)
                try:
                    text = (_ask(state, prompt).text or "").strip()
                except Exception as exc:  # noqa: BLE001 -- a failed answer is "decide", never a crash
                    state.say(f"  the model could not answer the agent ({exc!s:.100})")
                    text = ""
                if text:
                    return text, "model"
            return DECIDE, "decide"

        return answer

    def _from_catalog(self, item: Any, attempt: Any):
        """One catalog entry as a candidate: a path whose text is the design."""
        from dataclasses import replace

        from .sources import from_file

        self._count += 1
        if isinstance(item, str) and self.task.home and not Path(item).is_absolute():
            item = str(Path(self.task.home) / item)            # D801: beside the document that names it
        cand, why = from_file(item, attempt,
                              name=f"{_leaf(self.task.id)}#{self._count}")
        if cand is None:
            return None, why
        return replace(cand, knobs={**cand.knobs, "task": self.task.id,
                                                "part": attempt.subgoal or ""}), ""

    def _rendered(self, attempt: Any, command: tuple[str, ...]):
        """Run the document's generator command: it writes `{artifact}` and is given `{failure}`
        (why the last draft was refused) and `{attempt}`."""
        state = attempt.state
        workdir = Path(state.workdir or ".")
        self._count += 1
        name = f"{attempt.subgoal or _leaf(self.task.id)}#{self._count}"
        path = workdir / f"draft-{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}{self.task.extension}"
        subs = {"artifact": str(path), "workdir": str(workdir), "name": name,
                "part": attempt.subgoal or "", "python": sys.executable, "home": self.task.home or ".",
                "failure": attempt.failure, "attempt": str(attempt.index + 1)}
        run = self._run(command, subs, self.task.gate.timeout_s, "generate")
        if not run.ok:
            text = ((run.stdout or "") + "\n" + (run.stderr or "")).strip()[-2000:]
            return None, text or f"the generator command exited {run.returncode}"
        if not path.is_file():
            return None, (f"the generator command exited 0 but wrote no {path.name}; it must "
                          f"write the artifact to {{artifact}}")
        return Candidate(name, path.read_text(),
                         knobs={"task": self.task.id, "part": attempt.subgoal or "",
                                "generator": "command"}, subgoal=attempt.subgoal), ""


def _printed_artifact(text: str) -> str | None:
    """The artifact an agent printed instead of writing its file (D848): the JSON reply's `artifact`,
    else its largest fenced code block; None for prose -- a note that it could not write the file
    is no design, and gated as one it read as `SyntaxError: invalid character '’'`."""
    text = (text or "").strip()
    if not text:
        return None
    doc = _json(text)
    if isinstance(doc, dict) and isinstance(doc.get("artifact"), str) and doc["artifact"].strip():
        return doc["artifact"]
    blocks = [b for b in re.findall(r"```[^\n`]*\n(.*?)```", text, flags=re.S) if b.strip()]
    return max(blocks, key=len) if blocks else None
