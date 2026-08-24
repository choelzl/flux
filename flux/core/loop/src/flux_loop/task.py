"""The standard task document, and the problem that runs one (D425 increment 2, D430).

A task enters the loop as a document -- statement, contract, parts, a gate (how a
candidate is checked), costed stages (how it is measured), objectives (what "better"
means), a budget -- and `PromptProblem` turns that document into a `Problem` with no
code of its own: the prompts are composed from the statement and contract, the gate and
the stages are commands (or evaluators named in the ABI registry), the record and the
report are the loop's. What a problem-specific `Problem` subclass adds on top of this
(a table oracle, an exhaustive reference, a prototype stage) stays possible; what every
task needs no longer has to be written.

Commands carry placeholders: `{artifact}` (the candidate written to a file),
`{workdir}`, `{name}`, `{part}`, `{python}` (this interpreter). A gate's `test` prints
its failures; `count_re` (one integer group) or `fail_re` (one match per failure) says
how the loop counts them, and a non-zero exit with nothing counted is one failure.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

from .model import _json
from .problem import Problem
from .types import (BuildError, Candidate, LoopRequest, LoopState, StageNames, Scored,
                    SubLoop, Verdict)

if TYPE_CHECKING:  # pragma: no cover
    from .roles import Roles

__all__ = ["Gate", "Objective", "Part", "PromptProblem", "Stage", "TaskError", "TaskSpec",
           "load_task", "request_for", "task_report_lines"]

_PLACEHOLDER = re.compile(r"\{(artifact|workdir|name|part|python)\}")
_DIRECTIONS = ("maximize", "minimize")


class TaskError(ValueError):
    """The document is not a task: the message names the field and what it should be."""


@dataclass(frozen=True)
class Part:
    name: str
    statement: str = ""


@dataclass(frozen=True)
class Gate:
    """How a candidate is checked. `build` refuses (non-zero exit) what cannot be built;
    `test` counts failures; both optional, at least one required."""

    build: tuple[str, ...] | None = None
    test: tuple[str, ...] | None = None
    count_re: str | None = None          # one integer group: the failure count the test prints
    fail_re: str | None = None           # one match per failure
    timeout_s: float = 120.0


@dataclass(frozen=True)
class Stage:
    """One costed measurement: a command whose output carries the metrics (`metrics_re`,
    one float group each), or an evaluator named in the ABI registry applied to the
    artifact read as an architecture document.

    `cutoff` is what is worth the NEXT stage (D454), declared rather than coded: one of
    `{"metric": m, "at": x}` (a floor the answer must clear), `{"metric": m, "below": x}`
    (a budget it must fit) or `{"metric": m, "within": f}` (a band around this run's own
    best, `f` a fraction). A document written by whoever owns the requirement can say the
    rule; nothing but the last stage's own results decide without one."""

    name: str
    command: tuple[str, ...] | None = None
    metrics_re: dict[str, str] = field(default_factory=dict)
    evaluator: str | None = None
    metrics: tuple[str, ...] = ()
    timeout_s: float = 600.0
    cutoff: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Objective:
    metric: str
    direction: str = "maximize"


