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
from typing import TYPE_CHECKING, Any, Iterable

from .estimate import KINDS as ESTIMATE_KINDS, Estimator
from .objective import Objective, Objectives
from .types import (LoopRequest)

if TYPE_CHECKING:  # pragma: no cover
    from .roles import Roles

__all__ = ["BUILD_FAILED", "BUILTIN_SUBS", "Check", "DOCUMENT_KEYS", "FLOW_BOXES", "Gate", "Part", "Stage", "TaskError", "TaskSpec", "describe_flow", "load_task", "read_input", "request_for", "resolve"]

#: `{name}` in a command: the loop's own (`BUILTIN_SUBS`) or a knob of `space:` (D581);
#: a name neither is stays as written (a script's own braces are its business)
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")
BUILTIN_SUBS = ("artifact", "workdir", "name", "part", "python", "home", "failure", "attempt",
                "prompt", "prompt_file", "point", "params", "history", "state", "parts")


class TaskError(ValueError):
    """The document is not a task: the message names the field and what it should be."""


@dataclass(frozen=True)
class Part:
    name: str
    statement: str = ""




#: what `flux rtl measure` prints (D628): a stage running it need not list them
RTL_METRICS = ("fmax_mhz", "area_um2", "power_w", "cell_count")
RTL_STAT_METRICS = ("area_um2", "cell_count")      # `--stage stat`: nothing timed (D662)


def rtl_tools_kind(cmd: Iterable[str] | None) -> str:
    """"test", "proto", "measure" for a `flux rtl ...` command, else ""."""
    toks = list(cmd or ())
    try:
        at = toks.index("rtl")
    except ValueError:
        return ""
    if at == 0 or "flux" not in " ".join(toks[:at]) or at + 1 >= len(toks):
        return ""
    return toks[at + 1]


def _flux_rtl_tools(cmd: Iterable[str]) -> list[str]:
    """The tools a `flux rtl lint|test|measure` or `flux prog count|size` command runs (D600): they
    may be missing outside the Nix dev shell, and the command itself is Python, so `task check`
    must name them."""
    toks = list(cmd)
    at = next((i for i, t in enumerate(toks) if t in ("rtl", "prog")), None)
    if not at or "flux" not in " ".join(toks[:at]):
        return []
    sub = toks[at + 1] if at + 1 < len(toks) else ""
    if toks[at] == "prog":                # D661: `time` falls back to a Python loop without hyperfine
        return {"count": ["valgrind"], "size": ["size"]}.get(sub, [])
    if sub in ("test", "lint"):
        return ["verilator"]
    if sub == "measure":
        if _stage_of(toks) == "stat":
            return ["yosys"]              # D662: Yosys alone, nothing timed
        return ["yosys", "openroad"]      # synthesis too: its timing is OpenROAD's OpenSTA
    return []


def _stage_of(toks: list[str]) -> str:
    """A `flux rtl measure` command's `--stage` (synth when it says none)."""
    for i, t in enumerate(toks):
        if t == "--stage" and i + 1 < len(toks):
            return toks[i + 1]
        if t.startswith("--stage="):
            return t.split("=", 1)[1]
    return "synth"

#: D594: a gate test's exit code for "the candidate did not build" (nothing was tested).
BUILD_FAILED = 3


def _digest_of(text: str | None) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode()).hexdigest()

@dataclass(frozen=True)
class Check:
    """One named check of a gate (D652): a command whose failures are counted by `count_re`
    (one integer group), `fail_re` (one match per failure) or, with neither matching, its exit
    code. Exit 3 means the candidate did not build; `builds` (the old `build:` key) makes any
    non-zero exit mean that."""

    name: str
    run: tuple[str, ...]
    count_re: str | None = None
    fail_re: str | None = None
    timeout_s: float = 120.0
    builds: bool = False

    @property
    def rule(self) -> str:
        """The pass rule in words, for `flux task check`."""
        if self.builds:
            return "passes when it exits 0; otherwise the candidate did not build"
        how = (f"`{self.count_re}` reads 0" if self.count_re
               else f"no line matches `{self.fail_re}`" if self.fail_re else "it exits 0")
        return f"passes when {how}; exit 3 = did not build"


class Gate(tuple):
    """How a candidate is checked (D652): its checks, run in order, cheapest first. The first
    that reports failures refuses the design; the checks after it do not run."""

    def named(self, name: str) -> Check | None:
        return next((c for c in self if c.name == name), None)

    @property
    def timeout_s(self) -> float:
        return max((c.timeout_s for c in self), default=120.0)

    def line(self) -> str:
        """The checks as one shell line, for a prompt or an agent's brief."""
        return " && ".join(" ".join(c.run) for c in self)


@dataclass(frozen=True)
class Stage:
    """One costed measurement: a command whose output carries the metrics (`metrics_re`,
    one float group each), or an evaluator named in the ABI registry applied to the
    artifact read as an architecture document.

    `cutoff` is what is worth the next stage (D454): one of `{"metric": m, "at": x}` (a floor),
    `{"metric": m, "below": x}` (a budget) or `{"metric": m, "within": f}` (a band around this
    run's best, `f` a fraction), or a list of them, all of which a design must pass, in order
    (D657). Without one, only the last stage's results decide.

    `estimate` (D665, off by default) predicts the stage's metrics before its tool runs; a design
    whose estimate fails the cutoff or an objective's limit by more than its margin is skipped."""

    name: str
    command: tuple[str, ...] | None = None
    metrics_re: dict[str, str] = field(default_factory=dict)
    evaluator: str | None = None
    metrics: tuple[str, ...] = ()
    timeout_s: float = 600.0
    cutoff: dict[str, Any] | tuple[dict[str, Any], ...] = field(default_factory=dict)   # one gate, or several
    needs: tuple[str, ...] = ()          # tools on PATH the stage wants; absent, the stage is skipped (D519)
    estimate: Estimator | None = None    # the pre-gate before the tool (D665)

    @property
    def cutoffs(self) -> tuple[dict[str, Any], ...]:
        """The stage's gates in order: the single-dict form is one."""
        return (self.cutoff,) if isinstance(self.cutoff, dict) else tuple(self.cutoff)


