"""The problem document (D519): what a `problem.yaml` says, how it is loaded and
checked, and the vocabulary of its `flow:` -- a statement, a contract, parts, a gate (how a
candidate is checked), costed stages (how it is measured), objectives (what "better" means), a
budget, a `space:` of knobs. What a document cannot say is a command beside it (D798-D803).

Commands carry placeholders: `{artifact}` (the candidate written to a file), `{home}` (the
document's folder), `{workdir}`, `{name}`, `{part}`, `{python}` (this interpreter), and `{knob}`
for each knob of `space:`. A gate is named checks run in order (D652); each prints its failures,
`count_re` (one integer group) or `fail_re` (one match per failure) says how the loop counts them,
and a non-zero exit with nothing counted is one failure. A check that exits 3 says the candidate
did not build (D594).

`flux_loop.task.PromptProblem` runs a document.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..objective import Objectives
from ..types import LoopRequest

if TYPE_CHECKING:  # pragma: no cover
    from ..objective import Objective
    from ..roles import Roles

from .keys import TaskError, DOCUMENT_KEYS, _LIFTED_KEYS, _INTERNAL_KEYS, _INHERITED, EXTENSIONS, DOCUMENT_FILE, ALT_SUFFIXES  # noqa: F401
from .commands import BUILTIN_SUBS, RTL_METRICS, RTL_STAT_METRICS, rtl_tools_kind, _flux_rtl_tools, _digest_of, _inferred_language, _command, _check_placeholders, _knob_subs, _substitute  # noqa: F401
from .gate import BUILD_FAILED, Check, Gate, DEFAULT_COUNT_RE, _gate, _gate_doc  # noqa: F401
from .stages import Stage, _stage  # noqa: F401
from .space import _knob_doc, _space, point_doc, _seeds, _point_name, _write_point, _dse_policies  # noqa: F401
from .flow import FLOW_BOXES, _flow  # noqa: F401
from .surface import _lift  # noqa: F401
from .layout import _layout  # noqa: F401

__all__ = ["BUILD_FAILED", "BUILTIN_SUBS", "Check", "DOCUMENT_KEYS", "FLOW_BOXES", "Gate", "Part", "Stage", "TaskError", "TaskSpec", "describe_flow", "load_task", "read_input", "request_for", "resolve"]


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
    workload: Any = None                 # for evaluator stages: a Workload IR document or path
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
                if not path.exists():
                    raise TaskError(f"knowledge.sheet {sheet!r} is not a file"
                                    + (f" beside {base}" if base is not None else ""))
                text = (text + "\n\n" if text else "") + path.read_text()
            files = knowledge.get("files") or []
            if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
                raise TaskError("knowledge.files is a list of paths (read beside the document)")
            for f in files:                  # D586: the documents a prompt came with -- specs, code, papers, tests
                path = Path(f) if Path(f).is_absolute() or base is None else Path(base) / f
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
            objectives = list(Objectives.from_doc(doc.get("objectives") or ()))
        except ValueError as exc:
            raise TaskError(str(exc)) from exc
        budget = dict(doc.get("budget") or {})
        if "pareto" in _dse_policies(flow.get("dse")) and len(objectives) < 2:
            raise TaskError(f"flow.dse: pareto needs two objectives; this document has {len(objectives)}")
        if flow:
            if flow.get("calibrate") == "off":
                if "calibrate" in budget:
                    raise TaskError("calibration is said twice: `flow.calibrate` and `budget.calibrate`")
                budget["calibrate"] = False
            if "sheet" in (flow.get("knowledge") or ()) and not sheet:
                raise TaskError("flow.knowledge names `sheet` but no `sheet:` file")
        known = {f.name for f in fields(LoopRequest)} - {"db", "params"}
        bad = sorted(set(budget) - known)
        if bad:
            raise TaskError(f"budget keys {bad} are not loop knobs; known: {sorted(known)}")
        if budget.get("prototype", True) not in (True, False, "python", "systemc"):
            raise TaskError(f"budget.prototype is true, false, python or systemc, not {budget['prototype']!r}")
        said_language = doc.get("language")
        inferred = None if said_language else _inferred_language(gate, stages)      # D832: from the tools named
        language = str(said_language or inferred or "text")
        ext = EXTENSIONS.get(language.lower(), "." + language.lower().replace(" ", ""))
        _check_placeholders(gate, stages, generator, space)
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
            budget=budget, params=dict(doc.get("params") or {}), space=space, when=when, space_from=space_from, seeds=seeds,
            workload=doc.get("workload"), home=str(Path(base).resolve()) if base is not None else "",
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
                       **({"evaluator": r.evaluator} if r.evaluator else {}),
                     **({"cutoff": dict(r.cutoff) if isinstance(r.cutoff, dict) else [dict(c) for c in r.cutoff]}
                        if r.cutoff else {}),
                       **({"metrics": list(r.metrics)} if r.metrics else {}),
                       **({"needs": list(r.needs)} if r.needs else {}),
                       **({"estimate": r.estimate.to_doc()} if r.estimate else {}),
                       "timeout_s": r.timeout_s} for r in self.stages],
            "objectives": [o.to_doc() for o in self.objectives],
            "knowledge": self.knowledge,
            "budget": dict(self.budget), "params": dict(self.params), "space": {k: _knob_doc(k, v, self.when.get(k), self.space_from.get(k)) for k, v in self.space.items()},
            **({"seeds": [dict(p) for p in self.seeds]} if self.seeds else {}),
            **({"workload": self.workload} if self.workload is not None else {}),
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
        if self.generator.get("command"):
            out.append(("generator", tuple(self.generator["command"])))
        for r in self.stages:
            if r.command:
                out.append((f"stage {r.name}", r.command))
            if r.estimate and r.estimate.command:
                out.append((f"estimate {r.name}", r.estimate.command))
        return out


def read_input(path: Path) -> str:
    """A file's text for a model to read (D586): a PDF through `pdftotext -layout`, anything
    else as UTF-8; non-text bytes are reported as such."""
    if path.suffix.lower() == ".pdf":
        if shutil.which("pdftotext") is None:
            return f"({path.name}: a PDF, and pdftotext is not on PATH to read it)"
        import subprocess

        r = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, timeout=120)
        return r.stdout if r.returncode == 0 else f"({path.name}: pdftotext failed: {r.stderr.strip()[:200]})"
    try:
        return path.read_text()
    except UnicodeDecodeError:
        return f"({path.name}: {path.stat().st_size} bytes of binary, not text)"


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
    home = (Path(base) / rel).resolve()
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


class ManyDocuments(TaskError):
    """A folder holding several problems that load: the caller names one (D787)."""

    def __init__(self, folder: Path, documents: list[Path]):
        self.documents = documents
        super().__init__(f"{folder}: {len(documents)} problems here ({', '.join(d.name for d in documents)}): name one")


def documents_in(folder: str | Path) -> list[Path]:
    """The problem documents of a folder (D787): its `problem.yaml` (or `problem.json`) first,
    then each `NAME.problem.yaml`."""
    f = Path(folder)
    main = [f / n for n in (DOCUMENT_FILE, "problem.json") if (f / n).is_file()][:1]
    return main + sorted(p for p in f.iterdir() if p.is_file() and p.name.endswith(ALT_SUFFIXES))


def alt_name(path: str | Path) -> str:
    """`NAME` of a `NAME.problem.yaml`: which of the folder's problems it is; '' for its `problem.yaml`."""
    name = Path(path).name
    for suffix in ALT_SUFFIXES:
        if name.endswith(suffix) and name != suffix[1:]:
            return name[: -len(suffix)]
    return ""


