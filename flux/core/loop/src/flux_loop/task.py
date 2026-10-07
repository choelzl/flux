"""`PromptProblem`: the `Problem` that runs a task document (D430). What a document says, and
how it is loaded, is `flux_loop.document`.

Prompts are composed from the statement and contract; the gate and stages are commands (or
evaluators named in the ABI registry); the record and report are the loop's.

Commands carry placeholders: `{artifact}` (the candidate written to a file), `{workdir}`,
`{name}`, `{part}`, `{python}` (this interpreter). A gate's `test` prints its failures;
`count_re` (one integer group) or `fail_re` (one match per failure) counts them, and a non-zero
exit with nothing counted is one failure. Exit 3 means the candidate did not build (D594): a
build failure, not a score, so "best so far" is always a design that compiles.

What a document cannot say in prose or numbers is a command beside it -- a search, a check,
a stage, a composition (D798-D803); `needs:` names a stage's tools on PATH, else it is
skipped; `params:` reach any command as `{params}`. The record is named by the id.

The class's methods by concern are mixins beside it (D891): `task_prototype` (the prototype, its
cost and spelling), `task_measure` (objectives, stages, measuring, cache keys and versions),
`task_knowledge` (the library and digests), `task_draft` (who drafts, the coding agent's turns) and
`task_parts` (parts, sub-loops, composition); here are the prompts, the gate, build and judge.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .model import _json
from .problem import Problem
from .types import BuildError, Candidate, LoopRequest, LoopState, Scored, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from .roles import Roles
from .gradient import CHECK_WEIGHT
from .document import (BUILD_FAILED, Part, TaskSpec, _digest_of, _flux_rtl_tools,
                        _knob_subs, _leaf, _point_name, _rig_for, _write_point, _substitute, describe_flow)
from .document import TaskError  # noqa: F401 -- still importable from here
# D891: PromptProblem's methods by concern, one mixin a module; the names stay importable from here
from .task_prototype import DEFAULT_COST_MAX, PrototypeMixin  # noqa: F401
from .task_measure import MeasureMixin, _metrics_in  # noqa: F401
from .task_knowledge import KnowledgeMixin, library_queries  # noqa: F401
from .task_draft import DraftMixin, _printed_artifact  # noqa: F401
from .task_parts import PartsMixin

__all__ = ["PromptProblem", "model_use", "task_report_lines"]


def _count_failures(check: Any, run: Any) -> tuple[int, str]:
    """One check's failures and report: `count_re`, else `fail_re`, else its exit code."""
    out = (run.stdout or "") + ("\n" + run.stderr if run.stderr else "")
    fails: int | None = None
    if check.count_re:
        m = re.search(check.count_re, out)
        fails = int(m.group(1)) if m else None
    elif check.fail_re:
        fails = len(re.findall(check.fail_re, out))
        if fails == 0 and not run.ok:
            fails = None
    if fails is None:
        fails = 0 if run.ok else 1
    text = out.strip()[-4000:] or (f"exit {run.returncode}" if not run.ok else "")
    return fails, text


