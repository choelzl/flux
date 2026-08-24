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

A WORLD (D519, review step 5.4): what a document cannot say in prose or numbers -- a
toolkit of blocks, a transpiler, an exhaustive judge, how parts compose, how a design is
measured -- it names ONCE, `world: package.module:World`, a callable that takes the
problem and returns an object whose methods are `Problem` hooks; the document problem
binds every hook the world has and keeps its own for the rest. `hooks: {judge:
module:callable}` replaces one hook with a callable that takes the problem first. What
the document still owns: the parts and their order, the objectives, the ladder, the
stages (a stage with no command and no evaluator is the world's to measure; `needs:`
names the tools it wants on PATH, and it is skipped without them), the knowledge (a
`sheet:` file read beside the document), the budget, the `campaign:` identity the record
is opened under, and the `params:` the world is built from. A new test, problem or
design-space exploration in a world that exists is a document, not an application.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from .model import _json
from .problem import Problem
from .objective import Objective, Objectives
from .types import (BuildError, Candidate, LoopRequest, LoopState, StageNames, Scored,
                    SubLoop, Verdict)

if TYPE_CHECKING:  # pragma: no cover
    from .roles import Roles

__all__ = ["Gate", "Objective", "Part", "PromptProblem", "Stage", "TaskError", "TaskSpec",
           "load_task", "request_for", "resolve", "task_report_lines", "world_hooks"]