def record_name(path: str | Path) -> str:
    """The record a document's runs keep (D787): the folder's name, `<folder>.<NAME>` for a
    `NAME.problem.yaml` -- the file `out/<record>.db` and its campaign."""
    p = Path(path)
    folder, alt = p.resolve().parent.name, alt_name(p)
    return f"{folder}.{alt}" if alt else folder


def loadable(folder: str | Path) -> list[tuple[Path, str]]:
    """Each document of a folder with what its loader says ('' when it loads)."""
    out = []
    for d in documents_in(folder):
        try:
            load_task(d)
            out.append((d, ""))
        except TaskError as exc:
            out.append((d, str(exc)))
    return out


def load_task(path: str | Path) -> TaskSpec:
    """A task from its folder or a document file. The id is the folder's name (D786): a document
    does not say it. A folder with several problems (D787) loads the one that loads, and when
    more than one does, raises ManyDocuments for the caller to ask which. Every way it can fail
    is a TaskError that names the file (D590): a missing file, a syntax error with its line, a
    key no document has, and whatever the document itself gets wrong."""
    p = Path(path)
    parent = _parent_listing(p)
    if parent is not None:                                 # D802: a sub-loop alone, as its parent reads it
        return parent
    if p.is_dir():
        docs = documents_in(p)
        if not docs:
            raise TaskError(f"{p}: no problem document here ({DOCUMENT_FILE}, or NAME.problem.yaml)")
        if len(docs) > 1:
            said = loadable(p)
            good = [d for d, err in said if not err]
            if len(good) > 1:
                raise ManyDocuments(p, good)
            if not good:
                raise TaskError(said[0][1])
            docs = good
        p = docs[0]
    if p.suffix not in (".json", ".yaml", ".yml"):
        raise TaskError(f"{p}: a problem document is a .yaml, .yml or .json file")
    if not p.is_file():
        raise TaskError(f"{p}: no such file")
    text = p.read_text()
    try:
        if p.suffix == ".json":
            doc = json.loads(text)
        else:
            import yaml

            doc = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 -- yaml and json raise their own kinds; both are the file's fault
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark is not None else (
            f" (line {exc.lineno}, column {exc.colno})" if hasattr(exc, "lineno") else "")
        raise TaskError(f"{p}: not valid {'JSON' if p.suffix == '.json' else 'YAML'}{where}: "
                        f"{getattr(exc, 'problem', None) or getattr(exc, 'msg', None) or exc}") from exc
    if not isinstance(doc, dict):
        raise TaskError(f"{p}: a problem document is a mapping of keys (statement, language, flow, ...)")
    try:
        return task_in(doc, p.parent, alt=alt_name(p))
    except TaskError as exc:
        raise TaskError(f"{p}: {exc}") from exc


