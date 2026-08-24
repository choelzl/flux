"""The problem document (D519): what a `*.problem.yaml` says, how it is loaded and
checked, and the vocabulary of its `flow:` -- a statement, a contract, parts, a gate (how a
candidate is checked), costed stages (how it is measured), objectives (what "better" means), a
budget, a `space:` of knobs, a `world:` for what prose and numbers cannot say.

Commands carry placeholders: `{artifact}` (the candidate written to a file), `{home}` (the
document's folder), `{workdir}`, `{name}`, `{part}`, `{python}` (this interpreter), and `{knob}`
for each knob of `space:`. A gate's `test` prints its failures; `count_re` (one integer group)
or `fail_re` (one match per failure) says how the loop counts them, and a non-zero exit with
nothing counted is one failure. A test that exits 3 says the candidate did not build (D594).

A world holds what a document cannot say (blocks, a transpiler, an exhaustive judge, how parts
compose or are measured): `world: module:World`, a callable taking the problem and returning an
object whose methods are `Problem` hooks (`contract_lines` lists them by box).
`hooks: {judge: module:callable}` replaces one hook. `flux_loop.task.PromptProblem` runs a document.
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

from .problem import Problem
from .objective import Objective, Objectives
from .types import (LoopRequest)

if TYPE_CHECKING:  # pragma: no cover
    from .roles import Roles

__all__ = ["BUILD_FAILED", "BUILTIN_SUBS", "DOCUMENT_KEYS", "DOCUMENT_OWNED", "FLOW_BOXES", "Gate", "Part", "Stage", "TaskError", "TaskSpec", "contract_lines", "describe_flow", "load_task", "loop_owned", "read_input", "request_for", "resolve", "world_hooks"]

#: `{name}` in a command: the loop's own (`BUILTIN_SUBS`) or a knob of `space:` (D581);
#: a name neither is stays as written (a script's own braces are its business)
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")
BUILTIN_SUBS = ("artifact", "workdir", "name", "part", "python", "home", "failure", "attempt",
                "prompt", "prompt_file", "point")


class TaskError(ValueError):
    """The document is not a task: the message names the field and what it should be."""


@dataclass(frozen=True)
class Part:
    name: str
    statement: str = ""




#: what `flux rtl measure` prints (D628): a stage running it need not list them
RTL_METRICS = ("fmax_mhz", "area_um2", "power_w", "cell_count")


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
    """The tools a `flux rtl test|measure` command runs (D600): they may be missing outside the
    Nix dev shell, and the command itself is Python, so `task check` must name them."""
    toks = list(cmd)
    try:
        at = toks.index("rtl")
    except ValueError:
        return []
    if at == 0 or "flux" not in " ".join(toks[:at]):
        return []
    sub = toks[at + 1] if at + 1 < len(toks) else ""
    if sub == "test":
        return ["verilator"]
    if sub == "measure":
        return ["yosys", "openroad"]      # synthesis too: its timing is OpenROAD's OpenSTA
    return []

#: D594: a gate test's exit code for "the candidate did not build" (nothing was tested).
BUILD_FAILED = 3


def _digest_of(text: str | None) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode()).hexdigest()

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

    `cutoff` is what is worth the next stage (D454): one of `{"metric": m, "at": x}` (a floor),
    `{"metric": m, "below": x}` (a budget) or `{"metric": m, "within": f}` (a band around this
    run's best, `f` a fraction). Without one, only the last stage's results decide."""

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
    brief: bool = False                  # "brief": "propose" -- the orchestrator briefs each part
    critique: bool = False               # flow critique: llm -- a model critic judges (D433)
    gate: Gate = field(default_factory=Gate)
    stages: tuple[Stage, ...] = ()
    objectives: tuple[Objective, ...] = ()
    knowledge: str = ""
    joiner: str = "\n\n"                 # how admitted parts compose, in `parts` order
    budget: dict[str, Any] = field(default_factory=dict)      # LoopRequest overrides
    params: dict[str, Any] = field(default_factory=dict)      # the problem's own settings
    #: The design space (D553): knob -> its choices in a meaningful order, what a `flow.dse`
    #: policy searches; a world may compute its own instead (`space(state)`).
    space: dict[str, list] = field(default_factory=dict)
    when: dict[str, dict[str, list]] = field(default_factory=dict)   # knob -> {knob: choices} it moves under
    seeds: tuple[dict[str, Any], ...] = ()     # points measured before the walk; the rest from the first choices
    skills: tuple[str, ...] = ()                  # D588: skill folders (absolute), for the model and the agents
    workload: Any = None                 # for evaluator stages: a Workload IR document or path
    # the world the document runs in (D519)
    world: str = ""                      # "package.module:World" -- (problem) -> an object of hooks
    #: The flow (D542): one key per box of the drawing naming its half, normalised; what it
    #: implies is folded into `roles`, `generator`, `critique`, `budget.calibrate`.
    flow: dict[str, Any] = field(default_factory=dict)
    #: The measurement cache sidecar beside the record (D541): `true` = `<id>.json`, `false` =
    #: none (a world that measures in microseconds, or owns its caching), a string = that name.
    cache: bool | str = True
    hooks: dict[str, str] = field(default_factory=dict)       # hook name -> "module:callable" (problem, ...)
    record: str = ""                     # the record's name: the id, `<parent>/<child>` for a sub-document
    ladder: Any = None                   # True, or the `flux_loop.Ladder` fields; None = no ladder
    knowledge_sheet: str = ""            # where `knowledge` was read from, for the report
    #: The directory the document was loaded from ("" inline); every artifact of a run lives
    #: under `<home>/out/`, never beside the source (D578).
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
        if base is not None:
            _HOMES.append(str(Path(base).resolve()))       # D602: its modules resolve beside it
        unknown = sorted(set(doc) - DOCUMENT_KEYS - _INTERNAL_KEYS) if isinstance(doc, dict) else []
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
        generator = dict(doc.get("generator") or {})      # from flow.generate: one of command, catalog, agent
        if "command" in generator:
            generator = {"command": list(_command(generator["command"], "flow.generate.command"))}
        elif "agent" in generator:
            from .agent import agent_spec

            try:
                agent_spec(generator["agent"])
            except ValueError as exc:
                raise TaskError(f"flow.generate.agent: {exc}") from exc
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
        space, when = _space(doc.get("space"))
        seeds = _seeds(doc.get("seeds"), space)
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
        if isinstance(knowledge, dict):
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
                    if name not in declared and name != "surrogate":
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
        return cls(
            id=tid.strip(), statement=statement.strip(), contract=str(doc.get("contract") or ""),
            language=language, extension=ext,
            parts=tuple(parts), decompose=decompose, max_parts=int(doc.get("max_parts") or 8),
            generator=dict(generator), roles=dict(roles), flow=dict(flow),
            subtasks=tuple(subtasks), split=split,
            max_subtasks=int(doc.get("max_subtasks") or 4),
            brief=doc.get("brief") in ("propose", True),
            critique=flow.get("critique") == "llm",
            gate=gate, stages=tuple(stages), objectives=tuple(objectives),
            knowledge=str(knowledge), joiner=str(doc.get("joiner") or "\n\n"),
            budget=budget, params=dict(doc.get("params") or {}), space=space, when=when, seeds=seeds,
            workload=doc.get("workload"), home=str(Path(base).resolve()) if base is not None else "",
            world=world, cache=cache, hooks=hooks, record=record, ladder=ladder if ladder else None,
            knowledge_sheet=sheet,
            skills=skills,
        )

    def _to_dict(self) -> dict[str, Any]:
        gate = {k: (list(v) if isinstance(v, tuple) else v)
                for k, v in self.gate.__dict__.items() if v is not None}
        return {
            "id": self.id, "statement": self.statement, "contract": self.contract,
            "language": self.language,
            "parts": ("decompose" if self.decompose
                      else [{"name": p.name, "statement": p.statement} for p in self.parts]),
            **({"max_parts": self.max_parts} if self.decompose else {}),
            **({"flow": dict(self.flow)} if self.flow else {}),
            **({"subtasks": "decompose", "max_subtasks": self.max_subtasks} if self.split else
               {"subtasks": [c.to_dict() for c in self.subtasks]} if self.subtasks else {}),
            **({"brief": "propose"} if self.brief else {}),
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
            "budget": dict(self.budget), "params": dict(self.params), "space": {k: ({"values": list(v), "when": dict(self.when[k])} if k in self.when else list(v))
                                                                        for k, v in self.space.items()},
            **({"seeds": [dict(p) for p in self.seeds]} if self.seeds else {}),
            **({"workload": self.workload} if self.workload is not None else {}),
            **({"world": self.world} if self.world else {}),
            **({"cache": self.cache} if self.cache is not True else {}),
            **({"hooks": dict(self.hooks)} if self.hooks else {}),
            **({"_record": self.record} if self.record not in ("", self.id) else {}),
            **({"ladder": self.ladder} if self.ladder else {}),
            **({"skills": list(self.skills)} if self.skills else {}),
        }

    def to_dict(self) -> dict[str, Any]:
        """The document, round-trippable: `flow:` says who fills each box (D542, D629)."""
        out = self._to_dict()
        if "calibrate" in self.flow and "budget" in out:
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
        cmds += [("gate.build", gate.build or ()), ("gate.test", gate.test or ())]
    cmds += [(f"stage {r.name}", r.command or ()) for r in stages]
    if generator.get("command"):
        cmds.append(("generator", generator["command"]))
    for what, cmd in cmds:
        for tok in cmd:
            if any(c.isspace() for c in tok):
                continue
            for m in _PLACEHOLDER.finditer(tok):
                if m.group(1) not in known:
                    raise TaskError(f"{what} says {{{m.group(1)}}}, which is neither a knob of `space:` "
                                    f"({', '.join(space) or 'none'}) nor the loop's ({', '.join(BUILTIN_SUBS)})")