# ------------------------------------------------------------------ the problem
class PromptProblem(PrototypeMixin, MeasureMixin, KnowledgeMixin, DraftMixin, PartsMixin, Problem):
    """A `Problem` from a task document alone (D430): the prompts from the statement and
    contract, the gate and the stages from its commands, the parts from its list."""

    def __init__(self, task: TaskSpec, *, roles: "Roles | None" = None) -> None:
        self.task = task
        self.name = task.id
        self._count = 0
        self.parts: tuple[Part, ...] = task.parts     # decided by `decompose` when the task says so
        #: Sub-task specs once `"subtasks": "decompose"` has been answered (D455); None until.
        self._children: tuple[TaskSpec, ...] | None = None
        self._roles = _rig_for(task, roles)
        self._caller_roles = roles                     # caller overrides reach the children (D555)
        from .skills import load_skills, skill_index

        self._skills = load_skills(list(task.skills)) if task.skills else []
        if self._skills:                               # every prompt's static part names the skills (D588)
            base_prefix = self.prompt_prefix

            def prefixed(subgoal: Any, state: Any, _base=base_prefix) -> str:
                tools = bool(getattr(getattr(state, "request", None), "tools", True))
                index = skill_index(self._skills, tools=tools)
                head = _base(subgoal, state) or ""
                # before the reply shape: models skip what follows "reply with ONLY JSON"
                i = head.find("REPLY SHAPE")
                if i > 0:
                    return head[:i].rstrip() + "\n\n" + index + "\n\n" + head[i:]
                return (head + "\n\n" + index) if head else index

            self.prompt_prefix = prefixed

    def skill_list(self) -> list[Any]:
        """The document's skills (D588): what the `skill` tool loads and the agents receive."""
        return list(self.__dict__.get("_skills") or [])

    # ---- what the document says
    def ladder(self):
        """The document's `ladder:` (D517): true is the default ladder, an object its fields."""
        doc = self.task.ladder
        if not doc:
            return None
        from .ladder import Ladder

        if doc is True:
            return Ladder()
        return Ladder(**{k: tuple(v) if isinstance(v, list) else v for k, v in doc.items()})

    def roles(self) -> "Roles":
        """Who fills each of the four roles (D460): the document's `roles`, overridden slot by
        slot by the caller's."""
        return self._roles

    # ---- mentor
    def space(self, state: Any) -> dict[str, list]:
        return dict(self.task.space)

    def seeds(self, state: Any) -> list[dict[str, Any]]:
        """The document's `seeds:`, a knob a seed leaves out at its first choice."""
        from .dse import first

        base = first(self.task.space)
        return [{**base, **p} for p in self.task.seeds]

    def instantiate(self, points: list[dict[str, Any]], state: LoopState) -> list[Candidate]:
        """With a `space:` and a generator command, run the command once per point with its knobs
        as `{knob}`; the file written at `{artifact}` is the candidate (D581). Without a command,
        a point stays knobs."""
        cmd = self.task.generator.get("command")
        if not cmd:
            return Problem.instantiate(self, points, state)
        workdir = Path(state.workdir or ".")
        workdir.mkdir(parents=True, exist_ok=True)
        out: list[Candidate] = []
        for point in points:
            name = _point_name(point) or _leaf(self.task.id)
            path = workdir / f"point-{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}{self.task.extension}"
            path.unlink(missing_ok=True)
            subs = {**_knob_subs(point), "artifact": str(path), "workdir": str(workdir), "name": name,
                    "point": _write_point(path, point),
                    "part": "", "python": sys.executable, "home": self.task.home or ".",
                    "failure": "", "attempt": "1"}
            run = self._run(tuple(cmd), subs, self.task.gate.timeout_s, "generate")
            if not run.ok or not path.is_file():
                tail = ((run.stdout or "") + "\n" + (run.stderr or "")).strip()[-300:]
                state.say(f"  {name}: the generator " + (f"exited {run.returncode}" if not run.ok
                                                         else f"wrote no {path.name}") + (f": {tail}" if tail else ""))
                continue
            out.append(Candidate(name, path.read_text(), knobs=dict(point)))
        return out

    def objective(self, request: LoopRequest) -> dict[str, Any]:
        """The record's identity document: the document's id, so sibling campaigns of one
        document find each other (D540)."""
        return {"study": self.task.id}

    def campaign_name(self, request: LoopRequest) -> str | None:
        """The record's name: the document's id, `<parent>/<child>` for a sub-document (D524, D628)."""
        return self.task.record or None

    def objections(self, state: Any) -> list[str]:
        """The model's objections to the document before any step runs (`flow: {validate: llm}`,
        D556), or an agent's (`{validate: {agent: ...}}`, D640). Advisory, never a gate; empty when not
        asked for, with no model, or when the agent fell back."""
        from .boxes import agent_of, box_turn

        agent = agent_of(self.task.flow, "validate")
        if agent is None and (self.task.flow.get("validate") != "llm" or state.proposer is None):
            return []
        import json as _json_mod

        from .model import _ask, _json

        doc = _json_mod.dumps(self.task.to_dict(), indent=1, default=str)[:12000]
        prompt = ("Read this problem document before the run spends anything and OBJECT to what makes it "
                  "unanswerable or wasteful as written: an objective on a metric no stage measures, a goal no "
                  "stage could reach, a part with no gate, a cutoff that contradicts an objective, a budget that "
                  "cannot finish, a statement the parts do not add up to. Say nothing about style. (`stage: deepest` on "
                  "an objective means the last of `stages`; a goal is judged there. `{artifact}`, `{home}`, `{python}` "
                  "and each knob's `{name}` are filled by the loop. No `parts` means one design for the whole "
                  "problem. `finalists: 0` stops at the first stage. `steps` counts the work items of one pass (a "
                  "part to draft, a batch to measure), not the operations inside one; runs go on pass after pass "
                  "until stopped. `--clock-ps` is the clock the tools time against; `fmax_mhz` comes from the "
                  "slack, so a design may beat it. An objective without a goal orders the designs that meet the "
                  "ones before it. Object only to what would make the run fail or waste its budget; if nothing "
                  "does, return no objection.)\n\nTHE DOCUMENT:\n"
                  + doc + "\n\nTHE FLOW IN FORCE:\n" + "\n".join(describe_flow(self.task, self)))
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"},
                                                   "objections": {"type": "array", "items": {"type": "string"}}},
                  "required": ["objections"]}
        if agent is not None:
            got = box_turn("validate", agent, prompt, schema, state, home=self.task.home, problem=self)
            return [str(o)[:300] for o in ((got or {}).get("objections") or []) if str(o).strip()]
        prompt += '\n\nReply as JSON: {"ok": true|false, "objections": ["one line each"]}.'
        try:
            got = _json(_ask(state, prompt, schema).text)
        except Exception as exc:  # noqa: BLE001 -- advisory: a failed reading objects to nothing
            return [f"(the model's reading did not run: {exc!s:.100})"]
        raw = (got or {}).get("objections") if isinstance(got, dict) else None
        return [str(o)[:300] for o in (raw or []) if str(o).strip()]

    def validate(self, request: LoopRequest) -> list[str]:
        """Problems that make the document unanswerable as written (D463): an objective on a
        metric no stage produces, or a cutoff on a metric its own stage does not measure.

        A stage with undeclared metrics (an `evaluator` stage without `metrics`) could produce
        anything, so it silences the objective check rather than failing it."""
        wrong: list[str] = []
        unknown = any((r.evaluator or not r.command) and not r.metrics for r in self.task.stages)
        produced = {m for r in self.task.stages for m in (*r.metrics_re, *r.metrics)}
        if self.task.stages and not unknown:
            for objective in self.task.objectives:
                if objective.metric not in produced:
                    wrong.append(
                        f"objective {objective.metric!r} names a metric no stage "
                        f"measures (this task measures: "
                        f"{', '.join(sorted(produced)) or 'nothing'})")
        if self.task.stages and not unknown:
            # each stage ranks its own rows by the objectives (D351): a stage that lacks one has
            # no front, so nothing climbs from it and nothing is decided on it (D625)
            # A limit named for a deeper stage ranks nothing above it: it waits there (D878)
            names = [s.name for s in self.task.stages]
            for i, stage in enumerate(self.task.stages):
                wanted = [o.metric for o in self.task.objectives
                          if not (o.goal is not None and o.stage in names and names.index(o.stage) > i)]
                lacks = [m for m in wanted if m not in {*stage.metrics_re, *stage.metrics}]
                if lacks:
                    wrong.append(f"the {stage.name} stage does not measure {', '.join(lacks)}: every stage must "
                                 "measure every objective, since each ranks its own results (measure them in "
                                 "one stage, or print them from each stage's command)")
        for stage in self.task.stages:
            mine = {*stage.metrics_re, *stage.metrics}
            for rule in stage.cutoffs:
                metric = rule.get("metric")
                if metric and mine and metric not in mine:
                    wrong.append(f"the {stage.name} stage cuts on {metric!r}, which it does "
                                 f"not measure (it measures: {', '.join(sorted(mine))})")
        return wrong

    def tools_missing(self) -> list[str]:
        missing = self._tools_missing()
        if self.task.generator.get("agent"):
            from .agent import missing_agent

            missing = list(missing) + [t for t in missing_agent(self.task.generator["agent"]) if t not in missing]
        return missing

    def _tools_missing(self) -> list[str]:
        missing: list[str] = []
        declared = {f"stage {r.name}" for r in self.task.stages if r.needs}   # skipped, not missing
        for _label, cmd in self.task.commands():
            head = _substitute(cmd[:1], {"python": sys.executable, "home": self.task.home or "."})[0]
            if head in ("{artifact}", "{workdir}", "{name}", "{part}"):
                continue
            found = Path(head).exists() if "/" in head else shutil.which(head) is not None
            if not found and head not in missing:
                missing.append(head)
            for tool in ([] if _label in declared else _flux_rtl_tools(cmd)):   # tools `flux rtl` runs (D600)
                if shutil.which(tool) is None and tool not in missing:
                    missing.append(tool)
        return missing

    def critique(self, kind: str, subject: Any, state: LoopState) -> Verdict:
        """The model as critic (D433) of a division, a gate-passed candidate, or the decision.

        The verdict is remembered, and an accepted division is remembered as the division.
        Without `flow: {critique: llm}` and a model, or `{critique: {agent: ...}}`, everything passes."""
        from .boxes import agent_of, box_turn

        t = self.task
        agent = agent_of(t.flow, "critique")
        if not t.critique or (state.proposer is None and agent is None):
            if kind == "decomposition" and t.decompose and not getattr(self, "_division_kept", False):
                self._division_kept = True
                self._remember_division(state)
            return Verdict(True, 0.0, "")
        from .model import _ask, _json

        if kind == "decomposition":
            shown = "\n".join(f"- {p.name}: {p.statement}" for p in self.parts)
            what = (f"THE DIVISION INTO PARTS (to be written one at a time and joined in order):\n{shown}\n\n"
                    "Object only to a division that cannot produce the whole: a missing piece, an "
                    "overlap, a part that cannot be checked on its own, an order that cannot be joined.")
            label = ", ".join(p.name for p in self.parts)
        elif kind == "candidate":
            cand: Candidate = subject
            numbered = "\n".join(f"{i + 1:4d} | {ln}" for i, ln in enumerate(cand.artifact.splitlines()))
            part = self._part(cand.subgoal)
            what = ((f"PART {part.name}: {part.statement}\n\n" if part else "")
                    + f"THE CANDIDATE, which the gate has ALREADY PASSED:\n\n{numbered}\n\n"
                    "Object only to a DEFECT the gate cannot see: a violated constraint of the contract "
                    "or statement (a forbidden construct, a required port or behaviour missing), or a "
                    "result the gate's vectors could miss. Comments, naming, style and claims about "
                    "speed or depth are not defects -- the stages measure those. Passing the gate is "
                    "not an issue.")
            label = cand.name
        else:
            pick: Scored = subject
            metrics = ", ".join(f"{k}={v:g}" for k, v in pick.metrics.items())
            what = (f"THE DECISION: {pick.name} on stage {pick.stage} with {metrics}. "
                    "Object only if the objectives or the statement point elsewhere.")
            label = pick.name
        gate = t.gate.line()
        question = "\n\n".join(x for x in (
            f"TASK {t.id}: {t.statement}",
            f"CONTRACT:\n{t.contract}" if t.contract else "",
            f"HOW IT IS JUDGED: `{gate}`; zero failures admits." if gate else "",
            "You are the critic. Your job is to find what is WRONG, precisely and briefly; a "
            "verdict without a concrete issue is worthless, and so is an issue the gate already "
            "covers.",
            what) if x)
        prompt = question + ('\n\nReply with ONLY JSON: {"ok": true|false, "issues": ["<one concrete issue each>"], '
                             '"why": "<one line>"}')
        schema = {"type": "object",
                  "properties": {"ok": {"type": "boolean"},
                                 "issues": {"type": "array", "items": {"type": "string"}},
                                 "why": {"type": "string"}},
                  "required": ["ok"]}
        if agent is not None:
            doc = box_turn("critique", agent, question, schema, state, home=t.home, problem=self)   # None: fell back, no objection
        else:
            try:
                doc = _json(_ask(state, prompt, schema).text)
            except Exception as exc:  # noqa: BLE001 -- a critic that cannot speak does not veto
                state.say(f"  critic did not answer ({exc!s:.80})")
                doc = None
        issues = [str(i) for i in (doc.get("issues") or [])] if isinstance(doc, dict) else []
        ok = bool(doc.get("ok", True)) if isinstance(doc, dict) else True
        if ok or not issues:
            ok, why = True, ""
        else:
            why = "; ".join(issues)[:600]
        if state.records is not None:
            state.records.remember("critique", {"kind": kind, "subject": label, "ok": ok, "why": why})
        if kind == "decomposition" and ok:
            self._division_kept = True
            self._remember_division(state)
        return Verdict(ok, 0.0 if ok else 1.0, why, {"issues": issues})

    # ---- generator
    def _part(self, subgoal: str | None) -> Part | None:
        return next((p for p in self.parts if p.name == subgoal), None)

    def prompt_prefix(self, subgoal: str | None, state: LoopState) -> str:
        t = self.task
        lines = [f"TASK {t.id}: {t.statement}"]
        from .direction import guidance

        lines.append(guidance(self, state, subgoal, active=False))
        part = self._part(subgoal)
        if part is not None:
            lines.append(f"PART {part.name}" + (f": {part.statement}" if part.statement else "")
                         + " -- this turn is about this part only.")
        if t.contract:
            lines.append(f"CONTRACT:\n{t.contract}")
        if t.knowledge:
            lines.append(f"KNOWLEDGE:\n{t.knowledge}")
        role = self._role_knowledge(state, subgoal)      # the knowledge role's text: the library, ... (D462, D648)
        if role:
            lines.append(role)
        lines.append(
            f'REPLY SHAPE: reply with ONLY JSON: {{"artifact": "<the complete {t.language} '
            'text>", "why": "<one line>"}. When asked for edits, reply {"edits": [{"find": '
            '"...", "replace": "..."}], "why": "..."} instead.')
        return "\n\n".join(lines)

    def design_prompt(self, subgoal: str | None, method: str, state: LoopState,
                      human: str | None, prior: Candidate | None, prior_why: str
                      ) -> tuple[str, dict | None]:
        from .novelty import tried_block
        from .direction import guidance

        target = f"part {subgoal}" if subgoal else f"task {self.task.id}"
        parts = [human or "", guidance(self, state, subgoal), tried_block(self, state, subgoal)]
        if prior is not None:
            numbered = "\n".join(f"{i + 1:4d} | {ln}" for i, ln in enumerate(prior.artifact.splitlines()))
            parts += [f"What the loop said about your previous attempt for {target}:\n\n{prior_why}",
                      "Rework it or try a new approach when there is a plausible hypothesis worth measuring.",
                      f"Previous attempt (line numbers for reading only):\n\n{numbered}"]
        else:
            parts.append(f"Write {target} now" + (f" ({method})" if method else "") + ".")
        schema = {"type": "object",
                  "properties": {"artifact": {"type": "string"}, "why": {"type": "string"}},
                  "required": ["artifact"]}
        return "\n\n".join(p for p in parts if p), schema

    def parse_design(self, reply: str, subgoal: str | None) -> tuple[Candidate | None, str]:
        doc = _json(reply)
        artifact: str | None = None
        if isinstance(doc, dict) and isinstance(doc.get("artifact"), str) and doc["artifact"].strip():
            artifact = doc["artifact"]
        elif not reply.lstrip().startswith("{"):
            try:
                from flux_llm import strip_markdown_fence
            except Exception:  # noqa: BLE001
                strip_markdown_fence = lambda t: t  # noqa: E731
            text = strip_markdown_fence(reply)
            if text.strip():
                artifact = text
        if artifact is None:
            return None, "the reply carried no artifact"
        self._count += 1
        return Candidate(f"{subgoal or self.task.id}#{self._count}", artifact,
                         knobs={"task": self.task.id, "part": subgoal or ""}, subgoal=subgoal), ""

    # ---- evaluator
    def _subs(self, cand: Candidate, subgoal: str | None, state: LoopState) -> dict[str, str]:
        workdir = Path(state.workdir or ".")
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", cand.name)
        path = workdir / f"{safe}{self.task.extension}"
        path.write_text(cand.artifact)
        return {**_knob_subs(cand.knobs), "artifact": str(path), "workdir": str(workdir), "name": cand.name,
                "point": _write_point(path, cand.knobs or {}),
                "part": subgoal or "", "python": sys.executable, "home": self.task.home or "."}

    def _run(self, cmd: tuple[str, ...], subs: dict[str, str], timeout_s: float, what: str):
        from flux_evaluator_abi.tools import run_tool

        if any("{params}" in t for t in cmd) and "params" not in subs:
            subs = {**subs, "params": self._params_file(subs.get("workdir") or ".")}   # D799

        who = subs.get("name") or ""                   # D709: the task says which candidate
        return run_tool(_substitute(cmd, subs), cwd=subs["workdir"], timeout_s=timeout_s,
                        what=f"{what} {who}" if who and who not in what else what)

    def _params_file(self, workdir: str) -> str:
        """`{params}` (D799): the document's `params:` as a JSON file a command reads."""
        path = Path(workdir) / "params.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.task.params, indent=1, default=str))
        return str(path)

    def _gate_run(self, subs: dict[str, str]) -> tuple[int, str]:
        """The gate's checks in order (D652): (score, report) of the first that fails, the checks
        after it not run; a check exiting 3 (any non-zero for a `build` check) raises BuildError.
        The score is the failures plus CHECK_WEIGHT per check not reached, so a design stopped
        earlier ranks worse whatever its count."""
        gate = self.task.gate
        for i, check in enumerate(gate):
            run = self._run(check.run, subs, check.timeout_s, check.name)
            at = f"failed at {check.name}: " if len(gate) > 1 else ""
            if run.returncode == BUILD_FAILED or (check.builds and not run.ok):
                text = ((run.stdout or "") + "\n" + (run.stderr or "")).strip()[-4000:]
                raise BuildError((f"did not build at {check.name}: " if len(gate) > 1 else "")
                                 + (text or f"{check.name} exited {run.returncode}"))
            fails, text = _count_failures(check, run)
            if fails:
                return fails + CHECK_WEIGHT * (len(gate) - 1 - i), at + text
        return 0, ""

    def build(self, cand: Candidate, subgoal: str | None, state: LoopState) -> Any:
        # the gate runs here, once: exit 3 is "did not build" (D594); fast_check reuses the result
        subs = self._subs(cand, subgoal, state)
        if self.task.gate:
            self.__dict__.setdefault("_tested", {})[_digest_of(cand.artifact)] = self._gate_run(subs)
        return subs["artifact"]

    def fast_check(self, built: Any, cand: Candidate, subgoal: str | None,
                   state: LoopState) -> tuple[int, str]:
        if not self.task.gate:
            return 0, ""
        got = self.__dict__.get("_tested", {}).pop(_digest_of(cand.artifact), None)   # build ran it (D594)
        return got if got is not None else self._gate_run(self._subs(cand, subgoal, state))

    def judge(self, built: Any, cand: Candidate, subgoal: str | None, state: LoopState) -> Verdict:
        fails, text = self.fast_check(built, cand, subgoal, state)
        return Verdict(fails == 0, float(fails), text if fails else "", {"failures": fails})


