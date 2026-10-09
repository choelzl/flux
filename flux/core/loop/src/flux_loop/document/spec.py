"""`TaskSpec`: the document read and checked, its sub-tasks, round trip and digest."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..objective import Objectives
from ..types import LoopRequest
from .commands import _check_placeholders, _command, _inferred_language
from .flow import _flow
from .gate import Gate, _gate, _gate_doc
from .keys import DOCUMENT_FILE, DOCUMENT_KEYS, EXTENSIONS, TaskError, _INHERITED, _INTERNAL_KEYS, _LIFTED_KEYS
from .layout import _layout
from .library import inside, read_input
from .space import _dse_policies, _knob_doc, _seeds, _space
from .stages import _stage
from .surface import _lift

if TYPE_CHECKING:  # pragma: no cover
    from ..objective import Objective
    from ..roles import Roles
    from .stages import Stage


@dataclass(frozen=True)
class Part:
    name: str
    statement: str = ""


@dataclass(frozen=True)
class TaskSpec:
    id: str
    statement: str
    contract: str = ""
    language: str = "text"
    extension: str = ".txt"
    #: D832: `language` was not said; the tools the checks and stages name gave it (not written back)
    language_inferred: bool = field(default=False, compare=False)
    parts: tuple[Part, ...] = ()
    decompose: bool = False              # "parts": "decompose" -- the orchestrator divides it
    max_parts: int = 8                   # the most a model's division may make (D792: not a document key)
    #: Sub-tasks, each run as its own loop with its own gate, stages and record (D455): nested
    #: documents, or "decompose" to ask the orchestrator. A child inherits what it does not say
    #: (see `_INHERITED`) but never `subtasks`, so nesting is bounded by the documents.
    subtasks: tuple["TaskSpec", ...] = ()
    split: bool = False                  # "subtasks": "decompose"
    max_subtasks: int = 4
    #: Who drafts (D456). Absent or "model": the model inner loop. `{"command": [...]}`: the
    #: command writes `{artifact}`, with `{failure}` carrying why the last draft was refused.
    #: `{"catalog": [path, ...]}`: existing designs, tried in order.
    generator: dict[str, Any] = field(default_factory=dict)
    #: Who fills each role (D460): `{"orchestrator": "rules"}`, `{"orchestrator": {"given":
    #: {"parts": [...]}}}`, ... -- one of `flux_loop.available_roles(role)` per role. The
    #: generation slot is the `generator` field above; saying it in both places is refused.
    roles: dict[str, Any] = field(default_factory=dict)
    critique: bool = False               # flow critique: llm -- a model critic judges (D433)
    gate: Gate = field(default_factory=Gate)
    stages: tuple[Stage, ...] = ()
    objectives: tuple[Objective, ...] = ()
    knowledge: str = ""
    joiner: str = "\n\n"                 # how admitted parts compose, in `parts` order (D792: fixed)
    budget: dict[str, Any] = field(default_factory=dict)      # LoopRequest overrides
    baseline: dict[str, Any] | None = None  # None: off; {}: check/measure the project as it is
    params: dict[str, Any] = field(default_factory=dict)      # the problem's own settings
    #: The design space (D553): knob -> its choices in a meaningful order, what a `flow.dse`
    #: policy searches.
    space: dict[str, list] = field(default_factory=dict)
    #: D801: the folder a sub-task was read from, as its parent wrote it ("" inline)
    from_path: str = field(default="", compare=False)
    #: D798: knob -> (its written choices, a glob beside the document) for a knob whose choices
    #: also come from files -- read at each load; written back as said, not as found.
    space_from: dict[str, tuple[tuple, str]] = field(default_factory=dict, compare=False)
    when: dict[str, dict[str, list]] = field(default_factory=dict)   # knob -> {knob: choices} it moves under
    seeds: tuple[dict[str, Any], ...] = ()     # points measured before the walk; the rest from the first choices
    skills: tuple[str, ...] = ()                  # D588: skill folders (absolute), for the model and the agents
    #: The flow (D542): one key per box of the drawing naming its half, normalised; what it
    #: implies is folded into `roles`, `generator`, `critique`, `budget.calibrate`.
    flow: dict[str, Any] = field(default_factory=dict)
    record: str = ""                     # the record's name: the id, `<parent>/<child>` for a sub-document
    ladder: Any = None                   # True, or the `flux_loop.Ladder` fields; None = no ladder
    knowledge_sheet: str = ""            # where `knowledge` was read from, for the report
    #: `flow: {knowledge: {agent: …}}` (D771, D773): who digests the papers in the Setup -- a
    #: coding agent's spec; None = the run's model.
    digest_by: Any = None
    #: The agents' workbench (D677, D790): `workbench/` beside the document, absolute; "" for an
    #: inline document; a sub-loop's in a folder, its parent's (D805), as its `out/` is. Their
    #: tools and notes, kept across runs; the loop provides it and never reads it. Where, like
    #: `home`, not what: not compared, not in the digest.
    workbench: str = field(default="", compare=False)
    #: The directory the document was loaded from ("" inline); every artifact of a run lives
    #: under `<home>/out/`, never beside the source (D578).
    home: str = field(default="", compare=False)      # not the document's: two loads of one text are equal

    def out_dir(self) -> Path:
        """Where a run of this document writes: `<home>/out/`, made on first use -- a sub-loop's
        in a folder, its parent's (D802: one record for the parent and its sub-loops)."""
        home = Path(self.home or ".")
        for _ in Path(self.from_path).parts if self.from_path else ():
            home = home.parent
        p = home / "out"
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ---- the document
    @classmethod
    def from_dict(cls, doc: dict[str, Any], base: str | Path | None = None) -> "TaskSpec":
        """`base` is the directory a `knowledge: {sheet: ...}` path is read beside (the
        document's own, when it was loaded from a file)."""
        if base is not None:
            _HOMES.append(str(Path(base).resolve()))       # D602: its modules resolve beside it
        if isinstance(doc, dict):
            doc = _lift(doc)                               # D775: each box's own settings under `flow`
        # D786: `id` is the folder's in a file (load_task), the caller's in a document built in code
        unknown = sorted(set(doc) - DOCUMENT_KEYS - _INTERNAL_KEYS - _LIFTED_KEYS - {"id"}) if isinstance(doc, dict) else []
        if unknown:
            import difflib

            hints = [f"{k} (did you mean {m[0]}?)" if (m := difflib.get_close_matches(k, DOCUMENT_KEYS, 1)) else k
                     for k in unknown]
            raise TaskError(f"keys a problem document does not have: {', '.join(hints)}")
        if not isinstance(doc, dict):
            raise TaskError("a task is a JSON/YAML object")
        tid = doc.get("id")
        if not isinstance(tid, str) or not tid.strip():
            raise TaskError("`id` must be a non-empty string")
        statement = doc.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            raise TaskError("`statement` must be a non-empty string: what is to be made")
        parts = []
        decompose = doc.get("parts") == "decompose"
        listed = () if doc.get("parts") == "decompose" else (doc.get("parts") or ())
        if decompose and listed:
            raise TaskError("`parts` is either a list or \"decompose\", not both")
        # D792: the parts' names in order, or a map from each name to what it is
        if isinstance(listed, dict):
            listed = [(str(k), v) for k, v in listed.items()]
        elif isinstance(listed, (list, tuple)) and all(isinstance(p, str) and p for p in listed):
            listed = [(p, "") for p in listed]
        else:
            raise TaskError('`parts` is "decompose", a list of names, or a map from each part\'s name to what it is')
        for name, what in listed:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name) or name == "decompose":
                raise TaskError(f"parts: {name!r} is no part's name (a letter, then letters, digits, _ and -)")
            if what is not None and not isinstance(what, str):
                raise TaskError(f"parts.{name}: what the part is, in words")
            parts.append(Part(name, str(what or "")))
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
                if isinstance(child, str):                 # D801: a folder beside the document
                    subtasks.append(_subtask_at(doc, child, base))
                    continue
                if not isinstance(child, dict):
                    raise TaskError(f"subtasks[{i}] is a folder beside the document, or a task document (an object)")
                subtasks.append(cls.from_dict(_inherited(doc, child), base))
        if len({c.id for c in subtasks}) != len(subtasks):
            raise TaskError(f"subtask ids must be unique, got {[c.id for c in subtasks]}")
        flow, doc = _flow(doc)             # D542: the drawing's boxes, folded into the fields below
        generator = dict(doc.get("generator") or {})      # from flow.generate: one of command, catalog, agent
        if "command" in generator:
            generator = {"command": list(_command(generator["command"], "flow.generate.command"))}
        elif "agent" in generator:
            from ..agent import agent_spec

            try:
                agent_spec(generator["agent"])
            except ValueError as exc:
                raise TaskError(f"flow.generate.agent: {exc}") from exc
            if isinstance(generator["agent"], dict) and "session" in generator["agent"]:
                # D669: generate's span is fixed -- one session per part until it is admitted
                raise TaskError("flow.generate.agent.session: generate keeps one session per part until the part is "
                                "admitted (repairs and send-backs resume it); `session` is set on a decision box")
        elif "catalog" in generator:
            value = generator["catalog"]
            if not isinstance(value, list) or not value or not all(isinstance(t, str) for t in value):
                raise TaskError("flow.generate.catalog must be a non-empty list of paths")
        roles = dict(doc.get("roles") or {})             # from the flow's boxes
        if roles:
            from ..roles import available_roles, make_role

            for role, spec in roles.items():
                try:            # an unknown role is a load error, not a
                    make_role(role, spec)   # surprise in the middle of a run
                except Exception as exc:  # noqa: BLE001
                    raise TaskError(f"roles.{role}: {exc} (available: "
                                    f"{', '.join(available_roles(role)) or 'none'})") from exc
        if (subtasks or split) and (parts or decompose):
            raise TaskError(
                "a task divides into `parts` of ONE artifact or into `subtasks` that are each "
                "their own loop, not both: with both, what the parent composes is ambiguous")
        space, when, space_from = _space(doc.get("space"), base)
        seeds = _seeds(doc.get("seeds"), space)
        # the record is named by the document's id, a sub-document's by `<parent>/<child>` (D628)
        record = str(doc.get("_record") or doc.get("id") or "").strip()
        ladder = doc.get("ladder")
        if isinstance(ladder, dict):
            from ..ladder import Ladder

            known_ladder = {f.name for f in fields(Ladder)}
            bad_ladder = sorted(set(ladder) - known_ladder)
            if bad_ladder:
                raise TaskError(f"ladder keys {bad_ladder} are not the ladder's; known: {sorted(known_ladder)}")
        elif ladder not in (None, True, False):
            raise TaskError("`ladder` is true (the default ladder) or an object of its fields")
        knowledge, sheet = doc.get("knowledge") or "", ""
        # D773: `flow: {knowledge: {agent: …}}` -- that coding agent digests the papers in the Setup
        digest_by = flow["knowledge"]["agent"] if isinstance(flow.get("knowledge"), dict) else None
        if isinstance(knowledge, dict):
            bad_keys = sorted(set(knowledge) - {"sheet", "text", "files"})
            if bad_keys:
                raise TaskError(f"knowledge keys {bad_keys} are not known; known: files, sheet, text")
            sheet = str(knowledge.get("sheet") or "")
            text = str(knowledge.get("text") or "")
            if sheet:
                path = Path(sheet) if Path(sheet).is_absolute() or base is None else Path(base) / sheet
                path = inside(path, f"knowledge.sheet {sheet!r}")          # D905: a web preview reads the loop's own
                if not path.exists():
                    raise TaskError(f"knowledge.sheet {sheet!r} is not a file"
                                    + (f" beside {base}" if base is not None else ""))
                text = (text + "\n\n" if text else "") + path.read_text()
            files = knowledge.get("files") or []
            if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
                raise TaskError("knowledge.files is a list of paths (read beside the document)")
            for f in files:                  # D586: the documents a prompt came with -- specs, code, papers, tests
                path = Path(f) if Path(f).is_absolute() or base is None else Path(base) / f
                path = inside(path, f"knowledge.files: {f!r}")
                if not path.is_file():
                    raise TaskError(f"knowledge.files: {f!r} is not a file" + (f" beside {base}" if base is not None else ""))
                body = read_input(path)
                text = (text + "\n\n" if text else "") + f"FILE {path.name}:\n{body}"
            knowledge = text
        # a parent whose work is its sub-loops judges nothing itself (D801)
        gate = _gate(doc.get("gate")) if doc.get("gate") or not (subtasks or split) else Gate()
        stages = [_stage(i, r) for i, r in enumerate(doc.get("stages") or ())]
        if len({r.name for r in stages}) != len(stages):
            raise TaskError("stage names must be unique")
        try:
            raw_objectives = []
            for raw in doc.get("objectives") or ():
                item = {"metric": raw} if isinstance(raw, str) else dict(raw) if isinstance(raw, dict) else raw
                if isinstance(item, dict):
                    metric = item.get("metric", "")
                    for stage in stages:
                        for parent, spec in stage.metric_specs.items():
                            if spec.get("type") == "dict" and metric == parent and spec.get("aggregate") == "none":
                                raise TaskError(f"objective {parent!r}: select a named submetric, such as {parent}.test, or configure a parent aggregate")
                            if metric == parent or (isinstance(metric, str) and spec.get("type") == "dict" and metric.startswith(parent + ".")):
                                for key in ("direction", "unit"):
                                    if key in spec:
                                        item.setdefault(key, spec[key])
                raw_objectives.append(item)
            objectives = list(Objectives.from_doc(raw_objectives))
        except ValueError as exc:
            raise TaskError(str(exc)) from exc
        budget = dict(doc.get("budget") or {})
        if space and "pareto" in _dse_policies(flow.get("dse")) and len(objectives) < 2:
            raise TaskError(f"flow.dse: pareto needs two objectives; this document has {len(objectives)}")
        if flow:
            if flow.get("calibrate") == "off":
                if "calibrate" in budget:
                    raise TaskError("calibration is said twice: `flow.calibrate` and `budget.calibrate`")
                budget["calibrate"] = False
            if "sheet" in (flow.get("knowledge") or ()) and not sheet:
                raise TaskError("flow.knowledge names `sheet` but no `sheet:` file")
        baseline = doc.get("baseline")
        if baseline is None or baseline is False:
            baseline = None
        elif baseline is True:
            baseline = {}
        elif isinstance(baseline, dict):
            baseline = dict(baseline)
            if set(baseline) - {"file", "command", "metrics", "timeout_s", "only"} or len(set(baseline) & {"file", "command", "metrics"}) > 1:
                raise TaskError("baseline takes file OR command OR metrics, optional timeout_s and only; true checks the project as it is")
            if "metrics" in baseline:
                rows = baseline["metrics"]
                if not isinstance(rows, list) or not rows:
                    raise TaskError("baseline.metrics must be a non-empty list of metric/value rows")
                if not stages:
                    raise TaskError("baseline.metrics needs a measurement stage")
                named = {s.name: s for s in stages}
                normalized, seen = [], set()
                for row in rows:
                    if not isinstance(row, dict) or set(row) - {"metric", "value", "stage"}:
                        raise TaskError("baseline.metrics rows take metric, value and optional stage")
                    metric, value = row.get("metric"), row.get("value")
                    if not isinstance(metric, str) or not metric.strip():
                        raise TaskError("baseline.metrics: metric must be a non-empty name")
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not -float("inf") < value < float("inf"):
                        raise TaskError("baseline.metrics: value must be a finite number")
                    stage = row.get("stage", stages[-1].name)
                    if stage in ("deepest", "last"):
                        stage = stages[-1].name
                    if not isinstance(stage, str) or stage not in named:
                        raise TaskError("baseline.metrics: stage must name a measurement stage")
                    declared = {*named[stage].metrics, *named[stage].metrics_re}
                    if declared and not named[stage].reports(metric):
                        raise TaskError(f"baseline.metrics: {metric!r} is not reported by {stage!r}")
                    if (stage, metric) in seen:
                        raise TaskError(f"baseline.metrics: duplicate {metric!r} on {stage!r}")
                    seen.add((stage, metric))
                    normalized.append({"metric": metric, "value": float(value), "stage": stage})
                baseline["metrics"] = normalized
            if "file" in baseline:
                if not isinstance(baseline["file"], str) or not baseline["file"].strip():
                    raise TaskError("baseline.file must be a non-empty file path")
                if base is not None:
                    inside(Path(base) / baseline["file"].replace("{home}", str(Path(base))), "baseline.file")
            if "command" in baseline:
                try:
                    baseline["command"] = list(_command(baseline["command"], "baseline.command") or ())
                except ValueError as exc:
                    raise TaskError(f"baseline.command: {exc}") from exc
                if not baseline["command"]:
                    raise TaskError("baseline.command must be a non-empty command")
            if "only" in baseline and not isinstance(baseline["only"], bool):
                raise TaskError("baseline.only must be true or false")
            if "timeout_s" in baseline and (isinstance(baseline["timeout_s"], bool)
                    or not isinstance(baseline["timeout_s"], (int, float)) or not 0 < baseline["timeout_s"] < float("inf")):
                raise TaskError("baseline.timeout_s must be a finite positive number")
        else:
            raise TaskError("baseline is true, false, or a mapping with file or command")
        known = {f.name for f in fields(LoopRequest)} - {"db", "params", "baseline", "baseline_only"}
        bad = sorted(set(budget) - known)
        if bad:
            raise TaskError(f"budget keys {bad} are not loop knobs; known: {sorted(known)}")
        quota = budget.get("exploration_quota", 0)
        if isinstance(quota, bool) or not isinstance(quota, (int, float)) or not 0 <= quota <= 1:
            raise TaskError("budget.exploration_quota is a fraction between 0 and 1")
        if budget.get("prototype", True) not in (True, False, "python", "systemc"):
            raise TaskError(f"budget.prototype is true, false, python or systemc, not {budget['prototype']!r}")
        said_language = doc.get("language")
        inferred = None if said_language else _inferred_language(gate, stages)      # D832: from the tools named
        language = str(said_language or inferred or "text")
        ext = EXTENSIONS.get(language.lower(), "." + language.lower().replace(" ", ""))
        _check_placeholders(gate, stages, generator, space, baseline)
        skills_raw = doc.get("skills") or []
        if isinstance(skills_raw, str):
            skills_raw = [skills_raw]
        if not isinstance(skills_raw, list) or not all(isinstance(x, str) for x in skills_raw):
            raise TaskError("`skills` is a list of folders (a skill, or a folder of skills), beside the document")
        from ..skills import SkillError, load_skills

        try:
            skills = tuple(str(sk.path) for sk in load_skills(skills_raw, base=Path(base) if base is not None else None))
        except SkillError as exc:
            raise TaskError(f"skills: {exc}") from exc
        workbench = str((Path(base) / "workbench").resolve()) if base is not None else ""
        return cls(
            id=tid.strip(), statement=statement.strip(), contract=str(doc.get("contract") or ""),
            language=language, extension=ext,
            parts=tuple(parts), decompose=decompose,
            generator=dict(generator), roles=dict(roles), flow=dict(flow),
            subtasks=tuple(subtasks), split=split,
            max_subtasks=int(doc.get("max_subtasks") or 4),
            critique=flow.get("critique") == "llm" or isinstance(flow.get("critique"), dict),
            gate=gate, stages=tuple(stages), objectives=tuple(objectives),
            knowledge=str(knowledge),
            budget=budget, baseline=baseline, params=dict(doc.get("params") or {}), space=space, when=when, space_from=space_from, seeds=seeds,
            home=str(Path(base).resolve()) if base is not None else "",
            record=record, ladder=ladder if ladder else None,
            knowledge_sheet=sheet, digest_by=digest_by,
            skills=skills, workbench=workbench, language_inferred=bool(inferred),
        )

    def _to_dict(self) -> dict[str, Any]:
        gate = _gate_doc(self.gate)
        return {
            "id": self.id, "statement": self.statement, "contract": self.contract,
            **({} if self.language_inferred else {"language": self.language}),
            "parts": ("decompose" if self.decompose
                      else {p.name: p.statement for p in self.parts} if any(p.statement for p in self.parts)
                      else [p.name for p in self.parts]),
            **({"flow": dict(self.flow)} if self.flow else {}),
            **({"subtasks": "decompose", "max_subtasks": self.max_subtasks} if self.split else
               {"subtasks": [c.from_path or c.to_dict() for c in self.subtasks]} if self.subtasks else {}),
            "gate": gate,
            "stages": [{"name": r.name,
                       **({"command": list(r.command)} if r.command else {}),
                       **({"metrics_re": dict(r.metrics_re)} if r.metrics_re else {}),
                     **({"cutoff": dict(r.cutoff) if isinstance(r.cutoff, dict) else [dict(c) for c in r.cutoff]}
                        if r.cutoff else {}),
                       **({"metrics": r.metric_doc()} if r.metrics else {}),
                       **({"needs": list(r.needs)} if r.needs else {}),
                       **({"estimate": r.estimate.to_doc()} if r.estimate else {}),
                       "timeout_s": r.timeout_s} for r in self.stages],
            "objectives": [o.to_doc() for o in self.objectives],
            "knowledge": self.knowledge,
            "budget": dict(self.budget), "params": dict(self.params), "space": {k: _knob_doc(k, v, self.when.get(k), self.space_from.get(k)) for k, v in self.space.items()},
            **({"baseline": dict(self.baseline) or True} if self.baseline is not None else {}),
            **({"seeds": [dict(p) for p in self.seeds]} if self.seeds else {}),
            **({"_record": self.record} if self.record not in ("", self.id) else {}),
            **({"ladder": self.ladder} if self.ladder else {}),
            **({"skills": list(self.skills)} if self.skills else {}),
        }

    def to_dict(self) -> dict[str, Any]:
        """The document, round-trippable: `flow:` says who fills each box and how (D542, D629, D775)."""
        out = self._to_dict()
        if "calibrate" in self.flow and "budget" in out:
            out["budget"] = {k: v for k, v in out["budget"].items() if k != "calibrate"}
        return _layout(out)

    @property
    def digest(self) -> str:
        # the fields, not their layout: a document said the D775 way is the same document (its record agrees)
        out = self._to_dict()
        if "calibrate" in self.flow and "budget" in out:
            out["budget"] = {k: v for k, v in out["budget"].items() if k != "calibrate"}
        return hashlib.sha256(json.dumps(out, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def commands(self) -> list[tuple[str, tuple[str, ...]]]:
        out: list[tuple[str, tuple[str, ...]]] = []
        out += [(f"gate {c.name}", c.run) for c in self.gate]
        if self.baseline and self.baseline.get("command"):
            out.append(("baseline", tuple(self.baseline["command"])))
        if self.generator.get("command") and not (self.baseline or {}).get("only"):
            out.append(("generator", tuple(self.generator["command"])))
        for r in self.stages:
            if r.command:
                out.append((f"stage {r.name}", r.command))
            if r.estimate and r.estimate.command and not (self.baseline or {}).get("only"):
                out.append((f"estimate {r.name}", r.estimate.command))
        return out


def _inherited(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """The child's document with what it does not say taken from the parent (D455), including
    its ladder and flow (D555). A child of a named
    campaign is its own campaign, `<parent>/<child>`, in the same record. D801: `flow`,
    `budget` and `params` merge key by key -- a child that says only `test:` keeps the
    parent's stages, objectives' search and knowledge."""
    out = dict(child)
    for key in _INHERITED:
        if key not in out and key in parent:
            out[key] = parent[key]
        elif key in ("flow", "budget", "params") and isinstance(parent.get(key), dict) and isinstance(out.get(key), dict):
            out[key] = {**parent[key], **out[key]}
    composes = (parent.get("flow") or {}).get("generate")
    if parent.get("subtasks") and isinstance(out.get("flow"), dict) and isinstance(composes, dict) \
            and "command" in composes and out["flow"].get("generate") is composes:
        # D801: a parent's `generate: {command}` composes its sub-loops; D804: a model or an
        # agent there drafts for them, and they inherit it like any box
        out["flow"] = {k: v for k, v in out["flow"].items() if k != "generate"}
    out["_inherited"] = True                               # D775: the parent's fields as the loop keeps them
    if child.get("id") and (parent.get("_record") or parent.get("id")):
        out["_record"] = f"{parent.get('_record') or parent.get('id')}/{child['id']}"
    return out


def _with_workbench(task: "TaskSpec", where: str) -> "TaskSpec":
    """`task` and its own sub-loops with the workbench `where` (D805): a parent's agents and its
    sub-loops' share one -- recip's notes and tools are there for rsqrt -- and an operator's
    folder stays what it says."""
    from dataclasses import replace

    return replace(task, workbench=where, subtasks=tuple(_with_workbench(c, where) for c in task.subtasks))


def _subtask_at(parent: dict[str, Any], rel: str, base: Any) -> "TaskSpec":
    """A sub-task in a folder beside the parent (D801): its `problem.yaml` says only what
    differs; its id is the folder's name, its home the folder (its golden model, library/, ...)."""
    import yaml

    if base is None:
        raise TaskError(f"subtasks: {rel!r} is a folder beside the document; an inline document has none")
    home = inside((Path(base) / rel).resolve(), f"subtasks: {rel!r}")     # D905
    path = home / DOCUMENT_FILE
    if not path.is_file():
        raise TaskError(f"subtasks: no {DOCUMENT_FILE} in {rel!r}")
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as exc:
        raise TaskError(f"subtasks: {rel}/{DOCUMENT_FILE} is not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise TaskError(f"subtasks: {rel}/{DOCUMENT_FILE} is a mapping of keys")
    if "id" in raw:
        raise TaskError(f"subtasks: {rel}/{DOCUMENT_FILE} does not say its `id`: it is its folder's name (D786)")
    from dataclasses import replace

    parent = dict(parent)
    know = parent.get("knowledge")
    if isinstance(know, dict):                             # the parent's files, beside the parent
        def _abs(f: Any) -> Any:
            return f if not isinstance(f, str) or Path(f).is_absolute() else str((Path(base) / f).resolve())
        parent["knowledge"] = {k: ([_abs(x) for x in v] if k == "files" and isinstance(v, list) else _abs(v) if k == "sheet" else v)
                               for k, v in know.items()}
    try:
        own = _lift({**raw, "id": home.name})              # its own surface, read before it inherits
        child = replace(TaskSpec.from_dict(_inherited(parent, own), home), from_path=rel)
        return _with_workbench(child, str((Path(base) / "workbench").resolve()))
    except TaskError as exc:
        raise TaskError(f"subtasks: {rel}: {exc}") from exc


def _rig_for(task: TaskSpec, caller: "Roles | None") -> "Roles":
    """The task's own roles, overlaid by the caller's (D460). A caller's filled slot wins; its
    empty ones leave the document's alone, so `--role orchestrator=rules` switches exactly the
    one role it names."""
    from ..roles import ROLES, Roles, rig

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


#: The folders of the documents loaded in this process (D602). A module a document names that
#: is not installed is looked for beside the document, appended to the path on a miss so it
#: never shadows an installed one.
_HOMES: list[str] = []


def resolve(spec: str, what: str = "policy") -> Any:
    """`package.module:attr` -> the attribute; a TaskError names what is missing."""
    import importlib

    mod_name, _, attr = spec.partition(":")
    try:
        mod = importlib.import_module(mod_name)
    except ImportError as exc:
        top = mod_name.split(".")[0]
        home = next((h for h in reversed(_HOMES)
                     if (Path(h) / f"{top}.py").is_file() or (Path(h) / top / "__init__.py").is_file()), None)
        if home is None:
            raise TaskError(f"{what} {spec!r}: {exc}") from exc
        if home not in sys.path:
            sys.path.append(home)
        try:
            mod = importlib.import_module(mod_name)
        except ImportError as exc2:
            raise TaskError(f"{what} {spec!r}: {exc2}") from exc2
    out = mod
    for piece in attr.split("."):
        out = getattr(out, piece, None)
        if out is None:
            raise TaskError(f"{what} {spec!r}: {mod_name} has no {attr!r}")
    return out