def _knob_subs(knobs: dict[str, Any]) -> dict[str, str]:
    """A candidate's knobs as `{knob}` substitutions (D581): the scalar ones."""
    return {k: str(v) for k, v in (knobs or {}).items() if isinstance(v, (str, int, float, bool))}


#: D628: what `flux rtl test`, `flux rtl proto` and the templates' checkers print; a gate that
#: prints no such line is judged by its exit code
DEFAULT_COUNT_RE = r"(\d+) failing"


def _gate(doc: Any) -> Gate:
    if isinstance(doc, (str, list)):
        doc = {"test": doc}                             # `gate: <command>` is its test (D628)
    if not isinstance(doc, dict) or not (doc.get("build") or doc.get("test")):
        raise TaskError("`gate` needs a `build` and/or a `test` command (a string or a list of "
                        "argv tokens; `{artifact}`, `{workdir}`, `{name}`, `{part}`, `{python}`, "
                        "`{home}` are substituted; a `flux ...` head runs this flux)")
    build, test = _command(doc.get("build"), "gate.build"), _command(doc.get("test"), "gate.test")
    for key in ("count_re", "fail_re"):
        pat = doc.get(key)
        if pat is not None:
            try:
                re.compile(pat)
            except re.error as exc:
                raise TaskError(f"gate.{key} is not a regex: {exc}") from exc
    count_re = doc.get("count_re") or (None if doc.get("fail_re") else DEFAULT_COUNT_RE)
    return Gate(build=build, test=test, count_re=count_re, fail_re=doc.get("fail_re"),
                timeout_s=float(doc.get("timeout_s") or 120.0))