_PLACEHOLDER = re.compile(r"\{(artifact|workdir|name|part|python|home)\}")


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
    needs: tuple[str, ...] = ()          # tools on PATH the stage wants; absent, the stage is skipped (D519)


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
    #: The DESIGN SPACE (D553): knob -> its choices in a meaningful order, what a `flow.dse`
    #: policy searches; a world may compute its own instead (`space(state)`).
    space: dict[str, list] = field(default_factory=dict)
    workload: Any = None                 # for evaluator stages: a Workload IR document or path
    # D519: the world the document runs in, and what it says that a `Problem` used to code
    world: str = ""                      # "package.module:World" -- (problem) -> an object of hooks
    #: The FLOW (D542): one key per box of the drawing naming its half -- what the document
    #: says, normalised; `roles`, `generator`, `critique`, `budget.calibrate` carry what it
    #: implies for the loop, so the rig underneath is unchanged. `describe_flow` reads it back.
    flow: dict[str, Any] = field(default_factory=dict)
    #: The measurement cache sidecar beside the record (D541): `true` = `<id>.json`, `false` =
    #: none (a world that measures in microseconds, or owns its caching), a string = that name.
    cache: bool | str = True
    hooks: dict[str, str] = field(default_factory=dict)       # hook name -> "module:callable" (problem, ...)
    campaign: dict[str, Any] = field(default_factory=dict)    # the record's identity; empty = the digest
    ladder: Any = None                   # True, or the `flux_loop.Ladder` fields; None = no ladder
    knowledge_sheet: str = ""            # where `knowledge` was read from, for the report
    #: D578: the directory the document was loaded from ("" for an inline document): every
    #: artifact of a run lives under `<home>/out/` -- the record, the decided artifact, the
    #: cache sidecar, a world's kept inventions -- never beside the source it was made from.
    home: str = field(default="", compare=False)      # not the document's: two loads of one text are equal

    def out_dir(self) -> Path:
        """Where a run of this document writes: `<home>/out/`, made on first use."""
        p = Path(self.home or ".") / "out"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ---- the document
    @classmethod
    def from_dict(cls, doc: dict[str, Any], base: str | Path | None = None) -> "TaskSpec":
        """`base` is the directory a `knowledge: {sheet: ...}` path is read beside (the
        document's own, when it was loaded from a file)."""
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
                subtasks.append(cls.from_dict(_inherited(doc, child), base))
        if len({c.id for c in subtasks}) != len(subtasks):
            raise TaskError(f"subtask ids must be unique, got {[c.id for c in subtasks]}")
        flow, doc = _flow(doc)             # D542: the drawing's boxes, folded into the fields below
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
            kinds = [k for k in ("command", "catalog", "agent") if k in generator]
            if len(kinds) != 1:
                raise TaskError("`generator` needs exactly one of `command` (a renderer or "
                                f"script that writes the artifact), `catalog` (designs that "
                                f"already exist) or `agent` (a coding agent, D575), got {sorted(generator)}")
            value = generator[kinds[0]]
            if kinds[0] == "command":
                if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
                    raise TaskError("`generator.command` must be a list of strings")
            elif kinds[0] == "agent":
                from .agent import agent_argv

                try:
                    agent_argv(value)
                except ValueError as exc:
                    raise TaskError(f"generator.agent: {exc}") from exc
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
        world = str(doc.get("world") or "")
        cache = doc.get("cache", True)
        if not isinstance(cache, (bool, str)) or cache == "":
            raise TaskError("`cache` is true (the loop's sidecar), false (none) or a file name")
        if world and ":" not in world:
            raise TaskError('`world` is "package.module:callable" -- what takes the problem and returns '
                            "the object whose methods are its hooks")
        hooks = dict(doc.get("hooks") or {})
        allowed = set(world_hooks()) | {"prototype", "tools_missing"}
        bad_hooks = sorted(h for h in hooks if h not in allowed)
        if bad_hooks:
            raise TaskError(f"hooks {bad_hooks} are not loop hooks a document may replace; known: "
                            + ", ".join(sorted(allowed)))
        for h, spec in hooks.items():
            if not isinstance(spec, str) or ":" not in spec:
                raise TaskError(f'hooks.{h} is "module:callable"')
        campaign = doc.get("campaign") or {}
        if not isinstance(campaign, dict):
            raise TaskError("`campaign` is an object: the identity the record is opened under")
        ladder = doc.get("ladder")
        if isinstance(ladder, dict):
            from .ladder import Ladder

            known_ladder = {f.name for f in fields(Ladder)}
            bad_ladder = sorted(set(ladder) - known_ladder)
            if bad_ladder:
                raise TaskError(f"ladder keys {bad_ladder} are not the ladder's; known: {sorted(known_ladder)}")
        elif ladder not in (None, True, False):
            raise TaskError("`ladder` is true (the default ladder) or an object of its fields")
        knowledge, sheet = doc.get("knowledge") or "", ""
        if isinstance(knowledge, dict):
            sheet = str(knowledge.get("sheet") or "")
            text = str(knowledge.get("text") or "")
            if sheet:
                path = Path(sheet) if Path(sheet).is_absolute() or base is None else Path(base) / sheet
                if not path.exists():
                    raise TaskError(f"knowledge.sheet {sheet!r} is not a file"
                                    + (f" beside {base}" if base is not None else ""))
                text = (text + "\n\n" if text else "") + path.read_text()
            knowledge = text
        gate = _gate(doc.get("gate")) if doc.get("gate") or not world else Gate()
        stages = [_stage(i, r, world=bool(world)) for i, r in enumerate(doc.get("stages") or ())]
        if len({r.name for r in stages}) != len(stages):
            raise TaskError("stage names must be unique")
        try:
            objectives = list(Objectives.from_doc(doc.get("objectives") or ()))
        except ValueError as exc:
            raise TaskError(str(exc)) from exc
        budget = dict(doc.get("budget") or {})
        if flow:
            declared = [r.name for r in stages]
            for box in ("analytical", "simulation"):
                for name in flow.get(box) or ():
                    if name not in declared:
                        raise TaskError(f"flow.{box} names stage {name!r}, which `stages` does not declare")
            both = set(flow.get("analytical") or ()) & set(flow.get("simulation") or ())
            if both:
                raise TaskError(f"flow: a stage is analytical or simulation, not both: {sorted(both)}")
            if flow.get("calibrate") == "off":
                if "calibrate" in budget:
                    raise TaskError("calibration is said twice: `flow.calibrate` and `budget.calibrate`")
                budget["calibrate"] = False
            if "sheet" in (flow.get("knowledge") or ()) and not sheet:
                raise TaskError("flow.knowledge names `sheet` but `knowledge: {sheet: file}` names none")
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
            generator=dict(generator), roles=dict(roles), flow=dict(flow),
            subtasks=tuple(subtasks), split=split,
            max_subtasks=int(doc.get("max_subtasks") or 4),
            brief=doc.get("brief") in ("propose", True),
            critique=doc.get("critique") in ("propose", True),
            gate=gate, stages=tuple(stages), objectives=tuple(objectives),
            knowledge=str(knowledge), joiner=str(doc.get("joiner") or "\n\n"),
            budget=budget, params=dict(doc.get("params") or {}), space=_space(doc.get("space")),
            workload=doc.get("workload"), home=str(Path(base).resolve()) if base is not None else "",
            world=world, cache=cache, hooks=hooks, campaign=dict(campaign), ladder=ladder if ladder else None,
            knowledge_sheet=sheet,
        )

    def _to_dict(self) -> dict[str, Any]:
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
            **({"flow": dict(self.flow)} if self.flow else {}),
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
                       **({"needs": list(r.needs)} if r.needs else {}),
                       "timeout_s": r.timeout_s} for r in self.stages],
            "objectives": [o.to_doc() for o in self.objectives],
            "knowledge": self.knowledge, "joiner": self.joiner,
            "budget": dict(self.budget), "params": dict(self.params), "space": dict(self.space),
            **({"workload": self.workload} if self.workload is not None else {}),
            **({"world": self.world} if self.world else {}),
            **({"cache": self.cache} if self.cache is not True else {}),
            **({"hooks": dict(self.hooks)} if self.hooks else {}),
            **({"campaign": dict(self.campaign)} if self.campaign else {}),
            **({"ladder": self.ladder} if self.ladder else {}),
        }

    def to_dict(self) -> dict[str, Any]:
        """The document, round-trippable: what `flow:` said is emitted under `flow:` alone,
        never also as the field it folded into (a load refuses a thing said twice, D542)."""
        out = self._to_dict()
        flow = self.flow
        if not flow:
            return out
        if "critique" in flow:
            out.pop("critique", None)
        if "generate" in flow:
            out.pop("generator", None)
        roles = dict(out.get("roles") or {})
        if "orchestrate" in flow or "dse" in flow:
            roles.pop("orchestrator", None)
        if "mined" in (flow.get("knowledge") or ()):
            roles.pop("knowledge", None)
        if roles:
            out["roles"] = roles
        else:
            out.pop("roles", None)
        if "calibrate" in flow and "budget" in out:
            out["budget"] = {k: v for k, v in out["budget"].items() if k != "calibrate"}
        return out

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
              "roles", "space", "world", "hooks", "ladder", "cache", "flow")