@dataclass(frozen=True)
class TaskSpec:
    id: str
    statement: str
    contract: str = ""
    language: str = "text"
    extension: str = ".txt"
    parts: tuple[Part, ...] = ()
    decompose: bool = False              # "parts": "decompose" -- the orchestrator divides it
    max_parts: int = 8
    #: Sub-tasks, each run as its OWN LOOP with its own gate, stages and record (D455). A list
    #: of nested documents is the division the person writing the task wants; "decompose" asks
    #: the orchestrator for one. A child inherits what it does not say (contract, language,
    #: gate, stages, objectives, knowledge, params) and never inherits `subtasks`, so the
    #: nesting is as deep as the documents are, not unbounded.
    subtasks: tuple["TaskSpec", ...] = ()
    split: bool = False                  # "subtasks": "decompose"
    max_subtasks: int = 4
    #: WHO drafts (D456). Absent or "model": the model inner loop. `{"command": [...]}`: the
    #: command writes `{artifact}` and IS the generator -- a renderer, a script, a solver, with
    #: `{failure}` carrying why the last draft was refused. `{"catalog": [path, ...]}`: designs
    #: that already exist, tried in order. A document with a generator and no model role runs
    #: the whole loop with no model in it.
    generator: dict[str, Any] = field(default_factory=dict)
    #: WHO FILLS EACH ROLE (D460): `{"orchestrator": "rules"}`, `{"orchestrator": {"given":
    #: {"parts": [...]}}}`, ... -- one of `flux_loop.available_roles(role)` per role. The
    #: generation slot is the `generator` field above; saying it in both places is refused.
    roles: dict[str, Any] = field(default_factory=dict)
    brief: bool = False                  # "brief": "propose" -- the orchestrator briefs each part
    critique: bool = False               # "critique": "propose" -- a model critic judges (D433)
    gate: Gate = field(default_factory=Gate)
    stages: tuple[Stage, ...] = ()
    objectives: tuple[Objective, ...] = ()
    knowledge: str = ""
    joiner: str = "\n\n"                 # how admitted parts compose, in `parts` order
    budget: dict[str, Any] = field(default_factory=dict)      # LoopRequest overrides
    params: dict[str, Any] = field(default_factory=dict)      # the problem's own settings
    workload: Any = None                 # for evaluator stages: a Workload IR document or path

    # ---- the document
    @classmethod
    def from_dict(cls, doc: dict[str, Any]) -> "TaskSpec":
        if not isinstance(doc, dict):
            raise TaskError("a task is a JSON/YAML object")
        tid = doc.get("id")
        if not isinstance(tid, str) or not tid.strip():
            raise TaskError("`id` must be a non-empty string")
        statement = doc.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            raise TaskError("`statement` must be a non-empty string: what is to be made")
        parts = []
        decompose = doc.get("parts") == "decompose" or bool(doc.get("decompose"))
        listed = () if doc.get("parts") == "decompose" else (doc.get("parts") or ())
        if decompose and listed:
            raise TaskError("`parts` is either a list or \"decompose\", not both")
        for i, p in enumerate(listed):
            if isinstance(p, str):
                p = {"name": p}
            if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"]:
                raise TaskError(f"parts[{i}] needs a `name`")
            parts.append(Part(p["name"], str(p.get("statement") or "")))
        names = [p.name for p in parts]
        if len(set(names)) != len(names):
            raise TaskError(f"part names must be unique, got {names}")
        raw_subtasks = doc.get("subtasks")
        split = raw_subtasks == "decompose"
        if split and doc.get("parts") == "decompose":
            raise TaskError('`parts` and `subtasks` cannot both be "decompose": either the '
                            "orchestrator divides the task into parts of one artifact, or into "
                            "sub-tasks that are each their own loop")
        subtasks: list["TaskSpec"] = []
        if not split:
            for i, child in enumerate(raw_subtasks or ()):
                if not isinstance(child, dict):
                    raise TaskError(f"subtasks[{i}] is a task document (an object)")
                subtasks.append(cls.from_dict(_inherited(doc, child)))
        if len({c.id for c in subtasks}) != len(subtasks):
            raise TaskError(f"subtask ids must be unique, got {[c.id for c in subtasks]}")
        generator = doc.get("generator") or {}
        if isinstance(generator, str):
            if generator != "model":
                raise TaskError('`generator` as a string is only "model" (the default); a '
                                'generator that is not the model is an object with `command` '
                                'or `catalog`')
            generator = {}
        if not isinstance(generator, dict):
            raise TaskError("`generator` must be \"model\" or an object")
        if generator:
            kinds = [k for k in ("command", "catalog") if k in generator]
            if len(kinds) != 1:
                raise TaskError("`generator` needs exactly one of `command` (a renderer or "
                                f"script that writes the artifact) or `catalog` (designs that "
                                f"already exist), got {sorted(generator)}")
            value = generator[kinds[0]]
            if kinds[0] == "command":
                if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
                    raise TaskError("`generator.command` must be a list of strings")
            elif not isinstance(value, list) or not value or not all(isinstance(t, str)
                                                                     for t in value):
                raise TaskError("`generator.catalog` must be a non-empty list of paths")
        roles = doc.get("roles") or {}
        if not isinstance(roles, dict):
            raise TaskError('`roles` is an object: {"orchestrator": "rules", ...}')
        if roles:
            from .roles import ROLES, available_roles, make_role

            unknown = [r for r in roles if r not in ROLES]
            if unknown:
                raise TaskError(f"`roles` keys are the four roles ({', '.join(ROLES)}), "
                                f"not {', '.join(sorted(unknown))}")
            if "generator" in roles and generator:
                raise TaskError("the generation role is said twice: `generator` and "
                                "`roles.generator`. One of them, not both")
            for role, spec in roles.items():
                try:            # a name a document cannot mean is a LOAD error, not a
                    make_role(role, spec)   # surprise in the middle of a run
                except Exception as exc:  # noqa: BLE001
                    raise TaskError(f"roles.{role}: {exc} (available: "
                                    f"{', '.join(available_roles(role)) or 'none'})") from exc
        if (subtasks or split) and (parts or decompose):
            raise TaskError(
                "a task divides into `parts` of ONE artifact or into `subtasks` that are each "
                "their own loop, not both: with both, what the parent composes is ambiguous")
        gate = _gate(doc.get("gate"))
        stages = [_stage(i, r) for i, r in enumerate(doc.get("stages") or ())]
        if len({r.name for r in stages}) != len(stages):
            raise TaskError("stage names must be unique")
        objectives = []
        for i, o in enumerate(doc.get("objectives") or ()):
            if isinstance(o, str):
                o = {"metric": o}
            if not isinstance(o, dict) or not isinstance(o.get("metric"), str):
                raise TaskError(f"objectives[{i}] needs a `metric`")
            direction = str(o.get("direction") or "maximize")
            if direction not in _DIRECTIONS:
                raise TaskError(f"objectives[{i}].direction must be one of {_DIRECTIONS}, "
                                f"got {direction!r}")
            objectives.append(Objective(o["metric"], direction))
        budget = dict(doc.get("budget") or {})
        known = {f.name for f in fields(LoopRequest)} - {"db", "params"}
        bad = sorted(set(budget) - known)
        if bad:
            raise TaskError(f"budget keys {bad} are not loop knobs; known: {sorted(known)}")
        ext = str(doc.get("extension") or ".txt")
        return cls(
            id=tid.strip(), statement=statement.strip(), contract=str(doc.get("contract") or ""),
            language=str(doc.get("language") or "text"),
            extension=ext if ext.startswith(".") else "." + ext,
            parts=tuple(parts), decompose=decompose, max_parts=int(doc.get("max_parts") or 8),
            generator=dict(generator), roles=dict(roles),
            subtasks=tuple(subtasks), split=split,
            max_subtasks=int(doc.get("max_subtasks") or 4),
            brief=doc.get("brief") in ("propose", True),
            critique=doc.get("critique") in ("propose", True),
            gate=gate, stages=tuple(stages), objectives=tuple(objectives),
            knowledge=str(doc.get("knowledge") or ""), joiner=str(doc.get("joiner") or "\n\n"),
            budget=budget, params=dict(doc.get("params") or {}), workload=doc.get("workload"),
        )

    def to_dict(self) -> dict[str, Any]:
        gate = {k: (list(v) if isinstance(v, tuple) else v)
                for k, v in self.gate.__dict__.items() if v is not None}
        return {
            "id": self.id, "statement": self.statement, "contract": self.contract,
            "language": self.language, "extension": self.extension,
            "parts": ("decompose" if self.decompose
                      else [{"name": p.name, "statement": p.statement} for p in self.parts]),
            **({"max_parts": self.max_parts} if self.decompose else {}),
            **({"generator": dict(self.generator)} if self.generator else {}),
            **({"roles": dict(self.roles)} if self.roles else {}),
            **({"subtasks": "decompose", "max_subtasks": self.max_subtasks} if self.split else
               {"subtasks": [c.to_dict() for c in self.subtasks]} if self.subtasks else {}),
            **({"brief": "propose"} if self.brief else {}),
            **({"critique": "propose"} if self.critique else {}),
            "gate": gate,
            "stages": [{"name": r.name,
                       **({"command": list(r.command)} if r.command else {}),
                       **({"metrics_re": dict(r.metrics_re)} if r.metrics_re else {}),
                       **({"evaluator": r.evaluator} if r.evaluator else {}),
                     **({"cutoff": dict(r.cutoff)} if r.cutoff else {}),
                       **({"metrics": list(r.metrics)} if r.metrics else {}),
                       "timeout_s": r.timeout_s} for r in self.stages],
            "objectives": [{"metric": o.metric, "direction": o.direction} for o in self.objectives],
            "knowledge": self.knowledge, "joiner": self.joiner,
            "budget": dict(self.budget), "params": dict(self.params),
            **({"workload": self.workload} if self.workload is not None else {}),
        }

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, default=str)
                              .encode()).hexdigest()[:16]

    def commands(self) -> list[tuple[str, tuple[str, ...]]]:
        out: list[tuple[str, tuple[str, ...]]] = []
        if self.gate.build:
            out.append(("gate.build", self.gate.build))
        if self.gate.test:
            out.append(("gate.test", self.gate.test))
        if self.generator.get("command"):
            out.append(("generator", tuple(self.generator["command"])))
        for r in self.stages:
            if r.command:
                out.append((f"stage {r.name}", r.command))
        return out