#: What a nested sub-task takes from its parent when it does not say (D455). `subtasks` is
#: deliberately absent: a child that inherited it would divide again, forever.
_INHERITED = ("contract", "language", "gate", "stages", "objectives", "knowledge", "skills",
              "params", "workload", "joiner", "budget", "brief", "space", "world", "hooks", "ladder",
              "cache", "flow")


def _space(raw: Any) -> tuple[dict[str, list], dict[str, dict[str, list]]]:
    """`space:` read and checked (D553): knob -> a non-empty list of scalar choices in the order
    written, or `{values: [...], when: {knob: [choices]}}` for a knob that only moves while
    those knobs hold one of those choices (elsewhere it stays at its first). A mapping without
    `values` is a component (D637): its knobs are `<component>.<knob>`, and `optional: true`
    adds `<component>.on` (off first), its knobs moving only while it is on."""
    if not raw:
        return {}, {}
    if not isinstance(raw, dict):
        raise TaskError("space: a mapping of knob -> [choices], in a meaningful order")
    flat: list[tuple[str, Any, dict[str, list]]] = []
    for k, vals in raw.items():
        k = str(k)
        if isinstance(vals, dict) and "values" not in vals:
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
            if set(vals) - {"values", "when"} or not isinstance(vals.get("when"), dict):
                raise TaskError(f"space.{k}: a list of choices, or {{values: [...], when: {{knob: [choices]}}}}")
            cond.update({str(c): list(v) if isinstance(v, (list, tuple)) else [v] for c, v in vals["when"].items()})
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
    return out, when


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
    a `world:` document's world, hooks, ladder, cache and flow (D555). A child of a named
    campaign is its own campaign, `<parent>/<child>`, in the same record."""
    out = dict(child)
    for key in _INHERITED:
        if key not in out and key in parent:
            out[key] = parent[key]
    if child.get("id") and (parent.get("_record") or parent.get("id")):
        out["_record"] = f"{parent.get('_record') or parent.get('id')}/{child['id']}"
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


def _point_name(point: dict[str, Any]) -> str:
    """A candidate's name from its point: the values joined, or a digest when that is long."""
    name = "-".join(str(v) for v in point.values())
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
    cmd = _command(cmd, f"stages[{i}].command")
    rtl_tools = _flux_rtl_tools(cmd) if cmd else []
    for tool in rtl_tools if "needs" not in doc else ():    # D628: `flux rtl measure` says what it runs
        needs.append(tool)
    metrics = tuple(doc.get("metrics") or (RTL_METRICS if "measure" in rtl_tools_kind(cmd) else ()))
    if not all(isinstance(m, str) for m in metrics):
        raise TaskError(f"stages[{i}].metrics is a list of metric names")
    metrics_re = dict(doc.get("metrics_re") or {})
    if cmd and not metrics_re and metrics:
        # `name=value` tokens (as `flux rtl measure` prints) need only the `metrics:` names
        # (D580); a token starts a line or follows whitespace, so `area_um2` never reads `xarea_um2`
        metrics_re = {m: rf"(?:^|(?<=\s)){re.escape(m)}=([-+0-9.eE]+)" for m in metrics}
    for m, pat in metrics_re.items():
        try:
            if re.compile(pat).groups < 1:
                raise TaskError(f"stages[{i}].metrics_re[{m!r}] needs one capturing group")
        except re.error as exc:
            raise TaskError(f"stages[{i}].metrics_re[{m!r}] is not a regex: {exc}") from exc
    if cmd and not metrics_re:
        raise TaskError(f"stages[{i}] ({doc['name']}): a command stage needs `metrics` (names the command "
                        "prints as `name=value` lines) or `metrics_re` (a regex per metric)")
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
    return Stage(name=doc["name"], command=cmd, metrics_re=metrics_re,
                evaluator=ev, metrics=metrics or tuple(metrics_re),
                timeout_s=float(doc.get("timeout_s") or 600.0), cutoff=cutoff, needs=tuple(needs))