def _parent_listing(path: Path) -> TaskSpec | None:
    """The sub-task a folder is, when a document a few folders up lists it under `subtasks:`
    (D802): read through the parent, so it inherits what the parent says and keeps its record."""
    import os

    import yaml

    folder = (path if path.is_dir() else path.parent).resolve()
    if not (folder / DOCUMENT_FILE).is_file():
        return None
    for anc in list(folder.parents)[:3]:
        doc_path = anc / DOCUMENT_FILE
        if not doc_path.is_file():
            continue
        try:
            raw = yaml.safe_load(doc_path.read_text()) or {}
        except yaml.YAMLError:
            continue
        rel = os.path.relpath(folder, anc)
        if isinstance(raw, dict) and isinstance(raw.get("subtasks"), list) and rel in raw["subtasks"]:
            whole = load_task(doc_path)
            return next(c for c in whole.subtasks if c.from_path == rel)
    return None


def task_in(doc: dict[str, Any], home: Path, alt: str = "") -> TaskSpec:
    """The task a document says in its folder `home`: the folder's name is its id (D786); `alt`,
    the NAME of a `NAME.problem.yaml`, names its record `<id>.NAME` (D787)."""
    folder = Path(home).resolve().name
    if "id" in doc:
        raise TaskError(f"a document does not say its `id`: it is its folder's name ({folder}) (D786)")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", folder):
        raise TaskError(f"the folder's name {folder!r} is the problem's id: letters, digits, _, . or - (D786)")
    return TaskSpec.from_dict({**doc, "id": folder, **({"_record": f"{folder}.{alt}"} if alt else {})}, base=home)


def request_for(task: TaskSpec, **overrides: Any) -> LoopRequest:
    """The loop's knobs for this task: the document's `budget`, then the caller's."""
    params = {"task": task.id, **task.params, **(overrides.pop("params", None) or {})}
    knobs = {**task.budget, **overrides}
    if isinstance(knobs.get("prototype"), str):
        knobs["prototype"] = True             # `prototype: systemc` names the language; the stage is on
    return LoopRequest(**knobs, params=params)


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


#: D735, D791: a loop's own papers and references, read without a word in the document: its
#: `library/` (and `flux ask` puts its attachments there).
LIBRARY_FOLDER = "library"


def library_folders(task: "TaskSpec") -> tuple[str, ...]:
    """The folder a document's library adds to the shared one (D648, D791): its `library/`."""
    lib = Path(task.home) / LIBRARY_FOLDER if task.home else None
    return (str(lib.resolve()),) if lib is not None and lib.is_dir() else ()


