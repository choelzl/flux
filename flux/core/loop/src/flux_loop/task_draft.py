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
from .hidden import hidden_subs
from .task_knowledge import library_queries
from .words import plural
from .recall import LOOKUP_LINE


def _where(state: LoopState) -> dict[str, str]:
    """The record and campaign a turn's `flux loop` reads (its probe context)."""
    rec = getattr(state, "records", None)
    return {"record": str(getattr(state.request, "db", "") or ""), "campaign": str(getattr(rec, "campaign_id", "") or "")}


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

    def _design_name(self, base: str, sg: str | None, state: Any) -> str:
        """D1062: the name of a draft -- the pass's design of this part, a new number the first time
        (`x#4`), then a revision of it for each repair or continuation in the pass (`x#4.2`, `x#4.3`)."""
        ps = state.part(sg) if state is not None else None
        if ps is None or ps.design_no is None:
            # D1075: the parts a pass designs share its number (lo#4, hi#4): one pass, one design of parts
            shared = next((q.design_no for q in state.parts.values() if q.design_no is not None), None) if ps is not None else None
            if shared is None:
                self._count += 1
                shared = self._count
            if ps is not None:
                ps.design_no, ps.revision = shared, 1
            return f"{base}#{shared}"
        ps.revision += 1
        return f"{base}#{ps.design_no}.{ps.revision}"

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
        if state.out_of_time:                     # D1026: the pass gave up; no other turn in it
            return None, state.out_of_time
        if state.agent_broken:                    # D1092
            return None, state.agent_broken
        sess = self._part_session(state, sg, "generate", agent.tool)
        workdir = sess.workdir
        name = self._design_name(sg or _leaf(self.task.id), sg, state)
        safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', name)
        path = workdir / f"draft-{re.sub(r'[^A-Za-z0-9_.-]+', '_', sg or _leaf(self.task.id))}{self.task.extension}"
        prior, failure = attempt.prior, attempt.failure
        budget = dict(agent.probe) if agent.probe is not None else None          # D678
        if prior is None and (sg or "*") in state.best:
            # a part the gate refused or the critic sent back on an earlier step (D669)
            _score, prior, failure = state.best[sg or "*"]
        body = self._agent_body(sg, state, getattr(attempt, "brief", "") or None, prior, failure, path)
        from .ideas import bind, capture, instructions, sidecar

        body += "\n\n" + instructions(path) + "\n\n" + LOOKUP_LINE
        brief = agent_brief(body=body, prefix=self.prompt_prefix(sg, state, agent=True) or "", artifact=path, workdir=workdir,
                            language=self.task.language or "text", part=sg or self.task.id,
                            questions=agent.questions,
                            library=library_section(self, library_queries(self.task, [p for p in self.parts if p.name == sg]), state),
                            workbench=workbench_section(self.task.workbench),
                            probes=probe_line([s.name for s in self.task.stages], budget, allowed=agent.allowed, checks=agent.probe_checks),
                            denied=set(agent.allowed) < set(DENIED))
        workbench_link(self.task.workbench, workdir)
        probe_ctx = probe_context(self.task, workdir, sg or "", budget, checks=agent.probe_checks, **_where(state))
        resume = sess.id if agent.resume and sess.id and prior is not None and failure else None
        message = ""
        if not (agent.resume and sess.id and sess.timed_out):
            sess.design_t0 = time.time()               # D1104: a new design (or repair) begins; a continue goes on with it
        if agent.resume and sess.id and sess.timed_out:
            # D999: its last turn ran out of time mid-design -- the same session finishes it, the file as it left it
            resume = sess.id
            message = (f"YOUR LAST TURN RAN PAST ITS TIME LIMIT before the design was finished. Continue where you "
                       f"stopped: finish the design in `{path}` (what you already wrote there is kept), "
                       "without starting over. Then reply with one line saying the file is written.\n")
            if sess.timeouts >= max(0, int(getattr(state.request, "agent_continues", 3))) - 1:     # the last of its full turns
                # D1088: said -- after this turn only a short one is left, to hand in what there is
                message += ("THIS IS YOUR LAST FULL TURN. Stop exploring alternatives: bring the design to a state that "
                            "builds and passes the checks, write it, and confirm it with `flux probe gate` -- a file that passes is "
                            "kept, and handed in if your time runs out. After this turn you only get a few minutes to hand it in.\n")
            message += "\n\n" + instructions(path)
        elif resume:
            # the session holds the brief: what failed, the file, fix it (D669)
            path.write_text(prior.artifact)
            message = (f"THE LOOP RAN YOUR DRAFT AND REFUSED IT:\n{failure.strip()[:4000]}\n\nThe refused draft is in "
                       f"`{path}`. Edit or replace it to test a reasoned improvement"
                       + ("; `flux probe` has a new budget for this turn. " if budget is not None else ". ")
                       + "Then reply with one line saying the file is written.\n")
            from .direction import guidance

            message += guidance(self, state, sg)
            message += "\n\n" + instructions(path)
        elif prior is not None:
            path.write_text(prior.artifact)            # D1105: the draft to rework is the file it writes, not pasted
        else:
            path.unlink(missing_ok=True)               # a fresh session creates the file
        told = state.__dict__.setdefault("_notes_told", {})          # D1023: notes a session has not heard lead its message
        if message and len(state.human_notes) > told.get(sess.id, 0):
            message = state.notes_block() + "\n\n" + message
        if sess.id or not message:
            told[sess.id or ""] = len(state.human_notes)
        prompt_file = workdir / f"PROMPT-{safe}.md"
        prompt_file.write_text(message or brief)
        subs = {"prompt": brief, "prompt_file": str(prompt_file), "artifact": str(path), "workdir": str(workdir),
                "part": sg or "", "name": name, "python": sys.executable, "home": self.task.home or ".",
                "workbench": self.task.workbench, "probe": probe_ctx, **hidden_subs(self.task)}   # D1159
        if self.agent_skill_list() and not resume:     # install skills where the agent looks (D588, D1152: the built-in ones too)
            from .skills import install

            install(self.agent_skill_list(), workdir)
        t0 = time.monotonic()
        path.with_name(path.name + ".ideas.json").unlink(missing_ok=True)
        turn, asked = converse(agent, subs, workdir=workdir, artifact=path, prompt_file=prompt_file,
                               answer=self._agent_answerer(agent, brief, state), say=state.say,
                               session=resume, message=message)
        self._session_turn(state, sess, turn, "generate", sg, agent.tool, path.is_file(),
                           f"exited {turn.rc}, wrote no {path.name}", message or brief, t0, probe_ctx)
        broken = _cannot_start(turn, time.monotonic() - t0, path)
        if broken:                                # D1092: not a design that failed -- a program that never ran
            state.agent_broken = f"{agent.tool}: {broken}"
            state.say(f"  {agent.tool} cannot start ({broken}); the pass stops instead of trying again")
            return None, state.agent_broken
        if sess.timeouts >= max(1, int(getattr(state.request, "agent_continues", 3))):            # its full turns all ran out of time
            graced = self._hand_in(state, sess, agent, path, subs, prompt_file, safe, turn,
                                   prior.artifact if prior is not None else None)
            if graced is None:                         # nothing that passed, no final turn or it wrote nothing: no design
                return None, self._out_of_time(state, sess, turn, path, agent)
            turn = graced
            sess.timed_out, sess.timeouts = False, 0          # D1088: it handed one in -- judged as any draft
        if sess.timed_out:                        # D1026: cut off mid-design -- not judged; its session continues it
            return None, (f"the coding agent {agent.tool} ran past its time limit ({agent.timeout_s:g} s) before it finished; "
                          f"its session continues ({sess.timeouts} of {int(getattr(state.request, 'agent_continues', 3))} turns)")
        knobs = {"task": self.task.id, "part": sg or "", "generator": f"agent:{agent.tool}"}
        meta = {"questions": [asdict(e) for e in asked]} if asked else {}
        notes = sidecar(path)
        ids = capture(state, sg, notes, inherited=(prior.meta.get("idea_ids") or []) if prior is not None else [])
        if ids:
            meta["idea_ids"] = ids
        unchanged = path.is_file() and prior is not None and path.read_text() == prior.artifact     # D1105: the draft it was given
        if path.is_file() and not unchanged:
            cand = Candidate(name, path.read_text(), knobs=knobs, meta=meta, subgoal=sg)
            bind(state, cand)
            return cand, ""
        from .agent import question_in

        printed = _printed_artifact(turn.text) if turn.ok and question_in(turn.text) is None else None
        if printed is not None:                                   # the agent printed the artifact instead (D848: only
            return Candidate(name, printed, knobs=knobs, meta=meta, subgoal=sg), ""   # code it fenced, not its prose)
        if unchanged:
            return None, f"the coding agent {agent.tool} left the draft unchanged ({path.name}): nothing new to judge"
        tail = ((turn.text or turn.stdout or "") + "\n" + (turn.stderr or "")).strip()[-2000:]
        still = (f" (still asking after {plural(len(asked), 'answer')})" if question_in(turn.text) is not None and asked
                 else " (it asked, and its questions are answered by nobody here)" if question_in(turn.text) is not None else "")
        return None, (f"the coding agent {agent.tool} exited {turn.rc} and wrote no {path.name}{still}"
                      + (f": {tail}" if tail else ""))

    def _agent_body(self, sg: str | None, state: LoopState, human: str | None, prior: Any, failure: str, path: Path) -> str:
        """D1105: what a design agent is told this turn -- the operator's notes, the orchestrator's brief, the pass's
        direction and the job. Nothing it can read itself is pasted: a draft to rework is the artifact file, the loop's
        words on it here (a refusal, or what this pass asks of it); what was tried, the ideas and the results are `flux loop`."""
        from .direction import guidance

        notes = state.notes_block()
        human = (human or "") if not notes or notes not in (human or "") else (human or "").replace(notes, "")
        parts = [notes, human, guidance(self, state, sg)]
        target = f"part {sg}" if sg else f"task {self.task.id}"
        if prior is not None:
            parts.append(f"THE DRAFT TO REWORK for {target} ({prior.name}) is already in `{path}`: edit it there, or replace it. "
                         "Rework it, or try a new approach when there is a plausible hypothesis worth measuring."
                         + (f"\n\nWhat the loop said about it:\n{failure.strip()[:4000]}" if failure.strip() else ""))
        else:
            parts.append(f"Write {target} now.")
        return "\n\n".join(p for p in parts if p)

    def _gate_says(self, path: Path, part: str | None, workdir: Path) -> tuple[bool, str]:
        """D1104: the loop's own gate on a file, as `flux probe gate` runs it, outside the agent's budget and log."""
        from .probe import probe, probe_context

        root = workdir / ".flux-grace"
        root.mkdir(exist_ok=True)
        ctx = probe_context(self.task, root, part or "", {"gate": 10 ** 6, "stages": 0})
        code, text = probe("gate", str(path), None, ctx_path=ctx)
        return code == 0, re.sub(r"\n?\[gate probe \d+ of \d+\]", "", text).strip()

    def _kept(self, sess: Any, prior: str | None) -> Path | None:
        """D1104: the newest file that passed `flux probe gate` since this design began -- not one of an earlier
        design, and not the draft it was asked to change."""
        from .probe import KEPT

        root = Path(sess.workdir) / ".flux-probes"
        kept = sorted((p for p in root.glob(f"{KEPT}*") if p.stat().st_mtime >= sess.design_t0 - 1), key=lambda p: p.stat().st_mtime_ns) \
            if root.is_dir() else []
        for p in reversed(kept):
            try:
                if prior is None or p.read_text() != prior:
                    return p
            except OSError:
                continue
        return None

    def _hand_in(self, state: LoopState, sess: Any, agent: Any, path: Path, subs: dict, prompt_file: Path, safe: str,
                 turn: Any, prior: str | None) -> Any:
        """D1088, D1104: its continues used up -- what the pass gets instead of nothing. The design as it stands when
        the gate passes it (no final turn); else one short final turn told the gate's failure, whose file is handed in
        when the gate passes it; else the last file the gate passed in its probes; else whatever the final turn wrote
        (judged, refused as any draft). None: nothing to hand in."""
        part = subs.get("part") or None
        workdir = Path(subs["workdir"])
        kept = self._kept(sess, prior)
        if path.is_file():
            ok, why = self._gate_says(path, part, workdir)
            if ok:
                state.say(f"  {agent.tool}: out of time, but its design passes the gate as it stands: handed in without a final turn")
                return turn
        else:
            why = f"no {path.name} written yet"
        graced, wrote = self._grace(state, sess, agent, path, subs, prompt_file, safe, why, kept)
        if wrote and kept is not None:
            ok, _why = self._gate_says(path, part, workdir)
            if ok:
                state.say(f"  {agent.tool}: handed in {path.name} in its final turn; it passes the gate")
                return graced
        if kept is not None:
            path.write_text(kept.read_text())
            state.say(f"  {agent.tool}: " + ("its final design does not pass the gate" if wrote else "no design from a final turn")
                      + f": handed in the last version that passed the gate ({time.strftime('%H:%M:%S', time.localtime(kept.stat().st_mtime))}, kept from its probe)")
            return graced or turn
        if wrote:
            state.say(f"  {agent.tool}: handed in {path.name} in its final turn")
            return graced
        return None

    def _grace(self, state: LoopState, sess: Any, agent: Any, path: Path, subs: dict, prompt_file: Path, safe: str,
               why: str, kept: Path | None) -> tuple[Any, bool]:
        """D1088, D1104: one short last turn in the same session whose only job is the gate: the gate's failure in its
        message, one gate probe, no measurements; the kept file that passed offered to copy back. (its turn, whether it
        wrote the design); (None, False) when the grace is off (`agent_grace_s: 0`) or its session cannot resume."""
        import dataclasses

        from .agent import converse
        from .probe import probe_context

        want = getattr(state.request, "agent_grace_s", None)
        grace = min(600.0, max(300.0, agent.timeout_s / 4)) if want is None else float(want)    # 5 to 10 minutes
        if grace <= 0 or not (agent.resume and sess.id):
            return None, False
        message = (f"TIME IS UP. This is your final turn, and it is short ({grace:.0f} s). Its only job: make `{path}` pass "
                   "the loop's gate. Do not experiment, optimise, explore, read papers or write scripts.\n\n"
                   f"The gate on the file as it stands:\n{why[-2500:]}\n\n"
                   "Fix exactly that, in as few edits as you can. If the fix is not small, simplify instead: take out what is "
                   "unfinished or half-changed until what is left is correct.")
        if kept is not None:
            message += (f" A version that passed the gate earlier in this session is kept at `{kept}`: if you cannot fix "
                        f"the file in a few edits, copy that one over `{path}` -- it is what the loop takes if this turn fails.")
        message += (f"\nYou may run `flux probe gate {path}` once to confirm. Then reply with one line saying the file is written.\n")
        grace_file = prompt_file.with_name(f"PROMPT-{safe}-grace.md")
        grace_file.write_text(message)
        state.say(f"  {agent.tool}: out of time after its continues; a final {grace:.0f} s turn to make its design pass the gate")
        rec = getattr(state, "records", None)
        ctx = probe_context(self.task, Path(subs["workdir"]), subs.get("part") or "", {"gate": 1, "stages": 0},
                            record=str(getattr(state.request, "db", "") or ""), campaign=str(getattr(rec, "campaign_id", "") or ""))
        t0, before = time.monotonic(), path.stat().st_mtime_ns if path.is_file() else None
        turn, _asked = converse(dataclasses.replace(agent, timeout_s=grace), {**subs, "prompt_file": str(grace_file), "probe": ctx},
                                workdir=Path(subs["workdir"]), artifact=path, prompt_file=grace_file, answer=None,
                                say=state.say, session=sess.id, message=message)
        self._session_turn(state, sess, turn, "generate", subs.get("part") or None, agent.tool, path.is_file(),
                           f"final turn: exited {turn.rc}, wrote no {path.name}", message, t0, ctx)
        wrote = path.is_file() and path.stat().st_mtime_ns != before      # written in this turn, not left from before
        if not wrote:
            state.say(f"  {agent.tool}: wrote nothing in its final turn")
        return turn, wrote

    def _out_of_time(self, state: LoopState, sess: Any, turn: Any, path: Path, agent: Any) -> str:
        """D1026: the design agent ran past its time limit on its first turn and on every continue: the
        pass gives up -- no design built from what it left -- and says what it was doing, for the
        designs after it (the record's refusal, the lessons)."""
        n = sess.timeouts
        words = " ".join((turn.text or "").split())[-800:]
        left = path.read_text(errors="replace") if path.is_file() else ""
        why = (f"OUT OF TIME: the design agent {agent.tool} ran past its time limit ({agent.timeout_s:g} s) {n} turns in a "
               f"row (budget.agent_continues) -- and the pass gave up without a design."
               + (f" What it said last: {words}" if words else "")
               + (f" It left a partial design of {len(left.splitlines())} lines ({path.name}, in its session folder"
                  f" {path.parent}): a next design may read it, or avoid what made it slow." if left else ""))
        state.out_of_time = why
        sess.timed_out = False                    # nothing continues it: a next pass starts afresh
        state.lessons.append("[out of time] " + why[:1200])
        state.say(f"  out of time: {agent.tool} ran past {agent.timeout_s:g} s {n} times in a row; the pass gives up")
        return why

    def _session_turn(self, state: LoopState, sess: Any, turn: Any, kind: str, sg: str | None, tool: str,
                      ok: bool, why: str, sent: str, t0: float, probe_ctx: str = "") -> None:
        """The session after an agent turn (D669): its id kept, the turn said and on the record,
        with the probes it ran (D678)."""
        from .boxes import record_turn
        from .probe import probes_done

        sess.id = turn.session or sess.id
        sess.turns += 1
        sess.timed_out = turn.rc == 124
        sess.timeouts = sess.timeouts + 1 if sess.timed_out else 0
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
        prototype check and comes back with what failed (D673). Returns the file as a
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
            ["{python}", "-m", "flux_loop.golden_proto", "{artifact}", "--golden", str(golden_path(self.task)),   # D951: `flux rtl` is gone
             "--table-max", str(int(self.task.budget.get("prototype_table_max") or TABLE_MAX))], "the prototype check") or (),
            {"python": sys.executable}))
        brief = agent_brief(body=prompt, prefix="", artifact=path, workdir=workdir, language="Python",
                            part=f"{subgoal or self.task.id} (the prototype `design(...)`)", questions=agent.questions,
                            library=library_section(self, library_queries(self.task, [p for p in self.parts if p.name == subgoal]), state),
                            workbench=workbench_section(self.task.workbench),
                            probes=probe_line([], budget, proto=True, allowed=agent.allowed),
                            denied=set(agent.allowed) < set(DENIED))
        from .ideas import capture, instructions, sidecar

        brief += "\n" + instructions(path) + "\n" + LOOKUP_LINE + "\n"
        workbench_link(self.task.workbench, workdir)
        probe_ctx = probe_context(self.task, workdir, subgoal or "", budget, proto=proto, **_where(state))
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
                "workbench": self.task.workbench, "probe": probe_ctx, **hidden_subs(self.task)}   # D1159
        if self.agent_skill_list() and not resume:     # D1152: the prototype's agent has the loop's skills too
            from .skills import install

            install(self.agent_skill_list(), workdir)
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
        name = self._design_name(attempt.subgoal or _leaf(self.task.id), attempt.subgoal, state)
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


_NO_START = re.compile(r"can't open file|No such file or directory|command not found|not found|Permission denied|"
                       r"cannot execute|Exec format error|ModuleNotFoundError", re.I)


def _cannot_start(turn: Any, seconds: float, path: Path) -> str:
    """D1092: why an agent's turn never began -- exit 2/126/127 within seconds, nothing written, nothing said, its
    error naming a missing or unrunnable program -- or "" for a turn that ran (and maybe failed as a design)."""
    if turn.ok or turn.rc not in (1, 2, 126, 127) or seconds > 5 or path.is_file() or (turn.steps or []):
        return ""
    err = " ".join(str(turn.stderr or "").split())
    m = _NO_START.search(err)
    return err[max(0, m.start() - 120):m.end() + 120].strip() if m else ""


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