@dataclass(frozen=True)
class TaskSpec:
    id: str
    statement: str
    contract: str = ""
    language: str = "text"
    extension: str = ".txt"
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
            from .agent import agent_spec

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
            from .roles import available_roles, make_role

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
            from .ladder import Ladder

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
        language = str(doc.get("language") or "text")
        ext = EXTENSIONS.get(language.lower(), "." + language.lower().replace(" ", ""))
        _check_placeholders(gate, stages, generator, space)
        skills_raw = doc.get("skills") or []
        if isinstance(skills_raw, str):
            skills_raw = [skills_raw]
        if not isinstance(skills_raw, list) or not all(isinstance(x, str) for x in skills_raw):
            raise TaskError("`skills` is a list of folders (a skill, or a folder of skills), beside the document")
        from .skills import SkillError, load_skills

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
            skills=skills, workbench=workbench,
        )

    def _to_dict(self) -> dict[str, Any]:
        gate = _gate_doc(self.gate)
        return {
            "id": self.id, "statement": self.statement, "contract": self.contract,
            "language": self.language,
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


def _command(raw: Any, what: str) -> tuple[str, ...] | None:
    """A command the document says (D580): argv tokens as a list, or one string split like
    a shell would. A command whose head is `flux` runs this flux (`{python} -m
    flux_cli.main`), so a document reads `flux rtl test {artifact} ...` and needs no
    wrapper on PATH. `{artifact}`, `{workdir}`, `{name}`, `{part}`, `{python}` and
    `{home}` are substituted at run time."""
    if raw is None:
        return None
    if isinstance(raw, str):
        import shlex

        toks = shlex.split(raw)
    elif isinstance(raw, list) and raw and all(isinstance(t, str) for t in raw):
        toks = list(raw)
    else:
        raise TaskError(f"{what} must be a command: a non-empty list of strings, or one string")
    if not toks:
        raise TaskError(f"{what} is empty")
    if toks[0] == "flux":
        # warnings off (D589): runpy and numpy warnings in a refusal mislead the model
        toks = ["{python}", "-W", "ignore", "-m", "flux_cli.main", *toks[1:]]
    return tuple(toks)


def _check_placeholders(gate: "Gate | None", stages: Iterable["Stage"], generator: dict[str, Any],
                        space: dict[str, list]) -> None:
    """Every `{name}` a command token says (not a `-c` script) must be the loop's or a knob of
    `space:`; otherwise it is a typo that would reach the tool as text (D581)."""
    known = set(BUILTIN_SUBS) | set(space)
    cmds: list[tuple[str, Iterable[str]]] = []
    if gate is not None:
        cmds += [(f"gate {c.name}", c.run) for c in gate]
    cmds += [(f"stage {r.name}", r.command or ()) for r in stages]
    cmds += [(f"estimate {r.name}", r.estimate.command) for r in stages if r.estimate and r.estimate.command]
    if generator.get("command"):
        cmds.append(("generator", generator["command"]))
    for what, cmd in cmds:
        for tok in cmd:
            if any(c.isspace() for c in tok):
                continue
            for m in _PLACEHOLDER.finditer(tok):
                if m.group(1) not in known:
                    raise TaskError(f"{what} says {{{m.group(1)}}}, which is neither a knob of `flow.dse.space` "
                                    f"({', '.join(space) or 'none'}) nor the loop's ({', '.join(BUILTIN_SUBS)})")


def _knob_subs(knobs: dict[str, Any]) -> dict[str, str]:
    """A candidate's knobs as `{knob}` substitutions (D581): the scalar ones."""
    return {k: str(v) for k, v in (knobs or {}).items() if isinstance(v, (str, int, float, bool))}


#: D628: what `flux rtl test`, `flux rtl proto` and the templates' checkers print; a gate that
#: prints no such line is judged by its exit code
DEFAULT_COUNT_RE = r"(\d+) failing"


_GATE_HELP = ("`flow.test` is a command (one check, `test`), or a map from each check's name to its command "
              "or `{run, count_re?, fail_re?, timeout_s?}`, run in order -- a check named `build` refuses on any "
              "non-zero exit; `{artifact}`, `{workdir}`, `{name}`, `{part}`, `{python}`, `{home}` are substituted; "
              "a `flux ...` head runs this flux")
_CHECK_KEYS = ("run", "count_re", "fail_re", "timeout_s")


def _patterns(doc: dict[str, Any], what: str) -> tuple[str | None, str | None]:
    for key in ("count_re", "fail_re"):
        pat = doc.get(key)
        if pat is not None:
            try:
                re.compile(pat)
            except re.error as exc:
                raise TaskError(f"{what}.{key} is not a regex: {exc}") from exc
    return doc.get("count_re") or (None if doc.get("fail_re") else DEFAULT_COUNT_RE), doc.get("fail_re")


def _gate(doc: Any) -> Gate:
    """`flow.test` (D652, D789): a map by name like `flow.measure`, the checks in the order
    written -- each a command or `{run, count_re, fail_re, timeout_s}`. A command alone is one
    check named `test`; a check named `build` refuses on any non-zero exit (did not build)."""
    if isinstance(doc, (str, list)):
        doc = {"test": doc}
    if not isinstance(doc, dict) or not doc:
        raise TaskError(_GATE_HELP)
    loose = sorted(k for k in doc if k in _CHECK_KEYS)
    if loose:
        raise TaskError(f"flow.test: {', '.join(loose)} is a check's setting, said under its name "
                        f"(test: {{run: ..., {loose[0]}: ...}}); flow.test is a map of checks by name")
    checks = []
    for name, c in doc.items():
        name, where = str(name), f"flow.test.{name}"
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name):
            raise TaskError(f"{where}: a check's name is letters, digits, _ and -")
        if isinstance(c, (str, list)):
            c = {"run": c}
        if not isinstance(c, dict):
            raise TaskError(f"{where}: a command, or {{run, count_re, fail_re, timeout_s}}")
        if "name" in c:
            raise TaskError(f"{where}: a check's name is its key")
        bad = sorted(set(c) - set(_CHECK_KEYS))
        if bad:
            raise TaskError(f"{where}: keys {bad} are not a check's; known: {', '.join(_CHECK_KEYS)}")
        if not c.get("run"):
            raise TaskError(f"{where} needs `run`: its command")
        count_re, fail_re = _patterns(c, where) if name != "build" or c.get("count_re") or c.get("fail_re") else (None, None)
        checks.append(Check(name, _command(c["run"], f"{where}.run"), count_re, fail_re,
                            float(c.get("timeout_s") or 120.0), builds=name == "build"))
    return Gate(checks)


def _gate_doc(gate: Gate) -> Any:
    """The gate as a document says it (D789): a map by name; a bare command when it is all."""
    out: dict[str, Any] = {}
    for c in gate:
        settings = {**({"count_re": c.count_re} if c.count_re and c.count_re != DEFAULT_COUNT_RE else {}),
                    **({"fail_re": c.fail_re} if c.fail_re else {}),
                    **({"timeout_s": c.timeout_s} if c.timeout_s != 120.0 else {})}
        out[c.name] = {"run": list(c.run), **settings} if settings else list(c.run)
    return out