def own_library(task: "TaskSpec") -> tuple[str, ...]:
    """The loop's own `library/`, when it holds a document (D753): what its Setup digests first."""
    from flux_knowledge.connectors.text import library_files as walk

    return tuple(f for f in library_folders(task) if any(True for _ in walk(Path(f))))


def library_on(task: "TaskSpec") -> bool:
    """Whether the library reaches this document's prompts and agents: always, unless
    `flow.knowledge` says `none` (D648)."""
    return "none" not in (task.flow.get("knowledge") or ())


def _agent_tool(spec: Any) -> str:
    from ..agent import agent_spec

    try:
        a = agent_spec(spec)
        return (a.tool + ("" if a.questions == "decide" else f", questions answered by the {a.questions}")
                + (", one session a pass" if a.session == "pass" else ""))
    except ValueError:
        return "?"


def _dse_name(value: Any) -> str:
    """A search as a document names it (D795: the model's half is `model`, an agent's by its name)."""
    if isinstance(value, dict) and "agent" in value:
        return f"agent {_agent_name(value)}"
    if isinstance(value, dict):
        (name, cfg), = value.items()
        name = "model" if name == "llm" else name
        return f"{name} {cfg}" if cfg else str(name)
    return "model" if value == "llm" else str(value)


def _space_size(task: "TaskSpec", problem: Any) -> str:
    space = dict(task.space)
    if not space and problem is not None:
        try:
            from ..types import LoopRequest, LoopState

            space = dict(problem.space(LoopState(request=LoopRequest(), say=lambda _m: None,
                                                  proposer=None, feedback=None)) or {})
        except Exception:  # noqa: BLE001 -- a world may need more than an empty state
            space = {}
    if not space:
        return "no space declared (the document's `flow.orchestrate.space`)"
    n = 1
    for vals in space.values():
        n *= max(1, len(vals))
    return f"{n} point(s): " + " x ".join(f"{k}[{len(v)}]" for k, v in space.items())


def _agent_name(value: dict[str, Any]) -> str:
    spec = value.get("agent")
    if isinstance(spec, str):
        return spec
    name = str((spec or {}).get("preset") or (spec or {}).get("name") or "command")
    return name + (", one session a pass" if (spec or {}).get("session") == "pass" else "")   # D669


#: What the rules half of orchestrate does, said the same in every place that shows it.
_KIND_OF_WORK = "rules pick the kind of work: a design sent back is improved first, then the parts, then the search"


def describe_orchestrate(task: "TaskSpec") -> str:
    """The orchestrate line's half, in words (D666)."""
    from ..roles import available_roles

    raw = task.flow.get("orchestrate")
    orch = task.roles.get("orchestrator")
    name = orch if isinstance(orch, str) else (next(iter(orch)) if isinstance(orch, dict) and orch else None)
    policies = [n for n in available_roles("orchestrator") if n not in ("rules", "given", "llm", "agent")]
    parts = bool(task.parts or task.decompose or task.subtasks or task.split)
    if name in policies:
        return f"{name} (a DSE policy picks the points)"
    if isinstance(raw, dict) and "agent" in raw:
        return (f"agent {_agent_name(raw)} (a coding agent picks the next part and the kind of work, "
                "its reasons on the record, D640)")
    if name == "agent":
        return "tools (the model with tools picks the next part and the kind of work, its reasons on the record, D505)"
    if name == "rules":
        return "rules (the first part waiting, no model; " + _KIND_OF_WORK + ")"
    if name == "given":
        return "given (the parts in the order given, no model; " + _KIND_OF_WORK + ")"
    if name == "llm":
        return "model (the model picks the next part; " + _KIND_OF_WORK + ")"
    if parts:
        return ("default (the model picks the next part, the first one waiting without a model; "
                + _KIND_OF_WORK + ")")
    return "default (one design, no part to pick; " + _KIND_OF_WORK + ")"


def describe_stage(stage: "Stage", modelled: bool = False) -> str:
    """One stage's line: how it is measured, its cutoffs and its estimator (D665)."""
    how = ("its command" if stage.command else f"evaluator {stage.evaluator}" if stage.evaluator
           else "nothing")
    cut = "; ".join(f"{c['metric']} " + (f">= {c['at']:g}" if "at" in c else f"<= {c['below']:g}" if "below" in c
                                        else f"within {c['within']:.0%} of the best") for c in stage.cutoffs if c)
    return (f"stage {stage.name}: {how}" + (", modelled" if modelled else "")
            + (f"; cutoff {cut}" if cut else "")
            + " -- estimate: " + (stage.estimate.describe() if stage.estimate else "none (the tool runs on every design)"))