#: Every top-level key a problem document may say; any other is refused with the nearest
#: real key (D590).
DOCUMENT_KEYS = frozenset({
    "id", "statement", "contract", "language", "parts", "max_parts",
    "flow", "subtasks", "max_subtasks", "seeds", "brief", "gate", "stages", "objectives",
    "knowledge", "joiner", "budget", "params", "space", "workload", "world", "hooks", "ladder", "cache",
    "skills"})

#: set by the loader, never written: a sub-document's record name, `<parent>/<child>` (D455)
_INTERNAL_KEYS = frozenset({"_record"})

#: the file extension a language's candidates are written with (D628); another language `x`
#: writes `.x`
EXTENSIONS = {"systemverilog": ".sv", "verilog": ".v", "vhdl": ".vhd", "python": ".py", "text": ".txt",
              "yaml": ".yaml", "json": ".json", "c": ".c", "cpp": ".cpp", "c++": ".cpp", "cuda": ".cu", "opencl": ".cl", "rust": ".rs",
              "markdown": ".md", "shell": ".sh", "bash": ".sh", "chisel": ".scala", "scala": ".scala"}


def load_task(path: str | Path) -> TaskSpec:
    """A task from a `.json` / `.yaml` / `.yml` file. Every way it can fail is a TaskError
    that names the file (D590): a missing file, a syntax error with its line, a key no
    document has, and whatever the document itself gets wrong."""
    p = Path(path)
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
        raise TaskError(f"{p}: a problem document is a mapping of keys (id, statement, gate, ...)")
    try:
        return TaskSpec.from_dict(doc, base=p.parent)
    except TaskError as exc:
        raise TaskError(f"{p}: {exc}") from exc