def _space(raw: Any) -> dict[str, list]:
    """`space:` read and checked (D553): a mapping of knob -> a non-empty list of scalar
    choices, in the order written."""
    if not raw:
        return {}
    if not isinstance(raw, dict):
        raise TaskError("space: a mapping of knob -> [choices], in a meaningful order")
    out: dict[str, list] = {}
    for k, vals in raw.items():
        if not isinstance(vals, (list, tuple)) or not vals:
            raise TaskError(f"space.{k}: a non-empty list of choices")
        if any(isinstance(v, (dict, list)) for v in vals):
            raise TaskError(f"space.{k}: choices are scalars (numbers or names)")
        out[str(k)] = list(vals)
    return out


def _inherited(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """The child's document with what it does not say taken from the parent (D455; D555: a
    `world:` document's children inherit its world, hooks, ladder, cache and flow too, so a
    nested flow needs no world of its own). A child of a named campaign is its own campaign,
    `<parent>/<child>`, in the same record."""
    out = dict(child)
    for key in _INHERITED:
        if key not in out and key in parent:
            out[key] = parent[key]
    parent_campaign = parent.get("campaign") or {}
    if "campaign" not in out and isinstance(parent_campaign, dict) and parent_campaign.get("name") and child.get("id"):
        out["campaign"] = {**parent_campaign, "name": f"{parent_campaign['name']}/{child['id']}"}
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


def _stage(i: int, doc: Any, world: bool = False) -> Stage:
    if not isinstance(doc, dict) or not isinstance(doc.get("name"), str) or not doc["name"]:
        raise TaskError(f"stages[{i}] needs a `name`")
    cmd, ev = doc.get("command"), doc.get("evaluator")
    if cmd and ev:
        raise TaskError(f"stages[{i}] ({doc['name']}) needs exactly one of `command` or `evaluator`, not both")
    if not cmd and not ev and not world:
        raise TaskError(f"stages[{i}] ({doc['name']}) needs exactly one of `command` or `evaluator` "
                        "(a document with a `world` may leave both out: the world measures it)")
    needs = doc.get("needs")
    if needs is not None and (not isinstance(needs, list) or not all(isinstance(t, str) for t in needs)):
        raise TaskError(f"stages[{i}].needs is a list of tool names")
    needs = list(needs or [])
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
                timeout_s=float(doc.get("timeout_s") or 600.0), cutoff=cutoff, needs=tuple(needs))


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
    return TaskSpec.from_dict(doc, base=p.parent)


def request_for(task: TaskSpec, **overrides: Any) -> LoopRequest:
    """The loop's knobs for this task: the document's `budget`, then the caller's."""
    params = {"task": task.id, **task.params, **(overrides.pop("params", None) or {})}
    return LoopRequest(**{**task.budget, **overrides}, params=params)


def _substitute(cmd: Iterable[str], subs: dict[str, str]) -> list[str]:
    return [_PLACEHOLDER.sub(lambda m: subs.get(m.group(1), m.group(0)), tok) for tok in cmd]


#: What the document says itself (D519); every other public `Problem` method is a hook a
#: world may fill. `prototype` and `tools_missing` are bound by hand (the problem's own
#: check may replace the world's; the document's commands and the world's tools add up).
DOCUMENT_OWNED = frozenset({"objective", "objectives", "subgoals", "ladder", "stages", "roles", "campaign_name",
                            "cache_suffix", "validate", "chained", "role_cutoff", "role_measure", "role_order",
                            "role_analytic", "role_evaluator_name", "prototype", "tools_missing"})


#: THE WORLD CONTRACT (D561, review item 4): what a world's object may fill, by box of the
#: drawing -- what a new world reads instead of the fifty-odd methods of `Problem`. `CORE` is
#: the fourteen a world usually fills; the rest are extensions a world fills when it has a
#: reason. Everything else on `Problem` is the loop's or the document's and a world that
#: defines it is refused at load.
CONTRACT: dict[str, tuple[str, ...]] = {
    "knowledge": ("prepare", "knowledge", "mentor_sections", "versions", "from_record", "open_records"),
    "orchestrate": ("review", "next_work", "plan_prompt", "standing"),
    "dse": ("search", "space", "instantiate"),
    "generate": ("design_prompt", "parse_design", "rewrite_prompt", "patch_prompt", "prompt_prefix",
                 "tools", "apply_tools", "transpile", "redesign_note"),
    "test": ("build", "fast_check", "judge", "describe_failure"),
    "evaluate": ("measure", "measure_batch", "cache_key", "compose", "analytic_stages", "analytic_metrics", "evaluator_name"),
    "select": ("frontier", "finalists", "frontier_axes", "decide", "conclusion"),
}
CORE = ("prepare", "knowledge", "review", "search", "design_prompt", "parse_design", "build", "fast_check",
        "judge", "measure", "frontier", "finalists", "decide", "conclusion")


def world_hooks() -> tuple[str, ...]:
    """The `Problem` hooks a world's object may carry, by name: the contract's, sorted."""
    return tuple(sorted(n for names in CONTRACT.values() for n in names))