def describe_flow(task: "TaskSpec", problem: Any = None) -> list[str]:
    """The drawing, one line per box, with the half in force for this document: from `flow:`,
    the other keys, the world (`problem`, when given) and the defaults (D542)."""
    from ..roles import available_roles

    flow = task.flow
    policies = [n for n in available_roles("orchestrator") if n not in ("rules", "given", "llm", "agent")]
    orch_name = describe_orchestrate(task).split(" ", 1)[0]
    gen = task.generator
    generate = ("catalog of %d design(s), no model" % len(gen["catalog"]) if gen.get("catalog")
                else "the generator command, no model" if gen.get("command")
                else f"coding agent `{_agent_tool(gen['agent'])}` (its own model and tools; the loop's build, test and judge around it, D575)" if gen.get("agent")
                else "model (the prototype stage, transpile, repair)")
    modelled: frozenset[str] = frozenset()
    if problem is not None:
        try:
            modelled = frozenset(problem.analytic_stages())
        except Exception:  # noqa: BLE001 -- a world that cannot say is a world with none
            pass
    knowledge = (["sheet"] if task.knowledge else []) + (
        [] if not library_on(task) else ["library" + "".join(f" + {Path(f).name}/" for f in library_folders(task))
                                         + " (on by default, its papers digested; `flow.knowledge: off` turns it off)"])
    if isinstance(flow.get("knowledge"), dict):                # D773
        knowledge += [f"the papers digested by agent {_agent_name(flow['knowledge'])}"]
    extract = flow.get("extract", "none")
    lines = [
        ("validate: " + (f"agent {_agent_name(flow['validate'])} (the loader's checks, then the agent reads the document and objects, D640)"
                         if isinstance(flow.get("validate"), dict) else
                         "model (the loader's checks, then the model reads the document and objects, D556)" if flow.get("validate") == "llm"
                         else "rules (the loader's checks)")),
        "orchestrate: " + describe_orchestrate(task)
        + " -- or: " + ", ".join(n for n in ("rules", "given", "model", "tools", "an agent") if n != {"llm": "model", "agent": "tools"}.get(orch_name, orch_name)),
        "plan: " + (f"agent {_agent_name(flow['plan'])} (the pass is planned first, checked by check_plan, D640)"
                    if isinstance(flow.get("plan"), dict) else
                    "model (the pass is planned first: parts, order, the method per part, budgets; the plan on the record, D577)"
                    if "plan" in (task.budget.get("agent") or ()) else "off (the orchestrator picks step by step)"),
        "dse: " + (f"{_dse_name(flow.get('dse'))} over {_space_size(task, problem)}" if flow.get("dse") not in (None, "none")
                   else "none")
        + f" -- registered: {', '.join(policies)}",
        f"generate: {generate}",
        "test: gate (never delegated) -- the document's commands",
        "critique: " + (f"agent {_agent_name(flow['critique'])} (on the division, each admitted part and the decision, D640)"
                        if isinstance(flow.get("critique"), dict) else
                        "model (a model adversary on the division, each admitted part and the decision)" if task.critique else "off"),
        "stages: " + (", ".join(r.name for r in task.stages) if task.stages else "none declared (the gate decides)"),
        *[describe_stage(r, r.name in modelled) for r in task.stages],
        "calibrate: " + ("off" if task.budget.get("calibrate") is False else "on (between every pair of stages, on the record)"),
        "select: objectives (" + (Objectives(task.objectives).describe() or "none") + ")"
        + (f"; agent {_agent_name(flow['select'])} breaks the ties they leave open (D640)" if isinstance(flow.get("select"), dict) else ""),
        "feedback: " + ("off (no notes are read, reloaded or waited for)" if flow.get("feedback") == "none"
                        else "human (the operator's notes, when a terminal is attached)"),
        "knowledge: " + (", ".join(knowledge) if knowledge else "off (the library is off)"),
        "lessons: " + (f"agent {_agent_name(extract)} (lessons from the record's rows, each citing its rows)" if isinstance(extract, dict)
                       else "mined (facts mined from the record reach the prompts)" if extract == "mined"
                       else "off (nothing is mined from the record) -- or: mined, an agent"),
        "records: always on (every candidate, measurement and refusal, read back on resume)",
    ]
    return lines