def _gate(doc: Any) -> Gate:
    if not isinstance(doc, dict) or not (doc.get("build") or doc.get("test")):
        raise TaskError("`gate` needs a `build` and/or a `test` command (a list of argv "
                        "tokens; `{artifact}`, `{workdir}`, `{name}`, `{part}`, `{python}` "
                        "are substituted)")
    for key in ("build", "test"):
        cmd = doc.get(key)
        if cmd is not None and (not isinstance(cmd, list) or not cmd
                                or not all(isinstance(t, str) for t in cmd)):
            raise TaskError(f"gate.{key} must be a non-empty list of strings")
    for key in ("count_re", "fail_re"):
        pat = doc.get(key)
        if pat is not None:
            try:
                re.compile(pat)
            except re.error as exc:
                raise TaskError(f"gate.{key} is not a regex: {exc}") from exc
    return Gate(build=tuple(doc["build"]) if doc.get("build") else None,
                test=tuple(doc["test"]) if doc.get("test") else None,
                count_re=doc.get("count_re"), fail_re=doc.get("fail_re"),
                timeout_s=float(doc.get("timeout_s") or 120.0))


#: What a nested sub-task takes from its parent when it does not say (D455). `subtasks` is
#: deliberately absent: a child that inherited it would divide again, forever.
_INHERITED = ("contract", "language", "extension", "gate", "stages", "objectives", "knowledge",
              "params", "workload", "joiner", "budget", "brief", "critique", "generator",
              "roles")