# ------------------------------------------------------------------ the report
def task_report_lines(task: TaskSpec, out: Any, problem: Any = None) -> list[str]:
    """The standard report for a task run: the decision and its metrics, the world's
    `report(out)` lines if any, the frontier, then the shared closing sections (D558)."""
    lines = [f"TASK {task.id}: {task.statement[:100]}"]
    d = out.decision
    if d is not None:
        metrics = ", ".join(f"{k}={v:g}" for k, v in d.metrics.items())
        lines.append(f"  DECISION {d.name} [{d.stage}; {out.decided_by}]" + (f": {metrics}" if metrics else ""))
    elif getattr(out, "children", None):            # D802: a parent of sub-loops that composes no whole
        lines.append(f"  DECISIONS, one per sub-loop ({sum(1 for c in out.children.values() if c.decision)} of {len(out.children)})")
        for name, child in out.children.items():
            cd = child.decision
            if cd is None:
                lines.append(f"    {name:<12} decided nothing")
            else:
                metrics = ", ".join(f"{k}={v:g}" for k, v in cd.metrics.items())
                lines.append(f"    {name:<12} {cd.name} [{cd.stage}; {child.decided_by}]" + (f": {metrics}" if metrics else ""))
    elif getattr(out, "closest", None) is not None:            # D900: a correct design, no qualifying answer
        c = out.closest
        metrics = ", ".join(f"{k}={v:g}" for k, v in c.metrics.items())
        lines.append("  NO FEASIBLE DESIGN YET -- no design meets every requirement")
        lines.append(f"  CLOSEST {c.name} [{c.stage}]" + (f": {metrics}" if metrics else "")
                     + (f"; not met: {'; '.join(out.unmet)}" if out.unmet else ""))
    else:
        lines.append("  NO CANDIDATE SURVIVED -- see NOT ESTABLISHED below")
    pool = out.confirmed or out.frontier
    if len(pool) > 1:
        lines.append(f"  frontier ({len(pool)} point(s)):")
        for p in pool:
            lines.append("    " + p.name + ": " + ", ".join(f"{k}={v:g}" for k, v in p.metrics.items()))
    if out.admitted:
        lines.append("  proven: " + ", ".join(f"{k}={c.name}" for k, c in sorted(out.admitted.items())))
    cited = (getattr(out, "provenance", None) or {}).get("library") or []
    if cited:                                                  # D648
        lines.append(f"  library: the prompts cited {len(cited)} file(s): " + ", ".join(cited))
    skipped = problem.skipped_stages() if callable(getattr(problem, "skipped_stages", None)) else []
    for name, tools in skipped:
        lines.append(f"  NOT RUN: stage {name} -- needs {', '.join(tools)}, not on PATH; every number above "
                     "is from the stages before it")
    for stage, n in ((getattr(out, "provenance", None) or {}).get("estimates") or {}).items():     # D665
        lines.append(f"  estimates: {stage}: {n['skipped']} estimated to fail, skipped; {n['measured']} measured")
    try:
        from .report import established, not_established, notes, refused

        for block in (established(out.lessons), not_established(out.not_established),
                      refused(out.refused, render=lambda r: f"{r[0]}: {r[1]}"), notes(out.notes)):
            if block:
                lines += [""] + list(block)
    except Exception:  # noqa: BLE001
        lines += [f"  {ln}" for ln in out.lessons]
    return lines