def loop_owned() -> frozenset[str]:
    """`Problem`'s public methods that are neither the contract's nor the document's: the
    loop's own machinery (routing, cutoffs, the improve loop, the plan, ...)."""
    public = {n for n in dir(Problem) if not n.startswith("_") and callable(getattr(Problem, n, None))}
    return frozenset(public - set(world_hooks()) - DOCUMENT_OWNED)


def contract_lines(filled: "set[str] | None" = None) -> list[str]:
    """The contract by box, one line each, marking the core hooks and (given `filled`) what
    a world fills -- what `flux task check` prints (D561)."""
    out = []
    for box, names in CONTRACT.items():
        parts = []
        for n in names:
            mark = ("*" if n in CORE else "") + n
            if filled is not None:
                mark = f"[{mark}]" if n in filled else mark
            parts.append(mark)
        out.append(f"{box}: " + ", ".join(parts))
    return out


def resolve(spec: str, what: str = "world") -> Any:
    """`package.module:attr` -> the attribute; a TaskError names what is missing."""
    import importlib

    mod_name, _, attr = spec.partition(":")
    try:
        mod = importlib.import_module(mod_name)
    except ImportError as exc:
        raise TaskError(f"{what} {spec!r}: {exc}") from exc
    out = mod
    for piece in attr.split("."):
        out = getattr(out, piece, None)
        if out is None:
            raise TaskError(f"{what} {spec!r}: {mod_name} has no {attr!r}")
    return out


# ------------------------------------------------------------------ the flow (D542)
#: The boxes of the drawing a document may say a half for, in flow order.
FLOW_BOXES = ("validate", "orchestrate", "plan", "dse", "generate", "test", "critique", "analytical",
              "simulation", "calibrate", "select", "knowledge", "feedback")
_FLOW_WORDS = {"validate": ("rules", "llm"), "test": ("gate",), "critique": ("none", "llm"), "plan": ("none", "llm"),
               "calibrate": ("on", "off"), "select": ("objectives",), "feedback": ("human", "none")}
_KNOWLEDGE_SOURCES = ("sheet", "library", "records", "mined", "digest")