#: What a nested sub-task takes from its parent when it does not say (D455). `subtasks` is
#: deliberately absent: a child that inherited it would divide again, forever.
_INHERITED = ("contract", "language", "gate", "stages", "objectives", "knowledge", "skills",
              "params", "workload", "budget", "space", "ladder",
              "flow")


def _knob_doc(knob: str, values: list, when: dict | None, found: tuple | None) -> Any:
    """A knob as a document says it: its choices, `when` it moves, and the files it also takes (D798)."""
    if found is None and not when:
        return list(values)
    out: dict[str, Any] = {"values": list(found[0]) if found is not None else list(values)}
    if found is not None:
        out["from"] = found[1]
    if when:
        out["when"] = dict(when)
    return out


def _space(raw: Any, base: Any = None) -> tuple[dict[str, list], dict[str, dict[str, list]], dict[str, tuple]]:
    """`space:` read and checked (D553): knob -> a non-empty list of scalar choices in the order
    written, or `{values: [...], when: {knob: [choices]}}` for a knob that only moves while
    those knobs hold one of those choices (elsewhere it stays at its first). A mapping without
    `values` is a component (D637): its knobs are `<component>.<knob>`, and `optional: true`
    adds `<component>.on` (off first), its knobs moving only while it is on."""
    if not raw:
        return {}, {}, {}
    if not isinstance(raw, dict):
        raise TaskError("space: a mapping of knob -> [choices], in a meaningful order")
    found: dict[str, tuple] = {}
    flat: list[tuple[str, Any, dict[str, list]]] = []
    for k, vals in raw.items():
        k = str(k)
        if isinstance(vals, dict) and "values" not in vals and "from" not in vals:
            comp = dict(vals)
            optional = comp.pop("optional", False)
            if not isinstance(optional, bool) or not (comp or optional):
                raise TaskError(f"space.{k}: a component is its knobs, and `optional: true` when the search may leave it out")
            if optional:
                flat.append((f"{k}.on", [False, True], {}))
            for kk, vv in comp.items():
                flat.append((f"{k}.{kk}", vv, {f"{k}.on": [True]} if optional else {}))
        else:
            flat.append((k, vals, {}))
    out: dict[str, list] = {}
    when: dict[str, dict[str, list]] = {}
    for k, vals, implied in flat:
        cond = dict(implied)
        if isinstance(vals, dict):
            if set(vals) - {"values", "when", "from"} or not isinstance(vals.get("when", {}), dict):
                raise TaskError(f"space.{k}: a list of choices, or {{values: [...], when: {{knob: [choices]}}, "
                                "from: <files>}}")
            cond.update({str(c): list(v) if isinstance(v, (list, tuple)) else [v] for c, v in (vals.get("when") or {}).items()})
            if "from" in vals:                     # D798: the choices also files beside the document, by name
                pattern = str(vals["from"])
                said = list(vals.get("values") or [])
                hits = sorted(Path(base).glob(pattern)) if base is not None and not Path(pattern).is_absolute() \
                    else sorted(Path("/").glob(pattern.lstrip("/"))) if Path(pattern).is_absolute() else []
                found[k] = (tuple(said), pattern)
                vals = said + [h.stem for h in hits if h.is_file() and h.stem not in said]
            else:
                vals = vals.get("values")
        if not isinstance(vals, (list, tuple)) or not vals:
            raise TaskError(f"space.{k}: a non-empty list of choices")
        if any(isinstance(v, (dict, list)) for v in vals):
            raise TaskError(f"space.{k}: choices are scalars (numbers or names)")
        out[k] = list(vals)
        if cond:
            when[k] = cond
    for k, cond in when.items():
        for c, allowed in cond.items():
            if c not in out or c == k:
                raise TaskError(f"space.{k}.when: {c} is not another knob of the space")
            bad = [v for v in allowed if v not in out[c]]
            if bad:
                raise TaskError(f"space.{k}.when.{c}: {bad} are not choices of {c}")
    return out, when, found


def point_doc(point: dict[str, Any]) -> dict[str, Any]:
    """A point as the `{point}` file says it (D637): a component's knobs under its name, an
    optional component only when it is on."""
    out: dict[str, Any] = {}
    for k, v in point.items():
        comp, _, knob = k.partition(".")
        if not knob:
            out[k] = v
        elif point.get(f"{comp}.on", True) is False:
            continue
        elif knob == "on":
            out.setdefault(comp, {})
        else:
            out.setdefault(comp, {})[knob] = v
    return out


def _seeds(raw: Any, space: dict[str, list]) -> tuple[dict[str, Any], ...]:
    """`seeds:` points of the space measured before the walk; a knob a seed leaves out is at
    its first choice."""
    if not raw:
        return ()
    if not isinstance(raw, list) or not all(isinstance(p, dict) for p in raw):
        raise TaskError("seeds: a list of points, each {knob: choice}")
    raw = [_flat_point(p, space) for p in raw]
    for i, p in enumerate(raw):
        for k, v in p.items():
            if k not in space:
                raise TaskError(f"seeds[{i}]: {k} is not a knob of the space")
            if v not in space[k]:
                raise TaskError(f"seeds[{i}].{k}: {v!r} is not one of its choices")
    return tuple(dict(p) for p in raw)


def _flat_point(p: dict[str, Any], space: dict[str, list]) -> dict[str, Any]:
    """A seed with components nested (`{bingo: {region_size: 2048}, sms: {on: true}}`) as knob
    names; an optional component is on only where the seed says `on: true`."""
    out: dict[str, Any] = {}
    for k, v in p.items():
        if isinstance(v, dict):
            out.update({f"{k}.{kk}": vv for kk, vv in v.items()})
        else:
            out[k] = v
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
    from .roles import ROLES, Roles, rig

    out = rig(**dict(task.roles)) if task.roles else Roles()
    if caller is None:
        return out
    for role in ROLES:
        who = getattr(caller, role)
        if who is not None:
            out = out.with_role(role, who)
    return out