def _inherited(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    out = dict(child)
    for key in _INHERITED:
        if key not in out and key in parent:
            out[key] = parent[key]
    return out


def _rig_for(task: TaskSpec, caller: "Roles | None") -> "Roles":
    """The task's own roles, overlaid by the caller's (D460). A caller's filled slot wins; its
    empty ones leave the document's alone, so `--role orchestrator=rules` switches exactly the
    one role it names."""
    from .roles import ROLES, Roles, rig

    out = rig(**dict(task.roles)) if task.roles else Roles()
    if caller is None:
        return out
    for role in ROLES:
        who = getattr(caller, role)
        if who is not None:
            out = out.with_role(role, who)
    return out


def _leaf(task_id: str) -> str:
    """A sub-task's work-item name: the last segment of its id, which is what a parent's
    orchestrator chooses from and what the record calls it."""
    return task_id.rsplit("/", 1)[-1]


def _stage(i: int, doc: Any) -> Stage:
    if not isinstance(doc, dict) or not isinstance(doc.get("name"), str) or not doc["name"]:
        raise TaskError(f"stages[{i}] needs a `name`")
    cmd, ev = doc.get("command"), doc.get("evaluator")
    if bool(cmd) == bool(ev):
        raise TaskError(f"stages[{i}] ({doc['name']}) needs exactly one of `command` or `evaluator`")
    if cmd is not None and (not isinstance(cmd, list) or not all(isinstance(t, str) for t in cmd)):
        raise TaskError(f"stages[{i}].command must be a list of strings")
    metrics_re = dict(doc.get("metrics_re") or {})
    for m, pat in metrics_re.items():
        try:
            if re.compile(pat).groups < 1:
                raise TaskError(f"stages[{i}].metrics_re[{m!r}] needs one capturing group")
        except re.error as exc:
            raise TaskError(f"stages[{i}].metrics_re[{m!r}] is not a regex: {exc}") from exc
    if cmd and not metrics_re:
        raise TaskError(f"stages[{i}] ({doc['name']}): a command stage needs `metrics_re`")
    cutoff = dict(doc.get("cutoff") or {})
    if cutoff:
        if not isinstance(cutoff.get("metric"), str):
            raise TaskError(f"stages[{i}].cutoff needs a `metric` naming one this stage measures")
        rules = [k for k in ("at", "below", "within") if k in cutoff]
        if len(rules) != 1:
            raise TaskError(
                f"stages[{i}].cutoff needs exactly one of `at` (a floor), `below` (a budget) or "
                f"`within` (a fraction of this run's best), got {sorted(cutoff)}")
        if not isinstance(cutoff[rules[0]], (int, float)) or isinstance(cutoff[rules[0]], bool):
            raise TaskError(f"stages[{i}].cutoff.{rules[0]} must be a number")
        if rules[0] == "within" and not 0 < float(cutoff["within"]) <= 1:
            raise TaskError(f"stages[{i}].cutoff.within must be a fraction in (0, 1]")
    return Stage(name=doc["name"], command=tuple(cmd) if cmd else None, metrics_re=metrics_re,
                evaluator=ev, metrics=tuple(doc.get("metrics") or ()),
                timeout_s=float(doc.get("timeout_s") or 600.0), cutoff=cutoff)


def load_task(path: str | Path) -> TaskSpec:
    """A task from a `.json` / `.yaml` / `.yml` file."""
    p = Path(path)
    if p.suffix not in (".json", ".yaml", ".yml"):
        raise TaskError(f"{p}: a task file is .json, .yaml or .yml")
    text = p.read_text()
    if p.suffix == ".json":
        doc = json.loads(text)
    else:
        import yaml

        doc = yaml.safe_load(text)
    return TaskSpec.from_dict(doc)


def request_for(task: TaskSpec, **overrides: Any) -> LoopRequest:
    """The loop's knobs for this task: the document's `budget`, then the caller's."""
    params = {"task": task.id, **task.params, **(overrides.pop("params", None) or {})}
    return LoopRequest(**{**task.budget, **overrides}, params=params)


def _substitute(cmd: Iterable[str], subs: dict[str, str]) -> list[str]:
    return [_PLACEHOLDER.sub(lambda m: subs.get(m.group(1), m.group(0)), tok) for tok in cmd]


# ------------------------------------------------------------------ the problem
class PromptProblem(Problem):
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

    def roles(self) -> "Roles":
        """Who fills each of the four roles for this task (D460): the document's `roles`, with
        the caller's own overriding it slot by slot -- which is how a command line switches one
        role of a document it did not write."""
        return self._roles

    # ---- mentor
    def objective(self, request: LoopRequest) -> dict[str, Any]:
        return {"study": self.task.id, "task": self.task.digest, **request.params}

    def validate(self, request: LoopRequest) -> list[str]:
        """Is this document answerable as written (D463)? An objective naming a
        metric no stage produces has nothing to rank by, and a cutoff on a metric its own
        stage does not measure cuts nothing -- both are typos that would otherwise run
        the whole task and decide on nothing.

        A stage whose metrics are not declared (an `evaluator` stage without `metrics`)
        could produce anything, so its presence silences the check rather than failing
        it: refusing what might be right is worse than not checking."""
        wrong: list[str] = []
        unknown = any(r.evaluator and not r.metrics for r in self.task.stages)
        produced = {m for r in self.task.stages for m in (*r.metrics_re, *r.metrics)}
        if self.task.stages and not unknown:
            for objective in self.task.objectives:
                if objective.metric not in produced:
                    wrong.append(
                        f"objective {objective.metric!r} names a metric no stage "
                        f"measures (this task measures: "
                        f"{', '.join(sorted(produced)) or 'nothing'})")
        for stage in self.task.stages:
            metric = stage.cutoff.get("metric")
            mine = {*stage.metrics_re, *stage.metrics}
            if metric and mine and metric not in mine:
                wrong.append(f"the {stage.name} stage cuts on {metric!r}, which it does "
                             f"not measure (it measures: {', '.join(sorted(mine))})")
        return wrong

    def tools_missing(self) -> list[str]:
        missing: list[str] = []
        for _label, cmd in self.task.commands():
            head = _substitute(cmd[:1], {"python": sys.executable})[0]
            if head in ("{artifact}", "{workdir}", "{name}", "{part}"):
                continue
            found = Path(head).exists() if "/" in head else shutil.which(head) is not None
            if not found and head not in missing:
                missing.append(head)
        return missing

    def mentor_sections(self, state: LoopState) -> list[tuple[str, str]]:
        out = [("task", self.task.statement + ("\n\n" + self.task.contract if self.task.contract else ""))]
        if self.task.knowledge:
            out.append(("knowledge", self.task.knowledge))
        mentor = self.knowledge()
        if mentor is not None:                  # D462: what the knowledge role holds
            out.extend(mentor.sections(state))
        return out

    def _role_knowledge(self, state: LoopState) -> str:
        """The knowledge role's own text for a prompt (D462), beside the document's: the
        document's `knowledge` field is the human half, and a `mined` source is the other one.
        Knowledge is help, never a gate, so a source that cannot be read contributes nothing."""
        mentor = self.knowledge()
        if mentor is None:
            return ""
        try:
            return mentor.prefix(state).strip()
        except Exception:  # noqa: BLE001
            return ""

    # ---- orchestrator
    def subgoals(self) -> list[str]:
        return [p.name for p in self.parts]

    def decompose(self, state: LoopState,
                  critique: str | None = None) -> list[str | SubLoop]:
        """propose: decompose (D431/D455). Sub-tasks are the answer when the document has them
        (each its own loop); otherwise a listed `parts` is, and "decompose" asks the
        model for one -- named parts with a one-line statement each, checked (unique
        identifier names, 1..max_parts) -- remembered in the record so a resume works on
        the same parts, and refused loudly when there is no model to ask. With a
        `critique` (D433) the previous division is shown with the objection and a new
        one is asked for; only the division that stands is remembered."""
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
        reply = _ask(state, prompt, schema)
        doc = _json(reply)
        why = self._check_decomposition(doc)
        if why:
            raise RuntimeError(f"task {t.id}: the decomposition was refused: {why}")
        self.parts = tuple(Part(str(p["name"]).strip(), str(p.get("statement") or "").strip())
                           for p in doc["parts"])
        self._division_why = str(doc.get("why") or "")[:200]
        # remembered when it stands: at once without a critic, at once when it answers a
        # critique (a later round may replace it; the last remembered wins on resume),
        # and on the critic's acceptance otherwise
        if critique is not None or not (t.critique and state.request.critique_rounds > 0):
            self._remember_division(state)
        state.say(f"decompose: {len(self.parts)} part(s): " + ", ".join(p.name for p in self.parts))
        return [p.name for p in self.parts]

    def _remember_division(self, state: LoopState) -> None:
        if state.records is not None:
            state.records.remember("decomposition", {
                "parts": [{"name": p.name, "statement": p.statement} for p in self.parts],
                "why": getattr(self, "_division_why", "")})

    def critique(self, kind: str, subject: Any, state: LoopState) -> Verdict:
        """critique (D433): with `"critique": "propose"` the model is the adversary. It sees
        the task, the contract, how the gate judges, and the subject -- a division, a
        candidate the gate passed, or the decision -- and answers with issues or none.
        Its verdict is remembered; a division it accepts is remembered as the division.
        No model, no critic."""
        t = self.task
        if not t.critique or state.proposer is None:
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
                    "Object only to something the gate cannot see and the contract requires: a "
                    "violated constraint, a fragile construction, a mismatch with the statement. "
                    "Passing the gate is not an issue.")
            label = cand.name
        else:
            pick: Scored = subject
            metrics = ", ".join(f"{k}={v:g}" for k, v in pick.metrics.items())
            what = (f"THE DECISION: {pick.name} on stage {pick.stage} with {metrics}. "
                    "Object only if the objectives or the statement point elsewhere.")
            label = pick.name
        gate = " ".join(t.gate.test or t.gate.build or ())
        prompt = "\n\n".join(x for x in (
            f"TASK {t.id}: {t.statement}",
            f"CONTRACT:\n{t.contract}" if t.contract else "",
            f"HOW IT IS JUDGED: `{gate}`; zero failures admits." if gate else "",
            "You are the critic. Your job is to find what is WRONG, precisely and briefly; a "
            "verdict without a concrete issue is worthless, and so is an issue the gate already "
            "covers.",
            what,
            'Reply with ONLY JSON: {"ok": true|false, "issues": ["<one concrete issue each>"], '
            '"why": "<one line>"}') if x)
        schema = {"type": "object",
                  "properties": {"ok": {"type": "boolean"},
                                 "issues": {"type": "array", "items": {"type": "string"}},
                                 "why": {"type": "string"}},
                  "required": ["ok"]}
        try:
            doc = _json(_ask(state, prompt, schema))
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

    def plan_part(self, subgoal: str | None, state: LoopState) -> dict[str, Any]:
        """propose: brief (D432). With `"brief": "propose"` the orchestrator's model writes
        the part's brief -- what to make, what to watch, how it is judged -- and may set
        the part's repair budget (clamped to twice the request's); remembered in the
        record so a resume reuses it; empty when there is no model (help, not a gate)."""
        t = self.task
        if not t.brief:
            return {}
        key = subgoal or "*"
        records = state.records
        earlier = [d for d in (records.recall("brief") if records is not None else [])
                   if d.get("part") == key]
        if earlier:
            state.say(f"brief for {subgoal or t.id}: resumed from the record")
            return {k: v for k, v in earlier[-1].items() if k in ("brief", "repair_attempts")}
        if state.proposer is None:
            return {}
        from .model import _ask, _json

        part = self._part(subgoal)
        gate = " ".join(t.gate.test or t.gate.build or ())
        prompt = "\n\n".join(x for x in (
            f"TASK {t.id}: {t.statement}",
            f"PART {part.name}: {part.statement}" if part is not None else "",
            f"CONTRACT:\n{t.contract}" if t.contract else "",
            f"KNOWLEDGE:\n{t.knowledge}" if t.knowledge else "",
            f"HOW IT IS JUDGED: the gate runs `{gate}` and counts failures; zero admits." if gate else "",
            "You are briefing the writer of this part. Write a BRIEF of at most 12 lines: exactly "
            "what the part must contain, the constraints most likely to be missed, what the gate "
            "will check, and the smallest correct approach. Do not write the part itself. Then "
            f"say how many repair attempts it deserves (1 to {2 * state.request.repair_attempts}; "
            f"{state.request.repair_attempts} is the default).",
            'Reply with ONLY JSON: {"brief": "<text>", "repair_attempts": <int>, "why": "<one line>"}',
        ) if x)
        schema = {"type": "object",
                  "properties": {"brief": {"type": "string"},
                                 "repair_attempts": {"type": "integer", "minimum": 1},
                                 "why": {"type": "string"}},
                  "required": ["brief"]}
        doc = _json(_ask(state, prompt, schema))
        if not isinstance(doc, dict) or not isinstance(doc.get("brief"), str) or not doc["brief"].strip():
            state.say(f"brief for {subgoal or t.id}: the reply carried no brief; the statement is the brief")
            return {}
        plan: dict[str, Any] = {"brief": doc["brief"].strip()[:2000]}
        ra = doc.get("repair_attempts")
        if isinstance(ra, int) and ra > 0:
            plan["repair_attempts"] = max(1, min(ra, 2 * state.request.repair_attempts))
        if records is not None:
            records.remember("brief", {"part": key, **plan, "why": str(doc.get("why") or "")[:200]})
        state.say(f"brief for {subgoal or t.id}: {len(plan['brief'].splitlines())} line(s)"
                  + (f", {plan['repair_attempts']} repair attempts" if "repair_attempts" in plan else ""))
        return plan

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

    def frontier_axes(self) -> tuple[Callable[[Scored], float], Callable[[Scored], float]] | None:
        objs = self.task.objectives
        if len(objs) < 2:
            return None
        first, second = objs[0], objs[1]

        def better(p: Scored) -> float:
            v = p.metrics.get(first.metric, float("nan"))
            return v if first.direction == "maximize" else -v

        def cost(p: Scored) -> float:
            v = p.metrics.get(second.metric, float("nan"))
            return v if second.direction == "minimize" else -v

        return better, cost

    def decide(self, pool: list[Scored], state: LoopState) -> tuple[Scored | None, str]:
        objs = self.task.objectives
        if len(objs) == 1 and pool:
            o = objs[0]
            pick = (max if o.direction == "maximize" else min)(
                pool, key=lambda p: p.metrics.get(o.metric, float("-inf") if o.direction == "maximize" else float("inf")))
            return pick, f"the {'largest' if o.direction == 'maximize' else 'smallest'} {o.metric}"
        return super().decide(pool, state)

    # ---- generator
    def _part(self, subgoal: str | None) -> Part | None:
        return next((p for p in self.parts if p.name == subgoal), None)

    def prompt_prefix(self, subgoal: str | None, state: LoopState) -> str:
        t = self.task
        lines = [f"TASK {t.id}: {t.statement}"]
        part = self._part(subgoal)
        if part is not None:
            lines.append(f"PART {part.name}" + (f": {part.statement}" if part.statement else "")
                         + " -- this turn is about this part only.")
        if t.contract:
            lines.append(f"CONTRACT:\n{t.contract}")
        if t.knowledge:
            lines.append(f"KNOWLEDGE:\n{t.knowledge}")
        role = self._role_knowledge(state)      # D462: the mined half, if it is switched on
        if role:
            lines.append(role)
        brief = (state.plans.get(subgoal or "*") or {}).get("brief")
        if brief:
            lines.append(f"BRIEF (from the orchestrator):\n{brief}")
        lines.append(
            f'REPLY SHAPE: reply with ONLY JSON: {{"artifact": "<the complete {t.language} '
            'text>", "why": "<one line>"}. When asked for edits, reply {"edits": [{"find": '
            '"...", "replace": "..."}], "why": "..."} instead.')
        return "\n\n".join(lines)

    def design_prompt(self, subgoal: str | None, method: str, state: LoopState,
                      human: str | None, prior: Candidate | None, prior_why: str
                      ) -> tuple[str, dict | None]:
        target = f"part {subgoal}" if subgoal else f"task {self.task.id}"
        parts = [human or ""]
        if prior is not None:
            numbered = "\n".join(f"{i + 1:4d} | {ln}" for i, ln in enumerate(prior.artifact.splitlines()))
            parts += [f"Your previous attempt for {target} was refused:\n\n{prior_why}",
                      "Rework it, or send a new one if the approach itself is wrong.",
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
        return {"artifact": str(path), "workdir": str(workdir), "name": cand.name,
                "part": subgoal or "", "python": sys.executable}

    def _run(self, cmd: tuple[str, ...], subs: dict[str, str], timeout_s: float, what: str):
        from flux_evaluator_abi.tools import run_tool

        return run_tool(_substitute(cmd, subs), cwd=subs["workdir"], timeout_s=timeout_s, what=what)

    def _count_failures(self, run: Any) -> tuple[int, str]:
        out = (run.stdout or "") + ("\n" + run.stderr if run.stderr else "")
        g = self.task.gate
        fails: int | None = None
        if g.count_re:
            m = re.search(g.count_re, out)
            fails = int(m.group(1)) if m else None
        elif g.fail_re:
            fails = len(re.findall(g.fail_re, out))
            if fails == 0 and not run.ok:
                fails = None
        if fails is None:
            fails = 0 if run.ok else 1
        text = out.strip()[-4000:] or (f"exit {run.returncode}" if not run.ok else "")
        return fails, text

    def build(self, cand: Candidate, subgoal: str | None, state: LoopState) -> Any:
        subs = self._subs(cand, subgoal, state)
        if self.task.gate.build:
            run = self._run(self.task.gate.build, subs, self.task.gate.timeout_s, "build")
            if not run.ok:
                raise BuildError(((run.stdout or "") + "\n" + (run.stderr or "")).strip()[-4000:]
                                 or f"build exited {run.returncode}")
        return subs["artifact"]

    def fast_check(self, built: Any, cand: Candidate, subgoal: str | None,
                   state: LoopState) -> tuple[int, str]:
        if not self.task.gate.test:
            return 0, ""
        subs = self._subs(cand, subgoal, state)
        return self._count_failures(self._run(self.task.gate.test, subs, self.task.gate.timeout_s, "test"))

    def judge(self, built: Any, cand: Candidate, subgoal: str | None, state: LoopState) -> Verdict:
        fails, text = self.fast_check(built, cand, subgoal, state)
        return Verdict(fails == 0, float(fails), text if fails else "", {"failures": fails})

    # ------------------------------------------------------------ who drafts (D456)
    def generator(self, subgoal: str | None, state: LoopState):
        """WHO drafts, from the document: the model unless it names a command (a renderer, a
        script, a solver) or a catalog of designs that already exist. The generate/build/
        fast-check sub-loop around it is the loop's either way."""
        from .sources import Catalog, Template

        chosen = self.roles().generator
        if chosen is not None:
            return chosen                 # a rig (a flag, a caller) named the generator
        spec = self.task.generator
        if not spec:
            return None
        if "catalog" in spec:
            return Catalog(list(spec["catalog"]), self._from_catalog)
        command = tuple(spec["command"])
        return Template(lambda attempt: self._rendered(attempt, command))

    def _from_catalog(self, item: Any, attempt: Any):
        """One catalog entry as a candidate: a path whose text is the design."""
        from dataclasses import replace

        from .sources import from_file

        self._count += 1
        cand, why = from_file(item, attempt,
                              name=f"{_leaf(self.task.id)}#{self._count}")
        if cand is None:
            return None, why
        return replace(cand, knobs={**cand.knobs, "task": self.task.id,
                                                "part": attempt.subgoal or ""}), ""

    def _rendered(self, attempt: Any, command: tuple[str, ...]):
        """The document's generator command IS the generator: it writes `{artifact}` and is
        told `{failure}` (why the last draft was refused) and `{attempt}`, so a script that
        can do better on a second try has what it needs."""
        state = attempt.state
        workdir = Path(state.workdir or ".")
        self._count += 1
        name = f"{attempt.subgoal or _leaf(self.task.id)}#{self._count}"
        path = workdir / f"draft-{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}{self.task.extension}"
        subs = {"artifact": str(path), "workdir": str(workdir), "name": name,
                "part": attempt.subgoal or "", "python": sys.executable,
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

    def subproblems(self, state: LoopState) -> list[SubLoop]:
        """The document's sub-tasks, each its own loop (D455).

        Declared, or asked for: a list of nested documents is the division whoever wrote the
        task wanted, and `"subtasks": "decompose"` asks the orchestrator for one -- children
        that inherit this task's contract, gate, stages and objectives and differ in their
        statement, which is what a sub-task IS. An asked-for division is remembered in the
        record, so a resumed run works on the same sub-tasks rather than a new split.
        """
        t = self.task
        if t.subtasks:
            return [SubLoop(name=_leaf(c.id), problem=PromptProblem(c), statement=c.statement)
                    for c in t.subtasks]
        if not t.split:
            return []
        if self._children is None:
            self._children = self._ask_for_subtasks(state)
        return [SubLoop(name=_leaf(c.id), problem=PromptProblem(c), statement=c.statement)
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
        doc = _json(_ask(state, prompt, schema))
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
            # what the sub-loops DECIDED, joined in their declared order (D455): each child
            # answered its own problem, and the parent's artifact is the answers together.
            names = [_leaf(c.id) for c in (self.task.subtasks or self._children or ())]
            ordered = [admitted[n] for n in names if n in admitted]
            if not ordered or len(ordered) != len(names):
                return None
            return Candidate(self.task.id, self.task.joiner.join(c.artifact for c in ordered),
                             knobs={"task": self.task.id, "subtasks": names})
        if not self.parts:
            return super().compose(admitted, state)
        ordered = [admitted[p.name] for p in self.parts if p.name in admitted]
        if len(ordered) != len(self.parts):
            return None
        return Candidate(self.task.id, self.task.joiner.join(c.artifact for c in ordered),
                         knobs={"task": self.task.id, "parts": [c.name for c in ordered]})

    def cutoff(self, stage: str, scored, state):
        """The stage's declared cutoff (D454), applied: a floor, a budget or a band around this
        run's own best. No `cutoff` in the document means every measured candidate may climb."""
        from .cutoff import above, below, within_best

        mine = self.role_cutoff(stage, scored, state)
        if mine is not None:
            return mine                       # the evaluation component's own stage (D461)
        spec = next((r for r in self.task.stages if r.name == stage), None)
        rule = dict(spec.cutoff) if spec is not None else {}
        metric = rule.get("metric")
        if not metric:
            return list(scored)
        if "at" in rule:
            return above(scored, metric, float(rule["at"]))
        if "below" in rule:
            return below(scored, metric, float(rule["below"]))
        direction = {o.metric: o.direction for o in self.task.objectives}.get(metric, "maximize")
        return within_best(scored, metric, float(rule["within"]),
                           higher_is_better=direction != "minimize")

    def stages(self) -> list[str]:
        """The document's stages, plus whatever the evaluation component adds below them
        (D461: a learned screen)."""
        return self.chained([r.name for r in self.task.stages] or [StageNames.GATE])

    def measure(self, cand: Candidate, stage: str, state: LoopState) -> dict[str, float] | None:
        predicted = self.role_measure(cand, stage, state)
        if predicted is not None:
            return predicted                  # the evaluation component's own stage (D461)
        spec = next((r for r in self.task.stages if r.name == stage), None)
        if spec is None:
            return {"failures": 0.0} if stage == StageNames.GATE else None
        if spec.command:
            subs = self._subs(cand, None, state)
            run = self._run(spec.command, subs, spec.timeout_s, f"stage {stage}")
            out = (run.stdout or "") + "\n" + (run.stderr or "")
            got: dict[str, float] = {}
            for metric, pat in spec.metrics_re.items():
                m = re.search(pat, out)
                if m:
                    try:
                        got[metric] = float(m.group(1))
                    except ValueError:
                        pass
            if not got:
                state.say(f"  stage {stage}: no metric matched in the output")
                return None
            return got
        try:
            from flux_evaluator_abi import Budget, Candidate as AbiCandidate, make_evaluator

            ev = make_evaluator(spec.evaluator or "")
            arch = _document(cand.artifact)
            workload = self.task.workload
            if isinstance(workload, str) and Path(workload).exists():
                workload = _document(Path(workload).read_text())
            result = ev.evaluate(AbiCandidate(workload=workload, arch=arch), Budget(),
                                 frozenset(spec.metrics) if spec.metrics else frozenset())
            return {k: float(v.value) for k, v in result.metrics.items()
                    if not spec.metrics or k in spec.metrics}
        except Exception as exc:  # noqa: BLE001
            state.say(f"  stage {stage} ({spec.evaluator}) could not measure: {exc!s:.120}")
            return None

    def cache_suffix(self) -> str:
        return f"{self.task.id}.json"


def _document(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        import yaml

        return yaml.safe_load(text)


# ------------------------------------------------------------------ the report
def task_report_lines(task: TaskSpec, out: Any) -> list[str]:
    """The standard report for a task run: the decision and its metrics, the frontier,
    then the shared closing sections (`flux_report`)."""
    lines = [f"TASK {task.id}: {task.statement[:100]}"]
    d = out.decision
    if d is not None:
        metrics = ", ".join(f"{k}={v:g}" for k, v in d.metrics.items())
        lines.append(f"  DECISION {d.name} [{d.stage}; {out.decided_by}]" + (f": {metrics}" if metrics else ""))
    else:
        lines.append("  NO CANDIDATE SURVIVED -- see NOT ESTABLISHED below")
    pool = out.confirmed or out.frontier
    if len(pool) > 1:
        lines.append(f"  frontier ({len(pool)} point(s)):")
        for p in pool:
            lines.append("    " + p.name + ": " + ", ".join(f"{k}={v:g}" for k, v in p.metrics.items()))
    if out.admitted:
        lines.append("  proven: " + ", ".join(f"{k}={c.name}" for k, c in sorted(out.admitted.items())))
    try:
        from flux_report import established, not_established, notes, refused

        for block in (established(out.lessons), not_established(out.not_established),
                      refused(out.refused, render=lambda r: f"{r[0]}: {r[1]}"), notes(out.notes)):
            if block:
                lines += [""] + list(block)
    except Exception:  # noqa: BLE001
        lines += [f"  {ln}" for ln in out.lessons]
    return lines