def _flow(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """`flow:` (D542) -- one key per box of the drawing naming its half -- read, checked and
    FOLDED into the fields the loop already runs on (`roles`, `generator`, `critique`), so the
    rig underneath is unchanged and a document may still say those directly. Saying a thing
    in both places is refused: one vocabulary per document."""
    raw = doc.get("flow")
    if not raw:
        return {}, doc
    if not isinstance(raw, dict):
        raise TaskError("`flow` is an object: one key per box of the drawing "
                        f"({', '.join(FLOW_BOXES)})")
    unknown = sorted(set(raw) - set(FLOW_BOXES))
    if unknown:
        raise TaskError(f"flow: {', '.join(unknown)} is not a box of the drawing; the boxes are "
                        f"{', '.join(FLOW_BOXES)}")
    flow: dict[str, Any] = {}
    doc = dict(doc)
    roles = dict(doc.get("roles") or {})
    for box, words in _FLOW_WORDS.items():
        if box in raw:
            value = raw[box]
            if isinstance(value, bool) and box == "calibrate":
                value = "on" if value else "off"
            if value not in words:
                raise TaskError(f"flow.{box} is one of {', '.join(words)}, not {value!r}"
                                + (" (the gate is never delegated, D460)" if box == "test" else ""))
            flow[box] = value
    if flow.get("plan") == "llm":
        # D577: the pass is PLANNED first -- the model writes the loop plan (parts, order, the
        # method per part, budgets) before a step is spent; `budget.agent` carries the half
        budget = dict(doc.get("budget") or {})
        halves = list(budget.get("agent") or [])
        if "plan" not in halves:
            halves.append("plan")
        budget["agent"] = halves
        doc["budget"] = budget
    if "critique" in flow:
        if "critique" in doc:
            raise TaskError("the critic is said twice: `flow.critique` and `critique`")
        doc["critique"] = "propose" if flow["critique"] == "llm" else False
    if "orchestrate" in raw:
        if "orchestrator" in roles:
            raise TaskError("orchestration is said twice: `flow.orchestrate` and `roles.orchestrator`")
        roles["orchestrator"] = raw["orchestrate"]
        flow["orchestrate"] = raw["orchestrate"]
    if "dse" in raw:
        value = raw["dse"]
        if value != "none":
            from .roles import available_roles

            name = value if isinstance(value, str) else next(iter(value), None) if isinstance(value, dict) else None
            if name == "llm":                                   # D554: the model's half of the box
                name = "model"
                value = "model" if isinstance(value, str) else {"model": value["llm"]}
            policies = [n for n in available_roles("orchestrator") if n not in ("rules", "given", "llm", "agent")]
            if name not in policies:
                raise TaskError(f"flow.dse {value!r}: no such DSE policy is registered; "
                                f"registered: {', '.join(policies)} (D553)")
            if "orchestrator" in roles:
                raise TaskError("a DSE policy IS the orchestrator: say `flow.dse` or `flow.orchestrate`, not both")
            roles["orchestrator"] = value
        flow["dse"] = raw["dse"]
    if "generate" in raw:
        value = raw["generate"]
        if doc.get("generator") or "generator" in roles:
            raise TaskError("generation is said twice: `flow.generate` and `generator`/`roles.generator`")
        if value == "model":
            pass
        elif isinstance(value, dict) and len(value) == 1 and next(iter(value)) in ("command", "catalog", "agent"):
            doc["generator"] = dict(value)
        else:
            raise TaskError('flow.generate is "model", {command: [...]}, {catalog: [...]} or {agent: claude|codex|opencode|{...}}, '
                            f"not {value!r}")
        flow["generate"] = value
    for box in ("analytical", "simulation"):
        if box in raw:
            names = raw[box]
            if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
                raise TaskError(f"flow.{box} is a list of stage names")
            flow[box] = list(names)
    if "knowledge" in raw:
        sources = raw["knowledge"]
        if isinstance(sources, str):
            sources = [sources]
        if not isinstance(sources, list) or not all(s in _KNOWLEDGE_SOURCES for s in sources):
            raise TaskError(f"flow.knowledge is a list from {', '.join(_KNOWLEDGE_SOURCES)}, not {sources!r}")
        wanted = [s for s in ("mined", "digest") if s in sources]
        if wanted:
            if "knowledge" in roles:
                raise TaskError("the model-side knowledge is said twice: `flow.knowledge` and `roles.knowledge`")
            roles["knowledge"] = wanted[0] if len(wanted) == 1 else {"sources": {"names": wanted}}
        flow["knowledge"] = list(sources)
    doc["roles"] = roles
    return flow, doc


def _surrogate_kind(roles: dict[str, Any]) -> str:
    who = roles.get("evaluator")
    if who == "surrogate":
        return "fitted"
    if isinstance(who, dict) and "surrogate" in who:
        return str((who.get("surrogate") or {}).get("kind", "fitted"))
    if isinstance(who, dict) and who.get("name") == "surrogate":
        return str(who.get("kind", "fitted"))
    return ""


def _agent_tool(spec: Any) -> str:
    from .agent import agent_argv

    try:
        return agent_argv(spec)[0]
    except ValueError:
        return "?"


def _dse_name(value: Any) -> str:
    if isinstance(value, dict):
        (name, cfg), = value.items()
        return f"{name} {cfg}" if cfg else str(name)
    return str(value)


def _space_size(task: "TaskSpec", problem: Any) -> str:
    space = dict(task.space)
    if not space and problem is not None:
        try:
            from .types import LoopRequest, LoopState

            space = dict(problem.space(LoopState(request=LoopRequest(), say=lambda _m: None,
                                                  proposer=None, feedback=None)) or {})
        except Exception:  # noqa: BLE001 -- a world may need more than an empty state
            space = {}
    if not space:
        return "no space declared (the document's `space:` or the world's)"
    n = 1
    for vals in space.values():
        n *= max(1, len(vals))
    return f"{n} point(s): " + " x ".join(f"{k}[{len(v)}]" for k, v in space.items())


def describe_flow(task: "TaskSpec", problem: Any = None) -> list[str]:
    """The drawing, one line per box, with the half in force for this document -- said by
    `flow:` or implied by the older keys, the world (`problem`, when given: its analytic
    stages) and the defaults (D542). `flux task check` prints it."""
    from .roles import available_roles

    flow = task.flow
    roles = task.roles
    orch = roles.get("orchestrator")
    orch_name = orch if isinstance(orch, str) else (next(iter(orch)) if isinstance(orch, dict) and orch else None)
    policies = [n for n in available_roles("orchestrator") if n not in ("rules", "given", "llm", "agent")]
    gen = task.generator
    generate = ("catalog of %d design(s), no model" % len(gen["catalog"]) if gen.get("catalog")
                else "the generator command, no model" if gen.get("command")
                else f"coding agent `{_agent_tool(gen['agent'])}` (its own model and tools; the loop's build, test and judge around it, D575)" if gen.get("agent")
                else "model (the prototype stage, transpile, repair)")
    stage_names = [r.name for r in task.stages]
    analytical = list(flow.get("analytical") or [])
    if problem is not None:
        try:
            analytical += [s for s in stage_names if s in problem.analytic_stages() and s not in analytical]
        except Exception:  # noqa: BLE001 -- a world that cannot say is a world with none
            pass
    simulation = list(flow.get("simulation") or [s for s in stage_names if s not in analytical])
    knowledge = list(flow.get("knowledge") or [])
    if not knowledge:
        who = roles.get("knowledge")
        knowledge = (["sheet"] if task.knowledge else []) + ([who] if isinstance(who, str) and who in ("mined", "digest") else
                                                              list((who.get("sources") or {}).get("names") or []) if isinstance(who, dict) else [])
    lines = [
        f"validate: {flow.get('validate', 'rules')}" + (" (the loader's checks, then the model reads the document and objects, D556)"
                                                       if flow.get("validate") == "llm" else " (the loader's checks)"),
        "orchestrate: " + (f"{orch_name} (a DSE policy)" if orch_name in policies else
                           f"{orch_name}" if orch_name else "the problem default (a model plans the part; rules pick the work)")
        + " -- or: " + ", ".join(n for n in ("rules", "given", "llm", "agent") if n != orch_name),
        "plan: " + ("llm (the pass is planned first: parts, order, the method per part, budgets; the plan on the record, D577)"
                    if "plan" in (task.budget.get("agent") or ()) else "none (the orchestrator picks step by step)"),
        "dse: " + (f"{_dse_name(flow.get('dse'))} over {_space_size(task, problem)}" if flow.get("dse") not in (None, "none")
                   else "none (the world's own search, if it has one)")
        + f" -- registered: {', '.join(policies)}",
        f"generate: {generate}",
        "test: gate (never delegated)" + (" -- the world's judge" if task.world else " -- the document's commands"),
        "critique: " + ("llm (a model adversary on the division, each admitted part and the decision)" if task.critique else "none"),
        "analytical: " + (", ".join(analytical) if analytical else "none")
        + (f" -- surrogate ({_surrogate_kind(roles)}) predicts the costly stage from the record and orders the finalists (D560)"
           if _surrogate_kind(roles) else ""),
        "simulation: " + (", ".join(simulation) if simulation else "none declared"),
        "calibrate: " + ("off" if task.budget.get("calibrate") is False else "on (between every pair of stages, on the record)"),
        "select: objectives (" + (", ".join(f"{o.direction} {o.metric}" for o in task.objectives) or "none") + ")",
        "knowledge: " + (", ".join(knowledge) if knowledge else "none declared (the world's mentor, if any)"),
        f"feedback: {flow.get('feedback', 'human')}" + ("" if flow.get("feedback") == "none" else " (the operator's notes, when a terminal is attached)"),
    ]
    return lines


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
        self._caller_roles = roles                     # D555: what the caller overrode reaches the children
        #: The world's object of hooks (D519), or None for a document that runs on its own.
        self.world: Any = None
        if task.world:
            self.world = resolve(task.world)(self)
            owned = loop_owned()
            taken = sorted(n for n in owned if callable(getattr(self.world, n, None)) and n in type(self.world).__dict__)
            if taken:
                raise TaskError(f"world {task.world}: {', '.join(taken)} is the loop's, not a world's hook (D561); "
                                "a world fills the contract: " + "; ".join(contract_lines()))
            for name in world_hooks():
                fn = getattr(self.world, name, None)
                if callable(fn):
                    setattr(self, name, fn)
        import functools

        for name, spec in task.hooks.items():
            setattr(self, name, functools.partial(resolve(spec, f"hooks.{name}"), self))
        declared = frozenset(task.flow.get("analytical") or ())
        if declared:                                   # D542: the document's analytical stages join the world's
            world_analytic = self.__dict__.get("analytic_stages") or (lambda: frozenset())
            self.analytic_stages = lambda: frozenset(world_analytic()) | declared

    def __getattr__(self, name: str) -> Any:
        """What the problem does not know, its world may (D519): a world's own state (the
        NLU's `vectors`, `per_op`) is reachable through the problem that runs it."""
        world = self.__dict__.get("world")
        if world is None or name.startswith("__"):
            raise AttributeError(name)
        return getattr(world, name)

    # ---- what the document says, and what the world fills (D519)
    def ladder(self):
        """The document's `ladder:` (D517): true is the default ladder, an object its fields."""
        doc = self.task.ladder
        if not doc:
            return None
        from .ladder import Ladder

        if doc is True:
            return Ladder()
        return Ladder(**{k: tuple(v) if isinstance(v, list) else v for k, v in doc.items()})

    def prototype(self):
        """The world's prototype stage (D515), with this problem's OWN check when one was put on
        the instance (a scripted gate, D516) -- otherwise the loop's skeleton runs the world's
        judge."""
        world = self.__dict__.get("world")
        proto = getattr(world, "prototype", None)
        cap = proto() if callable(proto) else None
        own = self.__dict__.get("prototype_check")
        if cap is not None and own is not None and cap.check is None:
            return replace(cap, check=own)
        return cap

    def prototype_check(self, code: str, subgoal: str | None, state: LoopState) -> Verdict:
        """The loop's skeleton (D516) over the world's capability -- rules, array form, the
        family search, the sandbox, the world's judge."""
        from .check import check_prototype
        from .prototype import verified_operators

        cap = self.prototype()
        if cap is None:
            raise TaskError(f"{self.task.id}: no prototype stage is declared")
        return check_prototype(cap, code, subgoal, state, operators=verified_operators(state, subgoal))

    def roles(self) -> "Roles":
        """Who fills each of the four roles for this task (D460): the document's `roles`, with
        the caller's own overriding it slot by slot -- which is how a command line switches one
        role of a document it did not write."""
        return self._roles

    # ---- mentor
    def space(self, state: Any) -> dict[str, list]:
        return dict(self.task.space)

    def objectives(self) -> Objectives:
        """The document's, with `stage: deepest` resolved to the chain's last stage (D506: a
        part's numbers count where the chain measures deepest) and the margins the record
        measured on the shallower stages (D562)."""
        stages = self.stages()
        out = []
        for o in self.task.objectives:
            if o.stage == "deepest" and stages:
                o = replace(o, stage=stages[-1])
            learned = self.__dict__.get("_margins", {}).get(o.metric) or {}
            if learned:
                o = replace(o, margins=tuple(sorted(learned.items())))
            out.append(o)
        return Objectives(out)

    def calibrated(self, biases: list[Any], state: Any) -> None:
        """What a costly stage said about a cheaper one (D464) becomes the MARGIN on the
        cheaper stage (D562): for an objective with a goal on stage D, a shallower stage S must
        clear the goal over the measured ratio D/S -- the ratios of every link from S up to D,
        multiplied -- and the document's `margin` is the floor. A chain with a link nobody
        measured yet keeps the document's margin on that stage."""
        stages = self.stages()
        ratio = {(b.stage, b.against, b.metric): float(b.ratio) for b in biases if getattr(b, "ratio", None)}
        margins: dict[str, dict[str, float]] = dict(self.__dict__.get("_margins", {}))
        for o in self.objectives():
            if o.goal is None or o.stage not in stages:
                continue
            top = stages.index(o.stage)
            for i in range(top):
                shallow = stages[i]
                r = 1.0
                for j in range(i, top):
                    link = ratio.get((stages[j], stages[j + 1], o.metric))
                    if link is None or link <= 0:
                        r = None
                        break
                    r *= link
                if r is None:
                    continue
                measured = max(0.0, (1.0 / r - 1.0) if o.direction == "maximize" else (r - 1.0))
                before = o.margin_at(shallow)
                margins.setdefault(o.metric, {})[shallow] = measured
                if measured > float(o.margin) + 1e-9 and abs(measured - before) > 1e-6:
                    state.say(f"  the margin on the {shallow} stage for {o.metric} is {measured:.1%} from the measured "
                              f"{o.stage}/{shallow} ratio {r:.3f} (the document's {o.margin:.0%} is the floor): "
                              f"the goal there is {o.goal * (1 + measured) if o.direction == 'maximize' else o.goal / (1 + measured):.4g}")
        self._margins = margins

    def objective(self, request: LoopRequest) -> dict[str, Any]:
        """The record's identity document: the document's `campaign:` when it says one (a
        document that took over a campaign an application opened, D519), else the document's
        digest. Its `name`, when given, is the campaign's id (D524, `campaign_name`)."""
        if self.task.campaign:
            # D540: `study` is the document's id, so sibling campaigns of one document find
            # each other by it (a block may still say its own, for a record opened before)
            return {"study": self.task.id, **{k: v for k, v in self.task.campaign.items() if k != "name"}}
        # D539: the digest LAST -- `request_for` puts `task: <id>` into the params, and it
        # overwrote the digest, so two documents with one id shared a campaign
        return {"study": self.task.id, **request.params, "task": self.task.digest}

    def campaign_name(self, request: LoopRequest) -> str | None:
        """`campaign: {name: nlu}` -- the record keyed by the problem (D524)."""
        name = str(self.task.campaign.get("name") or "").strip()
        return name or None

    def objections(self, state: Any) -> list[str]:
        """The model's reading of the document (`flow: {validate: llm}`, D556): what it would
        object to before a step is spent -- advisory beside the rules, never a gate. Empty
        when the document does not ask for it or the run has no model."""
        if self.task.flow.get("validate") != "llm" or state.proposer is None:
            return []
        import json as _json_mod

        from .model import _ask, _json

        doc = _json_mod.dumps(self.task.to_dict(), indent=1, default=str)[:12000]
        prompt = ("Read this problem document before the run spends anything and OBJECT to what makes it "
                  "unanswerable or wasteful as written: an objective on a metric no stage measures, a goal no "
                  "stage could reach, a part with no gate, a cutoff that contradicts an objective, a budget that "
                  "cannot finish, a statement the parts do not add up to. Say nothing about style. "
                  "Reply as JSON: {\"ok\": true|false, \"objections\": [\"one line each\"]}.\n\nTHE DOCUMENT:\n"
                  + doc + "\n\nTHE FLOW IN FORCE:\n" + "\n".join(describe_flow(self.task, self)))
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"},
                                                   "objections": {"type": "array", "items": {"type": "string"}}},
                  "required": ["objections"]}
        try:
            got = _json(_ask(state, prompt, schema).text)
        except Exception as exc:  # noqa: BLE001 -- advisory: a failed reading objects to nothing
            return [f"(the model's reading did not run: {exc!s:.100})"]
        raw = (got or {}).get("objections") if isinstance(got, dict) else None
        return [str(o)[:300] for o in (raw or []) if str(o).strip()]

    def validate(self, request: LoopRequest) -> list[str]:
        """Is this document answerable as written (D463)? An objective naming a
        metric no stage produces has nothing to rank by, and a cutoff on a metric its own
        stage does not measure cuts nothing -- both are typos that would otherwise run
        the whole task and decide on nothing.

        A stage whose metrics are not declared (an `evaluator` stage without `metrics`)
        could produce anything, so its presence silences the check rather than failing
        it: refusing what might be right is worse than not checking."""
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
        for stage in self.task.stages:
            metric = stage.cutoff.get("metric")
            mine = {*stage.metrics_re, *stage.metrics}
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
        world_missing = getattr(self.__dict__.get("world"), "tools_missing", None)
        if callable(world_missing):
            missing.extend(world_missing())
        for _label, cmd in self.task.commands():
            head = _substitute(cmd[:1], {"python": sys.executable, "home": self.task.home or "."})[0]
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
        reply = _ask(state, prompt, schema).text
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
        doc = _json(_ask(state, prompt, schema).text)
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
                "part": subgoal or "", "python": sys.executable, "home": self.task.home or "."}

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
        if "agent" in spec:
            from .agent import agent_argv

            tool, argv, timeout = agent_argv(spec["agent"])
            return Template(lambda attempt: self._agent_draft(attempt, tool, argv, timeout), name=f"agent:{tool}")
        command = tuple(spec["command"])
        return Template(lambda attempt: self._rendered(attempt, command))

    def _agent_draft(self, attempt: Any, tool: str, argv: tuple[str, ...], timeout_s: float):
        """A coding agent's turn (D575): the brief on disk, the agent run in the work
        directory, the artifact it wrote -- or the one it printed, parsed as a model's reply
        would be -- as the candidate."""
        from .agent import agent_brief, run_agent

        state = attempt.state
        workdir = Path(state.workdir or ".")
        workdir.mkdir(parents=True, exist_ok=True)
        self._count += 1
        sg = attempt.subgoal
        name = f"{sg or _leaf(self.task.id)}#{self._count}"
        safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', name)
        path = workdir / f"draft-{safe}{self.task.extension}"
        if attempt.prior is not None and attempt.failure:
            body, _schema = self.rewrite_prompt(sg, attempt.prior, attempt.failure, state)
        else:
            body, _schema = self.design_prompt(sg, "", state, None, attempt.prior, attempt.failure)
        brief = agent_brief(body=body, prefix=self.prompt_prefix(sg, state) or "", artifact=path, workdir=workdir,
                            language=self.task.language or "text", part=sg or self.task.id,
                            prior=attempt.prior.artifact if attempt.prior is not None else None, failure=attempt.failure)
        prompt_file = workdir / f"PROMPT-{safe}.md"
        prompt_file.write_text(brief)
        subs = {"prompt": brief, "prompt_file": str(prompt_file), "artifact": str(path), "workdir": str(workdir),
                "part": sg or "", "name": name, "python": sys.executable, "home": self.task.home or "."}
        ok, rc, out, err = run_agent(argv, subs, workdir=workdir, timeout_s=timeout_s)
        knobs = {"task": self.task.id, "part": sg or "", "generator": f"agent:{tool}"}
        if path.is_file():
            return Candidate(name, path.read_text(), knobs=knobs, subgoal=sg), ""
        if ok and out.strip():
            cand, why = self.parse_design(out, sg)          # the agent printed the artifact instead
            if cand is not None:
                return Candidate(name, cand.artifact, knobs=knobs, subgoal=sg), ""
        tail = ((out or "") + "\n" + (err or "")).strip()[-2000:]
        return None, (f"the coding agent {tool} exited {rc} and wrote no {path.name}"
                      + (f": {tail}" if tail else ""))

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
        """The document's stages whose `needs` are on PATH (D519: the NLU's placement only
        with openroad), plus whatever the evaluation component adds below them (D461: a
        learned screen)."""
        mine = [r.name for r in self.task.stages if all(shutil.which(t) for t in r.needs)]
        return self.chained(mine or [StageNames.GATE])

    def measure(self, cand: Candidate, stage: str, state: LoopState) -> dict[str, float] | None:
        predicted = self.role_measure(cand, stage, state)
        if predicted is not None:
            return predicted                  # the evaluation component's own stage (D461)
        spec = next((r for r in self.task.stages if r.name == stage), None)
        if spec is None:
            return {"failures": 0.0} if stage == StageNames.GATE else None
        if not spec.command and not spec.evaluator:
            state.say(f"  stage {stage}: the world names no way to measure it")
            return None
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

    def cache_suffix(self) -> str | None:
        """The document's `cache:` (D541): the loop's sidecar, none, or a name."""
        cache = self.task.cache
        if cache is False:
            return None
        return cache if isinstance(cache, str) else f"{self.task.id}.json"