def model_use(task: "TaskSpec") -> str:
    """Why this document needs a model, or "" when it does not (D608), so a banner names a
    model only when one is used."""
    flow = dict(task.flow or {})
    gen = dict(task.generator or {})
    reasons = []
    searched = flow.get("dse")
    by_command = isinstance(searched, dict) and "command" in searched     # D799: the command writes them
    if not gen and not task.space and not by_command and not task.subtasks and not task.split:
        reasons.append("it writes the candidates")                   # D801: sub-loops write their own
    phases = flow.get("dse")
    specs = phases if isinstance(phases, list) else [phases] if phases else []
    if any((s if isinstance(s, str) else (s or {}).get("policy", "")) in ("llm", "model") for s in specs):
        reasons.append("a search phase asks it for points")
    for box in ("plan", "critique", "validate"):
        if flow.get(box) in ("llm", "model"):
            reasons.append(f"{box}: model")
    reasons += [f"stage {r.name} estimates with it" for r in task.stages if r.estimate and r.estimate.kind == "model"]
    orch = (task.roles or {}).get("orchestrator")
    orch_name = orch if isinstance(orch, str) else (orch.get("name") or next(iter(orch), "")) if isinstance(orch, dict) else ""
    coding = isinstance(orch, dict) and isinstance(orch.get("agent"), dict) and orch["agent"].get("coding")
    if not coding and orch_name in ("llm", "model", "agent"):
        # D843: a coding agent orchestrating (`orchestrate: opencode`, D640) picks by its own turns, not Flux's model
        reasons.append(f"the orchestrator is the {orch_name}")
    return "; ".join(reasons)
