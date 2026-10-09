"""The drawing in words (D542, D666): one line per box with the half in force."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..objective import Objectives
from .library import library_folders, library_on

if TYPE_CHECKING:  # pragma: no cover
    from .spec import TaskSpec
    from .stages import Stage


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
    how = "its command" if stage.command else "nothing"
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