def _document(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        import yaml

        return yaml.safe_load(text)


# ------------------------------------------------------------------ the report
def task_report_lines(task: TaskSpec, out: Any, problem: Any = None) -> list[str]:
    """The standard report for a task run: the decision and its metrics, the world's own
    lines under it when it has a `report(out)` (D519: the NLU's per-operator error table),
    the frontier, then the shared closing sections (`flux_loop.report`, D558)."""
    lines = [f"TASK {task.id}: {task.statement[:100]}"]
    d = out.decision
    if d is not None:
        metrics = ", ".join(f"{k}={v:g}" for k, v in d.metrics.items())
        lines.append(f"  DECISION {d.name} [{d.stage}; {out.decided_by}]" + (f": {metrics}" if metrics else ""))
    else:
        lines.append("  NO CANDIDATE SURVIVED -- see NOT ESTABLISHED below")
    report = getattr(getattr(problem, "world", None), "report", None)
    if callable(report):
        try:
            lines.extend(report(out))
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  (the world's report could not be made: {exc!s:.120})")
    pool = out.confirmed or out.frontier
    if len(pool) > 1:
        lines.append(f"  frontier ({len(pool)} point(s)):")
        for p in pool:
            lines.append("    " + p.name + ": " + ", ".join(f"{k}={v:g}" for k, v in p.metrics.items()))
    if out.admitted:
        lines.append("  proven: " + ", ".join(f"{k}={c.name}" for k, c in sorted(out.admitted.items())))
    try:
        from .report import established, not_established, notes, refused

        for block in (established(out.lessons), not_established(out.not_established),
                      refused(out.refused, render=lambda r: f"{r[0]}: {r[1]}"), notes(out.notes)):
            if block:
                lines += [""] + list(block)
    except Exception:  # noqa: BLE001
        lines += [f"  {ln}" for ln in out.lessons]
    return lines