def request_for(task: TaskSpec, **overrides: Any) -> LoopRequest:
    """The loop's knobs for this task: the document's `budget`, then the caller's."""
    params = {"task": task.id, **task.params, **(overrides.pop("params", None) or {})}
    knobs = {**task.budget, **overrides}
    if isinstance(knobs.get("prototype"), str):
        knobs["prototype"] = True             # `prototype: systemc` names the language; the stage is on
    return LoopRequest(**knobs, params=params)


def _substitute(cmd: Iterable[str], subs: dict[str, str]) -> list[str]:
    return [_PLACEHOLDER.sub(lambda m: subs.get(m.group(1), m.group(0)), tok) for tok in cmd]


#: What the document says itself (D519); every other public `Problem` method is a hook a
#: world may fill. `prototype` and `tools_missing` are bound by hand (the problem's own
#: check may replace the world's; the document's commands and the world's tools add up).
DOCUMENT_OWNED = frozenset({"objective", "objectives", "subgoals", "ladder", "stages", "roles", "campaign_name",
                            "cache_suffix", "validate", "chained", "role_cutoff", "role_measure", "role_order",
                            "role_analytic", "role_evaluator_name", "prototype", "tools_missing"})


#: The world contract (D561): what a world's object may fill, by box of the drawing. `CORE`
#: is what a world usually fills; the rest are extensions. Everything else on `Problem` is the
#: loop's or the document's, and a world that defines it is refused at load.
CONTRACT: dict[str, tuple[str, ...]] = {
    "knowledge": ("prepare", "knowledge", "mentor_sections", "versions", "from_record", "open_records"),
    "orchestrate": ("review", "next_work", "plan_prompt", "standing"),
    "dse": ("search", "space", "instantiate", "seeds", "moves"),
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


#: The folders of the documents loaded in this process (D602). A module a document names that
#: is not installed is looked for beside the document, appended to the path on a miss so it
#: never shadows an installed one.
_HOMES: list[str] = []


def resolve(spec: str, what: str = "world") -> Any:
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
FLOW_BOXES = ("validate", "orchestrate", "plan", "dse", "generate", "test", "critique", "analytical",
              "simulation", "calibrate", "select", "feedback", "knowledge", "extract", "records")
_FLOW_WORDS = {"validate": ("rules", "llm"), "test": ("gate",), "critique": ("none", "llm"), "plan": ("none", "llm"),
               "calibrate": ("on", "off"), "select": ("objectives",), "feedback": ("human", "none"),
               "extract": ("none", "mined"), "records": ("on",)}
_KNOWLEDGE_SOURCES = ("sheet", "library", "digest")


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
        # the model writes the loop plan (parts, order, method, budgets) before a step is
        # spent (D577); `budget.agent` carries the half
        budget = dict(doc.get("budget") or {})
        halves = list(budget.get("agent") or [])
        if "plan" not in halves:
            halves.append("plan")
        budget["agent"] = halves
        doc["budget"] = budget
    if "orchestrate" in raw:
        roles["orchestrator"] = raw["orchestrate"]
        flow["orchestrate"] = raw["orchestrate"]
    if "dse" in raw:
        value = raw["dse"]
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
    for box in ("analytical", "simulation"):
        if box in raw:
            names = raw[box]
            if not isinstance(names, list):
                raise TaskError(f"flow.{box} is a list of stage names")
            if box == "analytical":
                # a learned screen is the analytical box's model half: `surrogate`, or
                # `{surrogate: {kind: ...}}` (D461)
                learned = [n for n in names if n == "surrogate" or (isinstance(n, dict) and "surrogate" in n)]
                if learned:
                    roles["evaluator"] = learned[0]
                names = [n for n in names if n not in learned]
            if not all(isinstance(n, str) for n in names):
                raise TaskError(f"flow.{box} is a list of stage names")
            flow[box] = list(names) + (["surrogate"] if box == "analytical" and "evaluator" in roles else [])
    if "knowledge" in raw:
        sources = raw["knowledge"]
        if isinstance(sources, str):
            sources = [sources]
        if not isinstance(sources, list) or not all(s in _KNOWLEDGE_SOURCES for s in sources):
            raise TaskError(f"flow.knowledge is a list from {', '.join(_KNOWLEDGE_SOURCES)}, not {sources!r}")
        flow["knowledge"] = list(sources)
    # the model-side knowledge: `digest` from the library, `mined` from the record (extract)
    wanted = [s for s in ("mined", "digest") if (s == "mined" and flow.get("extract") == "mined")
              or (s == "digest" and "digest" in (flow.get("knowledge") or ()))]
    if wanted:
        roles["knowledge"] = wanted[0] if len(wanted) == 1 else {"sources": {"names": wanted}}
    doc["roles"] = roles
    return flow, doc


def _surrogate_kind(roles: dict[str, Any]) -> str:
    who = roles.get("evaluator")
    if who == "surrogate":
        return "fitted"
    if isinstance(who, dict) and "surrogate" in who:
        return str((who.get("surrogate") or {}).get("kind", "fitted"))
    return ""


def _agent_tool(spec: Any) -> str:
    from .agent import agent_spec

    try:
        a = agent_spec(spec)
        return a.tool + ("" if a.questions == "decide" else f", questions answered by the {a.questions}")
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
    """The drawing, one line per box, with the half in force for this document: from `flow:`,
    the other keys, the world (`problem`, when given) and the defaults (D542)."""
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
    knowledge = list(flow.get("knowledge") or (["sheet"] if task.knowledge else []))
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
        f"feedback: {flow.get('feedback', 'human')}" + ("" if flow.get("feedback") == "none" else " (the operator's notes, when a terminal is attached)"),
        "knowledge: " + (", ".join(knowledge) if knowledge else "none declared (the world's mentor, if any)"),
        f"extract: {flow.get('extract', 'none')}" + (" (facts mined from the record reach the prompts)" if flow.get("extract") == "mined"
                                                    else " (mined: facts from the record reach the prompts)"),
        "records: on (every candidate, measurement and refusal, read back on resume)",
    ]
    return lines