def _point_name(point: dict[str, Any]) -> str:
    """A candidate's name from its point: the values joined, or a digest when that is long. A
    value that is not a word by itself (a number, a flag, a letter) carries its knob (D743):
    `list_sieve-wheel=1`, not `list_sieve-1`."""
    name = "-".join(str(v) if isinstance(v, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{2,}", v) else f"{k}={v}"
                    for k, v in point.items())
    if len(name) <= 60:
        return name
    return "p" + hashlib.sha1(json.dumps(point, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _write_point(artifact: Path, point: dict[str, Any]) -> str:
    """The `{point}` file beside the artifact: the point as JSON, components nested (D637)."""
    path = artifact.with_name(artifact.name + ".point.json")
    path.write_text(json.dumps(point_doc(point), indent=1, default=str))
    return str(path)


def _leaf(task_id: str) -> str:
    """A sub-task's work-item name: the last segment of its id, which is what a parent's
    orchestrator chooses from and what the record calls it."""
    return task_id.rsplit("/", 1)[-1]


def _stage(i: int, doc: Any) -> Stage:
    if not isinstance(doc, dict) or not isinstance(doc.get("name"), str) or not doc["name"]:
        raise TaskError(f"flow.measure: stage {i + 1} needs a name")
    at = f"flow.measure.{doc['name']}"                 # D775: a stage is said by its name
    cmd, ev = doc.get("command"), doc.get("evaluator")
    if cmd and ev:
        raise TaskError(f"{at} needs exactly one of `command` or `evaluator`, not both")
    if not cmd and not ev:
        raise TaskError(f"{at} needs exactly one of `command` or `evaluator`")
    needs = doc.get("needs")
    if needs is not None and (not isinstance(needs, list) or not all(isinstance(t, str) for t in needs)):
        raise TaskError(f"{at}.needs is a list of tool names")
    needs = list(needs or [])
    cmd = _command(cmd, f"{at}.command")
    rtl_tools = _flux_rtl_tools(cmd) if cmd else []
    for tool in rtl_tools if "needs" not in doc else ():    # D628: `flux rtl measure` says what it runs
        needs.append(tool)
    metrics = tuple(doc.get("metrics") or (() if "measure" not in rtl_tools_kind(cmd)
                                           else RTL_STAT_METRICS if _stage_of(list(cmd)) == "stat" else RTL_METRICS))
    if not all(isinstance(m, str) for m in metrics):
        raise TaskError(f"{at}.metrics is a list of metric names")
    metrics_re = dict(doc.get("metrics_re") or {})
    if cmd and not metrics_re and metrics:
        # `name=value` tokens (as `flux rtl measure` prints) need only the `metrics:` names
        # (D580); a token starts a line or follows whitespace, so `area_um2` never reads `xarea_um2`
        metrics_re = {m: rf"(?:^|(?<=\s)){re.escape(m)}=([-+0-9.eE]+)" for m in metrics}
    for m, pat in metrics_re.items():
        try:
            if re.compile(pat).groups < 1:
                raise TaskError(f"{at}.metrics_re[{m!r}] needs one capturing group")
        except re.error as exc:
            raise TaskError(f"{at}.metrics_re[{m!r}] is not a regex: {exc}") from exc
    if cmd and not metrics_re:
        raise TaskError(f"{at}: a command stage needs `metrics` (names the command "
                        "prints as `name=value` lines) or `metrics_re` (a regex per metric)")
    raw = doc.get("cutoff") or {}
    if isinstance(raw, dict):
        cutoff: dict[str, Any] | tuple[dict[str, Any], ...] = dict(raw)
        named = [(f"{at}.cutoff", cutoff)] if cutoff else []
    elif isinstance(raw, list) and all(isinstance(c, dict) for c in raw):
        cutoff = tuple(dict(c) for c in raw)        # several gates, all must pass (D657)
        named = [(f"{at}.cutoff[{j}]", c) for j, c in enumerate(cutoff)]
    else:
        raise TaskError(f"{at}.cutoff is one condition {{metric, at|below|within}} or a list of them")
    for where, rule in named:
        if not isinstance(rule.get("metric"), str):
            raise TaskError(f"{where} needs a `metric` naming one this stage measures")
        rules = [k for k in ("at", "below", "within") if k in rule]
        if len(rules) != 1:
            raise TaskError(
                f"{where} needs exactly one of `at` (a floor), `below` (a budget) or "
                f"`within` (a fraction of this run's best), got {sorted(rule)}")
        if not isinstance(rule[rules[0]], (int, float)) or isinstance(rule[rules[0]], bool):
            raise TaskError(f"{where}.{rules[0]} must be a number")
        if rules[0] == "within" and not 0 < float(rule["within"]) <= 1:
            raise TaskError(f"{where}.within must be a fraction in (0, 1]")
    return Stage(name=doc["name"], command=cmd, metrics_re=metrics_re,
                evaluator=ev, metrics=metrics or tuple(metrics_re),
                timeout_s=float(doc.get("timeout_s") or 600.0), cutoff=cutoff, needs=tuple(needs),
                estimate=_estimator(at, doc.get("estimate")))


def _estimator(at: str, raw: Any) -> Estimator | None:
    """`flow.measure.<stage>.estimate` (D665): `{kind: surrogate|command|model, margin: 0.05, command: ...}`,
    `command` for kind command only."""
    if raw is None:
        return None
    where = f"{at}.estimate"
    if not isinstance(raw, dict):
        raise TaskError(f"{where} is {{kind: {'|'.join(ESTIMATE_KINDS)}, margin: 0.05}}")
    bad = sorted(set(raw) - {"kind", "margin", "command"})
    if bad:
        raise TaskError(f"{where} keys {bad} are not known; known: kind, margin, command")
    kind = raw.get("kind")
    if kind not in ESTIMATE_KINDS:
        raise TaskError(f"{where}.kind is one of {', '.join(ESTIMATE_KINDS)}, not {kind!r}")
    margin = raw.get("margin", 0.05)
    if isinstance(margin, bool) or not isinstance(margin, (int, float)) or margin < 0:
        raise TaskError(f"{where}.margin is a number >= 0 (a fraction of the threshold), not {margin!r}")
    if (kind == "command") != ("command" in raw):
        raise TaskError(f"{where}.command is said for kind command, and only then")
    cmd = _command(raw["command"], f"{where}.command") if kind == "command" else None
    return Estimator(kind, float(margin), cmd)


#: Every top-level key a problem document may say; any other is refused with the nearest
#: real key (D590).
DOCUMENT_KEYS = frozenset({
    "statement", "contract", "language", "parts",
    "flow", "subtasks", "max_subtasks", "objectives",
    "budget", "params", "workload", "ladder",
    "skills"})
#: The fields `flow`'s boxes are read into (D775): the loop's own, never a document's key.
_LIFTED_KEYS = frozenset({"gate", "stages", "space", "seeds", "knowledge"})

#: set by the loader, never written: a sub-document's record name, `<parent>/<child>` (D455)
_INTERNAL_KEYS = frozenset({"_record", "_lifted", "_inherited"})

#: the file extension a language's candidates are written with (D628); another language `x`
#: writes `.x`
EXTENSIONS = {"systemverilog": ".sv", "verilog": ".v", "vhdl": ".vhd", "python": ".py", "text": ".txt",
              "yaml": ".yaml", "json": ".json", "c": ".c", "cpp": ".cpp", "c++": ".cpp", "cuda": ".cu", "opencl": ".cl", "rust": ".rs",
              "markdown": ".md", "shell": ".sh", "bash": ".sh", "chisel": ".scala", "scala": ".scala"}


#: D786: a problem is a folder; its document is this file in it, and the folder's name is its id.
DOCUMENT_FILE = "problem.yaml"
#: D787: beside it, `NAME.problem.yaml` -- another problem of the same loop, its record `<id>.NAME`.
ALT_SUFFIXES = (".problem.yaml", ".problem.yml")


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


def _substitute(cmd: Iterable[str], subs: dict[str, str]) -> list[str]:
    return [_PLACEHOLDER.sub(lambda m: subs.get(m.group(1), m.group(0)), tok) for tok in cmd]


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


# ------------------------------------------------------------------ the flow (D542)
#: The boxes of the drawing a document may say a half for, in flow order.
FLOW_BOXES = ("validate", "orchestrate", "plan", "dse", "generate", "test", "critique",
              "calibrate", "select", "feedback", "knowledge", "extract")
_FLOW_WORDS = {"validate": ("rules", "llm"), "test": ("gate",), "critique": ("none", "llm"), "plan": ("none", "llm"),
               "calibrate": ("on", "off"), "select": ("objectives",), "feedback": ("human", "none"),
               "extract": ("none", "mined")}
#: What `flow.knowledge` may name (D648): the library is on by default; `none` turns it off.
_KNOWLEDGE_SOURCES = ("sheet", "library", "none")


#: D791: what is read (files, sheet, text), who digests the library (agent), or off -- the
#: library is the loop's `library/` folder and the operator's, digested whenever it is on.
_KNOWLEDGE_KEYS = ("files", "sheet", "text", "off", "agent")



#: D795: every box says who works it the same way -- a word (`rules`, `model`, `off`, ...) or
#: `{by: <who>, ...its settings}`, `by` a word, an agent preset (opencode, claude, codex) or an
#: agent of one's own (`{command: [...]}`), the agent's options (session, timeout_s, ...) beside.


def _agents() -> tuple[str, ...]:
    """The agents a box may name: the presets, and those the server adds (D807)."""
    from .agent import agent_kinds

    return tuple(agent_kinds())
_DELEGABLE = frozenset({"validate", "orchestrate", "plan", "dse", "generate", "critique", "extract", "select", "knowledge"})
_AGENT_OPTS = ("session", "timeout_s", "questions", "max_questions", "wait_s", "bin", "args", "probe", "allow",
               "output", "resume", "name")
#: the words each box takes on the surface, and what the loop calls them inside
_BY_WORDS = {"validate": {"rules": "rules", "model": "llm"},
             "orchestrate": {"rules": "rules", "given": "given", "model": "llm", "tools": "agent"},
             "plan": {"off": "none", "model": "llm"},
             "critique": {"off": "none", "model": "llm"},
             "generate": {"model": "model"},
             "select": {"objectives": "objectives"},
             "extract": {"off": "none", "mined": "mined"},
             "feedback": {"human": "human", "off": "none"},
             "calibrate": {"on": "on", "off": "off"},
             "knowledge": {"off": "off", "model": None}}
_BY_SETTINGS = {"generate": ("command", "catalog"), "select": ("finalists",), "dse": ("policy", "space", "seeds"),
                "knowledge": ("files", "sheet", "text", "off")}


def _who(box: str, by: Any, opts: dict[str, Any]) -> Any:
    """An agent spec from `by` and the options beside it, or the box's inside word for a word."""
    if isinstance(by, dict):
        return {**by, **opts}
    if by in _agents():
        return {"preset": by, **opts} if opts else by
    raise TaskError(f"flow.{box}.by is " + " | ".join([*(_BY_WORDS.get(box) or {}), *_agents()])
                    + f" or an agent of your own ({{command: [...]}}), not {by!r}")


def _by_surface(flow: dict[str, Any]) -> dict[str, Any]:
    """D795: the boxes as a document says them, read into the forms the loop keeps. D796: the
    record's lessons are `knowledge.lessons`, kept inside as the `extract` box."""
    if "extract" in flow:
        raise TaskError("flow.extract is `knowledge: {lessons: mined}` (or `{lessons: claude}`) (D796)")
    if "dse" in flow:
        raise TaskError("flow.dse is `orchestrate`: `orchestrate: {policy: sweep, space: {...}}` (D797)")
    flow = dict(flow)
    o = flow.get("orchestrate")                            # D797: a search is the orchestrator's choice
    if isinstance(o, dict) and "command" in o and isinstance(o["command"], (str, list)):
        # D799: a search a command runs -- its rounds' candidates, its conclusion
        rest = {k: v for k, v in o.items() if k != "command"}
        bad = sorted(set(rest) - {"timeout_s"})
        if bad:
            raise TaskError(f"flow.orchestrate: a command's search takes `command` and `timeout_s`, not {bad}")
        flow["orchestrate"] = o = {"command": {"run": o["command"], **rest}}
    if isinstance(o, list) or (isinstance(o, str) and o in _dse_words()) \
            or (isinstance(o, dict) and ({"policy", "space", "seeds"} & set(o) or set(o) & set(_dse_words()))):
        flow["dse"] = flow.pop("orchestrate")
    k = flow.get("knowledge")
    if isinstance(k, dict) and "lessons" in k:
        k = dict(k)
        flow["extract"] = k.pop("lessons")
        if k:
            flow["knowledge"] = k
        else:
            flow.pop("knowledge")
    out = dict(flow)
    for box, value in flow.items():
        if box in ("test", "measure") or box not in FLOW_BOXES:
            continue                                           # the loader says what is no box
        words = _BY_WORDS.get(box, {})
        if value is False:                                   # YAML reads a bare `off` as false
            value = "off"
        if isinstance(value, str):
            if box == "dse":
                if value in ("llm", "agent"):
                    raise TaskError("flow.dse: a model or an agent proposing points is `{by: model}` or `{by: claude}` (D795)")
                continue                                       # a policy's name
            if value in _agents():
                value = {"by": value}
            elif box == "orchestrate" and value not in ("llm", "agent", "none") and value not in words:
                continue                                       # a registered orchestrator; the roles say if not
            elif value not in words:
                raise TaskError(f"flow.{box} is " + " | ".join([*words, *(_agents() if box in _DELEGABLE else ())])
                                + (" or {by: ..., ...}" if box in _DELEGABLE else "") + f", not {value!r} (D795)")
            else:
                inner = words[value]
                if inner is None:
                    out.pop(box)
                else:
                    out[box] = inner
                continue
        if isinstance(value, list):
            continue                                           # dse phases
        if box == "dse" and isinstance(value, dict) and ("llm" in value or value.get("policy") == "llm"):
            raise TaskError("flow.dse: the model proposing points is `{by: model, ...its options}` (D795)")
        if not isinstance(value, dict):
            continue
        if "agent" in value:
            raise TaskError(f"flow.{box}: who works it is `by` -- {{by: {value['agent'] if isinstance(value['agent'], str) else 'claude'}}} (D795)")
        if "by" not in value:
            continue                                           # its settings alone (generate's command, dse's space, ...)
        v = dict(value)
        by = v.pop("by")
        settings = {k: v.pop(k) for k in list(v) if k in _BY_SETTINGS.get(box, ())}
        if box == "dse" and by == "model" and v:           # the model's search, with its own options
            cfg, v = dict(v), {}
            out[box] = {**settings, "policy": {"llm": cfg}}
            continue
        opts = {k: v.pop(k) for k in list(v) if k in _AGENT_OPTS}
        if v:
            raise TaskError(f"flow.{box}: {', '.join(sorted(v))} is not a setting of this box or an agent's")
        if by == "model" or (isinstance(by, str) and by in words):
            if opts:
                raise TaskError(f"flow.{box}: {', '.join(sorted(opts))} is an agent's option, not {by}'s")
            inner = words.get(by)
            if box == "dse":
                out[box] = {**settings, "policy": "llm"}
            elif settings:                                     # the reading, the finalists: the word is the default
                out[box] = settings
            elif inner is None:
                out.pop(box)
            else:
                out[box] = inner
            continue
        if box not in _DELEGABLE:
            raise TaskError(f"flow.{box} is not a box an agent answers; those are {', '.join(sorted(_DELEGABLE))}")
        spec = _who(box, by, opts)
        if box == "dse":
            out[box] = {**settings, **({"policy": {"agent": spec}} if settings else {"agent": spec})}
        else:
            out[box] = {**settings, "agent": spec}
    return out


def _by_doc(spec: Any, more: dict[str, Any]) -> Any:
    """An agent spec written as `by` (D795): the preset's name alone when that is all."""
    if isinstance(spec, dict) and spec.get("preset"):
        who = {"by": spec["preset"], **{k: x for k, x in spec.items() if k != "preset"}}
    else:
        who = {"by": spec}
    return who["by"] if len(who) == 1 and not more and isinstance(who["by"], str) else {**who, **more}


def _dse_words() -> tuple[str, ...]:
    """The search policies a document names by word (D797: as its `orchestrate`)."""
    from .roles import available_roles

    return tuple(n for n in available_roles("orchestrator") if n not in ("rules", "given", "llm", "agent", "model"))


def _by_layout(flow: dict[str, Any]) -> dict[str, Any]:
    """D795: the boxes as the loop keeps them, written the way a document says them; D796: the
    `extract` box as `knowledge.lessons`."""
    out: dict[str, Any] = {}
    for box, value in flow.items():
        words = {inner: word for word, inner in (_BY_WORDS.get(box) or {}).items() if inner is not None}
        rest = {k: x for k, x in value.items() if k not in ("agent", "policy")} if isinstance(value, dict) else {}
        if isinstance(value, str) and box != "dse" and value in words:
            out[box] = words[value]
        elif isinstance(value, dict) and "agent" in value:
            got = _by_doc(value["agent"], rest)
            out[box] = {"by": got} if box == "dse" and isinstance(got, str) else got      # a dse word is a policy
        elif box == "dse" and value == "llm":
            out[box] = {"by": "model"}
        elif box == "dse" and isinstance(value, dict) and "llm" in value:      # the model's search, its options
            out[box] = {"by": "model", **(value["llm"] or {}), **{k: x for k, x in value.items() if k != "llm"}}
        elif box == "dse" and isinstance(value, dict) and isinstance(value.get("policy"), dict) and set(value["policy"]) == {"llm"}:
            out[box] = {"by": "model", **(value["policy"]["llm"] or {}), **rest}
        elif box == "dse" and isinstance(value, dict) and value.get("policy") == "llm":
            out[box] = {"by": "model", **rest}
        elif box == "dse" and isinstance(value, dict) and isinstance(value.get("policy"), dict) and "agent" in value["policy"]:
            got = _by_doc(value["policy"]["agent"], rest)
            out[box] = {"by": got} if isinstance(got, str) else got
        else:
            out[box] = value
    if "dse" in out:                                       # D797: the search, as the orchestrator
        dse = out.pop("dse")
        if isinstance(dse, dict) and set(dse) == {"command"} and isinstance(dse["command"], dict):
            dse = {"command": dse["command"].get("run"), **{k: v for k, v in dse["command"].items() if k != "run"}}
        out["orchestrate"] = {"by": "model"} if dse == "model" else dse
    lessons = out.pop("extract", "off")
    if lessons != "off":
        k = out.get("knowledge")
        if k is None:
            k = {}
        elif k == "off":
            k = {"off": True}
        elif isinstance(k, str):
            k = {"by": k}
        out["knowledge"] = {**k, "lessons": lessons}
    return out


def _lift(doc: dict[str, Any]) -> dict[str, Any]:
    """D775: each box's own settings, said under `flow`, read into the fields the loop keeps:
    `flow.test` the gate, `flow.measure` the stages (a map: a stage's name to its command or its
    settings), `flow.dse` its space and seeds beside its policy, `flow.knowledge` what is read
    and who digests it, `flow.select` its finalists. The fields themselves are not a document's keys."""
    if doc.get("_lifted"):
        return doc
    strict = not doc.get("_inherited")                     # an inherited parent's fields are the loop's already
    if strict:
        said = sorted(set(doc) & _LIFTED_KEYS)
        if said:
            raise TaskError(f"keys a problem document does not have: {', '.join(said)}")
        knobs = sorted(set(doc.get("budget") or {}) & {"finalists", "calibrate"}) if isinstance(doc.get("budget"), dict) else []
        if knobs:
            raise TaskError(f"budget keys {knobs} are not loop knobs")
    doc = {**doc, "_lifted": True}
    raw = doc.get("flow")
    if not isinstance(raw, dict):
        return doc
    flow = _by_surface(raw) if strict else dict(raw)
    for box in ("test", "measure"):
        if isinstance(raw.get(box), dict) and ("agent" in raw[box] or "by" in raw[box]):
            raise TaskError(f"flow.{box} is never delegated to an agent: it establishes facts (D460)")
    if "test" in flow:
        doc["gate"] = flow.pop("test")
    if "measure" in flow:
        m = flow.pop("measure")
        if not isinstance(m, dict):
            raise TaskError("flow.measure is a map: each stage's name to its command, or to its settings "
                            "(command, metrics, needs, timeout_s, cutoff, estimate), in the order they run")
        stages = []
        for name, spec in m.items():
            if isinstance(spec, (str, list)):
                stages.append({"name": str(name), "command": spec})
            elif isinstance(spec, dict):
                if "name" in spec:
                    raise TaskError(f"flow.measure.{name}: a stage's name is its key, not a `name:`")
                stages.append({"name": str(name), **spec})
            elif spec is None:
                stages.append({"name": str(name)})
            else:
                raise TaskError(f"flow.measure.{name} is its command or its settings, not {spec!r}")
        doc["stages"] = stages
    if isinstance(flow.get("dse"), dict) and {"space", "seeds", "policy"} & set(flow["dse"]):
        d = dict(flow["dse"])
        if "space" in d:
            doc["space"] = d.pop("space")
        if "seeds" in d:
            doc["seeds"] = d.pop("seeds")
        if "policy" in d:
            if len(d) > 1:
                raise TaskError(f"flow.dse: a `policy` or an `agent`, not {sorted(d)}")
            flow["dse"] = d.pop("policy")
        elif d:
            flow["dse"] = d
        else:
            flow.pop("dse")
    if "knowledge" in flow:
        k = flow["knowledge"]
        if k == "off" or k is False:                       # YAML reads a bare `off` as false
            flow["knowledge"] = ["none"]
        elif isinstance(k, dict) and not (set(k) == {"agent"} and not strict):
            k = dict(k)
            bad = sorted(set(k) - set(_KNOWLEDGE_KEYS))
            if bad:
                raise TaskError(f"flow.knowledge keys {bad} are not known; known: {', '.join(_KNOWLEDGE_KEYS)}")
            read = {x: k[x] for x in ("files", "sheet", "text") if x in k}
            if read:
                doc["knowledge"] = read
            if k.get("off") and (k.get("agent") is not None or read):
                raise TaskError("flow.knowledge `off: true` stands alone: it turns the reading off")
            if k.get("agent") is not None:
                flow["knowledge"] = {"agent": k["agent"]}
            elif k.get("off"):
                flow["knowledge"] = ["none"]
            else:
                flow.pop("knowledge")
        elif strict and not isinstance(k, dict):
            raise TaskError("flow.knowledge is `off` or an object: files, sheet, text (what is read), "
                            f"agent: <who digests the library> (D791), not {k!r}")
    if isinstance(flow.get("select"), dict) and "finalists" in flow["select"]:
        sel = dict(flow["select"])
        doc["budget"] = {**(doc.get("budget") or {}), "finalists": sel.pop("finalists")}
        if sel:
            flow["select"] = sel
        else:
            flow.pop("select")
    doc["flow"] = flow
    if not flow:
        doc.pop("flow")
    return doc


def _layout(doc: Any) -> Any:
    """D775: the loop's fields -- gate, stages, space, seeds, knowledge, finalists, calibrate --
    as a document says them, each under its box in `flow`: what `to_dict` writes."""
    if not isinstance(doc, dict):
        return doc
    out = {k: v for k, v in doc.items() if k not in _LIFTED_KEYS and k not in ("_lifted", "_inherited")}
    flow = dict(doc.get("flow") or {})
    budget = dict(doc["budget"]) if isinstance(doc.get("budget"), dict) else None
    if flow.get("test") == "gate":
        flow.pop("test")
    if doc.get("gate") not in (None, "", [], {}):
        flow["test"] = doc["gate"]
    stages = doc.get("stages")
    if isinstance(stages, list) and stages:
        measure: dict[str, Any] = {}
        for st in stages:
            st = dict(st)
            name = str(st.pop("name"))
            if st.get("timeout_s") == 600:                 # the default, said by the writer
                st.pop("timeout_s")
            measure[name] = st["command"] if set(st) == {"command"} else (st or None)
        flow["measure"] = measure
    if doc.get("space") or doc.get("seeds"):
        dse = flow.get("dse")
        new: dict[str, Any] = {}
        if isinstance(dse, dict):
            new.update(dse)
        elif dse is not None:
            new["policy"] = dse
        if doc.get("space"):
            new["space"] = doc["space"]
        if doc.get("seeds"):
            new["seeds"] = doc["seeds"]
        flow["dse"] = new
    k, fk = doc.get("knowledge"), flow.get("knowledge")
    know: dict[str, Any] = {}
    if isinstance(k, str) and k:
        know["text"] = k
    elif isinstance(k, dict):
        know.update({x: v for x, v in k.items() if x in ("files", "sheet", "text") and v not in (None, "", [])})
    if isinstance(fk, dict) and "agent" in fk:
        know["agent"] = fk["agent"]
    elif fk is not None and not isinstance(fk, dict):
        srcs = [fk] if isinstance(fk, str) else list(fk)
        if "none" in srcs or "off" in srcs:
            know["off"] = True
    elif isinstance(fk, dict):
        know.update(fk)
    if know:
        flow["knowledge"] = "off" if know == {"off": True} else know
    else:
        flow.pop("knowledge", None)
    if budget is not None and "finalists" in budget:
        sel = flow.get("select")
        flow["select"] = {**(sel if isinstance(sel, dict) else {}), "finalists": budget.pop("finalists")}
    if budget is not None and "calibrate" in budget:
        if budget.pop("calibrate") is False:
            flow["calibrate"] = "off"
    if budget is not None:
        if budget:
            out["budget"] = budget
        else:
            out.pop("budget", None)
    if isinstance(out.get("subtasks"), list):
        out["subtasks"] = [_layout(c) for c in out["subtasks"]]
    if flow:
        out["flow"] = _by_layout(flow)
    else:
        out.pop("flow", None)
    return out


def _flow(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """`flow:` (D542), one key per box of the drawing naming its half, read, checked and folded
    into the internal `roles`, `generator` and `critique` (D629: `flow` is the only place a
    document says them)."""
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
    roles: dict[str, Any] = {}
    from .boxes import DELEGABLE, NEVER

    for box, value in raw.items():
        if not (isinstance(value, dict) and "agent" in value) or box in ("generate", "knowledge"):   # their own (D773)
            continue
        if box in NEVER:
            raise TaskError(f"flow.{box} is never delegated to an agent: it establishes facts (D460)")
        if box not in DELEGABLE:
            raise TaskError(f"flow.{box} is not a box an agent answers; those are {', '.join(sorted(DELEGABLE))}")
        if set(value) != {"agent"}:
            raise TaskError(f"flow.{box} is {{agent: <preset or spec>}}, not {sorted(value)}")
        from .agent import agent_spec

        try:
            agent_spec(value["agent"])
        except ValueError as exc:
            raise TaskError(f"flow.{box}.agent: {exc}") from exc
        flow[box] = dict(value)
    for box, words in _FLOW_WORDS.items():
        if box in raw and box not in flow:
            value = raw[box]
            if isinstance(value, bool) and box == "calibrate":
                value = "on" if value else "off"
            if value not in words:
                raise TaskError(f"flow.{box} is one of {', '.join(words)}, not {value!r}"
                                + (" (the gate is never delegated, D460)" if box == "test" else ""))
            flow[box] = value
    if flow.get("plan") == "llm" or isinstance(flow.get("plan"), dict):
        # the model writes the loop plan (parts, order, method, budgets) before a step is
        # spent (D577); `budget.agent` carries the half
        budget = dict(doc.get("budget") or {})
        halves = list(budget.get("agent") or [])
        if "plan" not in halves:
            halves.append("plan")
        budget["agent"] = halves
        doc["budget"] = budget
    if "orchestrate" in raw:
        value = raw["orchestrate"]
        # a coding agent picks through the agent orchestrator (D640)
        roles["orchestrator"] = {"agent": {"coding": value["agent"]}} if isinstance(value, dict) and "agent" in value else value
        flow["orchestrate"] = value
    if "dse" in raw:
        value = raw["dse"]
        if isinstance(value, dict) and set(value) == {"agent"}:
            value = {"llm": {"agent": value["agent"]}}      # a coding agent proposes the points (D640)
        if isinstance(value, list):                              # D583: phases, in order
            from .dse import validate_phase

            for i, spec in enumerate(value):
                try:
                    validate_phase(spec)
                except ValueError as exc:
                    raise TaskError(f"flow.dse[{i}]: {exc}") from exc
            value = {"phases": {"phases": value}}
        elif isinstance(value, str) and ":" in value:          # D602: a policy of your own
            from .dse import validate_phase

            try:
                validate_phase(value)
            except ValueError as exc:
                raise TaskError(f"flow.dse: {exc}") from exc
            value = {"phases": {"phases": [value]}}
        if value != "none":
            from .roles import available_roles

            name = value if isinstance(value, str) else next(iter(value), None) if isinstance(value, dict) else None
            if name == "llm":                                   # D554: the model's half of the box
                name = "model"
                value = "model" if isinstance(value, str) else {"model": value["llm"]}
            policies = [n for n in available_roles("orchestrator") if n not in ("rules", "given", "llm", "agent")]
            if name not in policies:
                raise TaskError(f"flow.dse {value!r}: no such DSE policy is registered; "
                                f"registered: {', '.join(policies)}, and llm (the model proposes points) (D553)")
            if "orchestrator" in roles:
                raise TaskError("a DSE policy IS the orchestrator: say `flow.dse` or `flow.orchestrate`, not both")
            roles["orchestrator"] = value
        flow["dse"] = raw["dse"]
    if "generate" in raw:
        value = raw["generate"]
        if value == "model":
            pass
        elif isinstance(value, dict) and len(value) == 1 and next(iter(value)) in ("command", "catalog", "agent"):
            doc["generator"] = dict(value)
        else:
            raise TaskError('flow.generate is "model", {command: [...]}, {catalog: [...]} or {agent: claude|codex|opencode|{...}}, '
                            f"not {value!r}")
        flow["generate"] = value
    if "knowledge" in raw:
        sources = raw["knowledge"]
        if isinstance(sources, str):
            sources = [sources]
        if isinstance(sources, dict):                          # D773: the papers digested by a coding agent
            try:
                if set(sources) != {"agent"}:
                    raise ValueError
                from .agent import agent_spec

                agent_spec(sources["agent"])
            except (ValueError, TypeError) as exc:
                raise TaskError("flow.knowledge is a list from " + ", ".join(_KNOWLEDGE_SOURCES)
                                + f" or {{agent: opencode|claude|codex|{{...}}}} (it digests the papers), not {sources!r}") from exc
            flow["knowledge"] = {"agent": sources["agent"]}
        else:
            if not isinstance(sources, list) or not all(s in _KNOWLEDGE_SOURCES for s in sources):
                raise TaskError(f"flow.knowledge is a list from {', '.join(_KNOWLEDGE_SOURCES)} or {{agent: …}}, not {sources!r}")
            if "none" in sources and len(sources) > 1:
                raise TaskError("flow.knowledge `none` stands alone: it turns the library off")
            flow["knowledge"] = list(sources)
    # the model-side knowledge: `digest` from the library, `mined` from the record (extract)
    # the record's lessons (extract); the library's digest is the problem's own whenever it is on (D791)
    wanted = ["mined"] if flow.get("extract") == "mined" else []
    lessons = flow["extract"]["agent"] if isinstance(flow.get("extract"), dict) else None
    if lessons is not None:                                    # the agent's lessons, beside the rest (D640)
        roles["knowledge"] = {"sources": {"names": [*wanted, "agent"], "agent": lessons}}
    elif wanted:
        roles["knowledge"] = wanted[0] if len(wanted) == 1 else {"sources": {"names": wanted}}
    doc["roles"] = roles
    return flow, doc


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


def _dse_policies(value: Any) -> list[str]:
    """The policy names `flow.dse` runs: one name, `{name: cfg}`, or a list of phases."""
    specs = value if isinstance(value, list) else [value] if value else []
    names = []
    for spec in specs:
        if isinstance(spec, str):
            names.append(spec)
        elif isinstance(spec, dict):
            names.append(str(spec.get("policy", "gradient")) if isinstance(value, list) else str(next(iter(spec), "")))
    return names


def _agent_tool(spec: Any) -> str:
    from .agent import agent_spec

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
            from .types import LoopRequest, LoopState

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
    from .roles import available_roles

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
    from .roles import available_roles

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
