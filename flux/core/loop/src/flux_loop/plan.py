"""The LOOP PLAN (D505): the shape of a pass as a document -- which parts in which order, the
budgets, which stages run, who fills each role, whether turns have tools -- written by hand or
by the agent, validated the same way, applied the same way, kept on the record.

Cedric: "agentic capabilities ... as an option for orchestrator/orchestration, tool calling,
planning and designing of the actual loop/sub loops ... both the option to hardcode/define
manually those or to have the agent make its picks". This module is the planning half. The
vocabulary a plan may use is the problem's own (`Problem.plan_surface`): its parts, its stages,
the roles the registry can fill, the loop's budgets -- so a plan can only name what exists, and
a plan the agent writes is checked by the same validator as one a person writes. A field set
to `"agent"` in a hand-written plan is the agent's to fill (a budget may be marked entry by
entry; inside `roles`, "agent" names the agent orchestrator component); every other field is
the person's. MIXED is the normal case.

What the plan cannot say: the gate. Admission stays the exhaustive test's.
"""

from __future__ import annotations

import dataclasses
import json
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .problem import Problem
    from .types import LoopState

__all__ = ["apply_plan", "check_plan", "plan_surface", "plan_with_agent"]

#: The loop's budgets a plan may set: name -> (type, low, high)
BUDGETS: dict[str, tuple[type, float, float]] = {
    "steps": (int, 1, 500),
    "repair_attempts": (int, 1, 200),
    "prototype_attempts": (int, 1, 200),
    "prototype_patience": (int, 0, 50),
    "prototype_attempts_max": (int, 1, 400),
    "explore_every": (int, 1, 50),
    "regress_after": (int, 1, 20),
    "max_tolerance": (int, 1, 20),
    "tool_hops": (int, 1, 30),
    "critique_rounds": (int, 0, 5),
    "budget_s": (float, 1, 10 ** 7),
}

AGENT = "agent"


def plan_surface(problem: "Problem", state: "LoopState") -> dict[str, Any]:
    """What a plan for this problem may say, with the choices and the defaults: the
    vocabulary the validator checks against and the agent is shown."""
    from .roles import available

    req = state.request
    parts = list(problem.subgoals())
    stages = list(problem.stages())
    surface: dict[str, Any] = {
        "parts": {"doc": "the parts to make, in this order (a subset is allowed; the rest are not made this pass)",
                  "choices": parts, "default": parts},
        "budget": {"doc": "the loop's budgets", "fields": {
            k: {"type": t.__name__, "min": lo, "max": hi, "default": getattr(req, k, None)}
            for k, (t, lo, hi) in BUDGETS.items()}},
        "stages": {"doc": "the costed stages to run, in rising order (a subset is allowed; the last quoted)",
                   "choices": stages, "default": stages},
        "roles": {"doc": "who fills each role; null = the problem's own hooks",
                  "fields": {"orchestrator": {"choices": [None, *available("orchestrator")],
                                              "default": getattr(problem.roles().orchestrator, "name", None)},
                             "knowledge": {"choices": [None, *available("knowledge")], "default": None},
                             "evaluator": {"choices": [None, *available("evaluator")], "default": None}}},
        "tools": {"doc": "whether model turns may call tools (compute, check, history, knowledge)",
                  "choices": [True, False], "default": bool(req.tools)},
        # D577 (Cedric: "plan first, then run"): the approach per part, decided before the pass
        # runs -- the generator's brief for that part, so its first draft follows the plan
        "methods": {"doc": "per part, the method to try first and why (one or two lines each): the generator's brief for that part",
                    "fields": parts, "default": {}},
    }
    extra = getattr(problem, "plan_extra", None)
    if callable(extra):
        try:
            surface.update(extra(state) or {})
        except Exception:  # noqa: BLE001
            pass
    return surface


def check_plan(doc: Any, surface: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """(the plan, cleaned; the errors). A field the document does not set is left out (the
    default stands); a field set to "agent" is left as "agent" for the agent to fill; a field
    that names what does not exist is an error naming it, so a person or the agent can fix
    that one field."""
    errors: list[str] = []
    clean: dict[str, Any] = {}
    if not isinstance(doc, dict):
        return {}, ["a plan is a JSON object"]
    known = set(surface) | {"why", "plan"}
    for key in doc:
        if key not in known:
            errors.append(f"`{key}` is not a field a plan may set (fields: {', '.join(sorted(surface))})")
    # parts
    parts = doc.get("parts")
    if parts == AGENT:
        clean["parts"] = AGENT
    elif parts is not None:
        choices = surface["parts"]["choices"]
        if not isinstance(parts, list) or not all(isinstance(p, str) for p in parts):
            errors.append("`parts` is a list of part names (or \"agent\")")
        else:
            bad = [p for p in parts if p not in choices]
            if bad:
                errors.append(f"`parts` names {bad}, which this problem does not have (parts: {choices})")
            elif len(set(parts)) != len(parts):
                errors.append("`parts` repeats a name")
            elif not parts:
                errors.append("`parts` is empty: nothing would be made")
            else:
                clean["parts"] = list(parts)
    # budget
    budget = doc.get("budget")
    if budget == AGENT:
        clean["budget"] = AGENT
    elif budget is not None:
        if not isinstance(budget, dict):
            errors.append("`budget` is an object of the loop's budgets (or \"agent\")")
        else:
            got: dict[str, Any] = {}
            for k, v in budget.items():
                if k not in BUDGETS:
                    errors.append(f"`budget.{k}` is not a budget (budgets: {', '.join(BUDGETS)})")
                    continue
                if v == AGENT:
                    got[k] = AGENT
                    continue
                t, lo, hi = BUDGETS[k]
                if v is None and k == "budget_s":
                    got[k] = None
                    continue
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    errors.append(f"`budget.{k}` must be a number, not {v!r}")
                    continue
                if not (lo <= v <= hi):
                    errors.append(f"`budget.{k}` = {v} is outside [{lo:g}, {hi:g}]")
                    continue
                got[k] = t(v)
            clean["budget"] = got
    # stages
    stages = doc.get("stages")
    if stages == AGENT:
        clean["stages"] = AGENT
    elif stages is not None:
        choices = surface["stages"]["choices"]
        if not isinstance(stages, list) or not all(isinstance(x, str) for x in stages):
            errors.append("`stages` is a list of stage names (or \"agent\")")
        else:
            bad = [x for x in stages if x not in choices]
            if bad:
                errors.append(f"`stages` names {bad}, which this problem does not have (stages: {choices})")
            elif not stages:
                errors.append("`stages` is empty: nothing would be measured")
            elif [x for x in choices if x in stages] != list(stages):
                errors.append(f"`stages` must keep the problem's rising order {choices}")
            else:
                clean["stages"] = list(stages)
    # roles
    roles = doc.get("roles")
    if roles == AGENT:
        clean["roles"] = AGENT
    elif roles is not None:
        if not isinstance(roles, dict):
            errors.append("`roles` is an object: {\"orchestrator\": \"rules\", ...} (or \"agent\")")
        else:
            got = {}
            fields = surface["roles"]["fields"]
            for role, spec in roles.items():
                if role not in fields:
                    errors.append(f"`roles.{role}` is not a role a plan may fill (roles: {', '.join(fields)})")
                    continue
                # inside `roles`, "agent" is the component of that name (the agent orchestrator),
                # never the fill marker: the marker for the roles field is `"roles": "agent"`
                name = spec if isinstance(spec, (str, type(None))) else (
                    spec.get("name") if isinstance(spec, dict) and "name" in spec
                    else next(iter(spec)) if isinstance(spec, dict) and len(spec) == 1 else None)
                if name not in fields[role]["choices"]:
                    errors.append(f"`roles.{role}` = {spec!r} is not one of {fields[role]['choices']}")
                    continue
                got[role] = spec
            clean["roles"] = got
    # methods (D577)
    methods = doc.get("methods")
    if methods == AGENT:
        clean["methods"] = AGENT
    elif methods is not None:
        choices = surface["parts"]["choices"]
        if not isinstance(methods, dict) or not all(isinstance(v, str) for v in methods.values()):
            errors.append("`methods` is an object {part: \"the method to try first and why\"} (or \"agent\")")
        else:
            bad = [p for p in methods if p not in choices]
            if bad:
                errors.append(f"`methods` names {bad}, which this problem does not have (parts: {choices})")
            else:
                clean["methods"] = {p: v.strip()[:600] for p, v in methods.items() if v.strip()}
    # tools
    if "tools" in doc:
        if doc["tools"] == AGENT:
            clean["tools"] = AGENT
        elif isinstance(doc["tools"], bool):
            clean["tools"] = doc["tools"]
        else:
            errors.append("`tools` is true or false (or \"agent\")")
    for key in surface:
        if key in ("parts", "budget", "stages", "roles", "tools", "methods") or key not in doc:
            continue
        clean[key] = doc[key]                      # a problem's own field: its `plan_check` judges it
    if isinstance(doc.get("why"), str):
        clean["why"] = doc["why"][:600]
    return clean, errors


def _open_fields(clean: dict[str, Any], surface: dict[str, Any], agent: bool, from_file: bool) -> list[str]:
    """Which fields the agent is to fill: those set to "agent"; and, when the agent plans
    WITHOUT a hand-written file, every field the plan does not set. A file is the person's
    word: what it leaves out keeps the default, what it marks "agent" is the agent's."""
    out = []
    for key in ("parts", "budget", "stages", "roles", "tools", "methods"):
        v = clean.get(key)
        if v == AGENT or (agent and not from_file and key not in clean):
            out.append(key)
        elif key == "budget" and isinstance(v, dict) and any(x == AGENT for x in v.values()):
            out.append(key)
    return out


def _read_file(path: str) -> tuple[dict[str, Any], str | None]:
    if not path or not os.path.exists(path):
        return {}, None
    try:
        with open(path) as f:
            text = f.read()
        if path.endswith((".yaml", ".yml")):
            import yaml  # type: ignore[import-untyped]

            return yaml.safe_load(text) or {}, None
        return json.loads(text), None
    except Exception as exc:  # noqa: BLE001
        return {}, f"{path}: could not be read as a plan ({exc!s:.120})"


def _last_on_record(state: "LoopState") -> dict[str, Any] | None:
    rec = getattr(state, "records", None)
    if rec is None or getattr(rec, "store", None) is None:
        return None
    try:
        plans = [e for e in rec.store.events(rec.campaign_id) if e.get("kind") == "plan"]
    except Exception:  # noqa: BLE001
        return None
    return dict((plans[-1].get("detail") or {})) if plans else None


def apply_plan(problem: "Problem", state: "LoopState") -> dict[str, Any]:
    """THE PLAN THIS PASS FOLLOWS: the document (a file, else the last on record), the agent's
    fill of what is open when the agent plans, validated, applied to the request, the roles,
    the division and the stages, recorded, and written back to the file when there is one."""
    from .observe import _phase

    req = state.request
    agent = "plan" in (req.agent or ())
    surface = plan_surface(problem, state)
    with _phase("plan: the loop", why="the agent plans" if agent else "a plan document") as out:
        doc, err = _read_file(req.plan_file or "")
        from_file = bool(doc)
        source = f"file {req.plan_file}" if doc else ""
        if err:
            state.say(f"  plan: {err}; ignored")
            out["error"] = err
        if not doc:
            prior = _last_on_record(state)
            if prior:
                doc = {k: v for k, v in prior.items() if k != "plan"}
                source = "the record's last plan"
        clean, errors = check_plan(doc, surface)
        for e in errors:
            state.say(f"  plan: {e}")
        if errors:
            out["errors"] = "\n".join(errors)
        opened = _open_fields(clean, surface, agent, from_file)
        if opened and agent and state.proposer is not None:
            filled = plan_with_agent(problem, state, clean, surface, opened, out)
            if filled is not None:
                clean = filled
                source = (source + " + " if source else "") + "the agent"
        # anything still "agent" with no agent to fill it falls back to the default
        for key in list(clean):
            v = clean[key]
            if v == AGENT:
                clean.pop(key)
            elif key == "budget" and isinstance(v, dict):
                clean[key] = {k: x for k, x in v.items() if x != AGENT}
        _apply(problem, state, clean)
        clean["plan"] = source or "the defaults"
        out["plan"] = json.dumps(clean, indent=1)
        out["source"] = clean["plan"]
        state.say(f"  plan ({clean['plan']}): " + _describe(clean))
        rec = getattr(state, "records", None)
        if rec is not None and getattr(rec, "store", None) is not None and clean.keys() - {"plan"}:
            try:
                rec.store.append_event(rec.campaign_id, "plan", clean)
            except Exception:  # noqa: BLE001
                pass
        if req.plan_file and agent and "the agent" in (source or ""):
            try:
                with open(req.plan_file, "w") as f:
                    json.dump(clean, f, indent=1)
                state.say(f"  plan: written to {req.plan_file}; edit it to take a field back by hand")
            except Exception as exc:  # noqa: BLE001
                state.say(f"  plan: could not be written to {req.plan_file} ({exc!s:.80})")
    return clean


def _describe(clean: dict[str, Any]) -> str:
    bits = []
    if clean.get("parts"):
        bits.append("parts " + ", ".join(clean["parts"]))
    if clean.get("budget"):
        bits.append("budget " + ", ".join(f"{k}={v}" for k, v in clean["budget"].items()))
    if clean.get("stages"):
        bits.append("stages " + " > ".join(clean["stages"]))
    if clean.get("roles"):
        bits.append("roles " + ", ".join(f"{k}={v}" for k, v in clean["roles"].items()))
    if "tools" in clean:
        bits.append(f"tools {'on' if clean['tools'] else 'off'}")
    if clean.get("methods"):
        bits.append("methods " + "; ".join(f"{p}: {m[:80]}" for p, m in clean["methods"].items()))
    if clean.get("why"):
        bits.append(f"why: {clean['why'][:160]}")
    return "; ".join(bits) or "nothing set: the defaults"


def _apply(problem: "Problem", state: "LoopState", clean: dict[str, Any]) -> None:
    from .roles import make

    changes: dict[str, Any] = {}
    for k, v in (clean.get("budget") or {}).items():
        changes[k] = v
    if "tools" in clean:
        changes["tools"] = bool(clean["tools"])
    if changes:
        state.request = dataclasses.replace(state.request, **changes)
    roles = problem.roles()
    for role, spec in (clean.get("roles") or {}).items():
        try:
            roles = roles.with_role(role, make(role, spec))
        except Exception as exc:  # noqa: BLE001
            state.say(f"  plan: roles.{role} = {spec!r} could not be built ({exc!s:.80}); the problem's own stands")
    problem._roles = roles
    for part, method in (clean.get("methods") or {}).items():          # D577: the plan's method is the part's brief
        if isinstance(method, str) and method.strip():
            state.plans[part] = {**(state.plans.get(part) or {}), "brief": method.strip()}
    state.plan = {k: v for k, v in clean.items() if k not in ("why", "plan")}
    problem._plan = state.plan                      # `chained` reads the stages from here


def plan_with_agent(problem: "Problem", state: "LoopState", clean: dict[str, Any],
                    surface: dict[str, Any], opened: list[str], out: dict) -> dict[str, Any] | None:
    """The agent fills the open fields (D505): shown the objective, the vocabulary with its
    defaults, what is fixed by hand, the previous plan and what came of it (through the
    tools: standings, history, decisions), it answers a plan for the OPEN fields only; the
    validator's errors go back to it, three times at most; a field it cannot get right
    keeps its default. Its reason is kept with the plan."""
    from .model import _ask, _json
    from .tools import orchestrator_tools

    fixed = {k: v for k, v in clean.items() if k not in opened and k != "why"}
    objectives: dict[str, Any] = {}
    try:
        objectives = problem.standing(state) or {}
    except Exception:  # noqa: BLE001
        pass
    prior = _last_on_record(state)
    vocab = {k: surface[k] for k in opened if k in surface}
    try:
        library = list(problem.library_index(state) or [])           # D576/D577
    except Exception:  # noqa: BLE001
        library = []
    lines = [
        "You are PLANNING a design campaign's next pass: how the loop is shaped before it runs.",
        f"OBJECTIVE: {objectives.get('goal', problem.name)}",
        f"NOW: {objectives.get('now', '')}" if objectives.get("now") else "",
        (f"THE WHOLE: {objectives.get('composed')}" if objectives.get("composed") else ""),
        "You have tools: standings() -- every part's numbers; history(part) -- what was tried for a part; "
        "decisions() -- the picks already taken and why; knowledge(query) -- the method sheet and papers.",
        "FIXED BY HAND (not yours to change): " + (json.dumps(fixed) if fixed else "nothing"),
        ("THE LIBRARY, digested (name a method from it in `methods` when it fits):\n" + "\n".join(library[:40])) if library else "",
        (f"THE PREVIOUS PLAN on record: {json.dumps({k: v for k, v in prior.items() if k != 'plan'})}" if prior else ""),
        f"YOU FILL these fields: {', '.join(opened)}. Their vocabulary, choices and defaults:\n{json.dumps(vocab, indent=1, default=str)}",
        "Rules: name only what the vocabulary lists; a part not listed is not made; keep the stages' order; "
        "budgets within their ranges; leave a field out to keep its default. Spend the budget where the "
        "numbers say the gap is. In `methods`, say for each part the approach to try FIRST and why -- from "
        "the library, the record, or your own knowledge -- in one or two lines; the generator reads it as its brief.",
        'Reply with ONLY JSON: {' + ", ".join(f'"{k}": ...' for k in opened) + ', "why": "<two lines: what the numbers say and what this plan does about it>"}',
    ]
    prompt = "\n".join(l for l in lines if l)
    tools = orchestrator_tools(problem, state)
    errors: list[str] = []
    for round_ in range(3):
        ask = prompt if not errors else prompt + "\n\nYOUR LAST PLAN WAS REFUSED:\n- " + "\n- ".join(errors) + "\nSend it again, fixed."
        try:
            reply = _ask(state, ask, None, tools=tools).text
        except Exception as exc:  # noqa: BLE001
            out["agent"] = f"did not answer ({exc!s:.100}); the defaults stand"
            state.say(f"  plan: the agent did not answer ({exc!s:.80}); the defaults stand")
            return None
        doc = _json(reply)
        if not isinstance(doc, dict):
            errors = ["the reply was not a JSON object"]
            continue
        proposed = {k: v for k, v in doc.items() if k in opened or k == "why"}
        merged = {**{k: v for k, v in clean.items() if k not in opened}, **proposed}
        got, errors = check_plan(merged, surface)
        # a fill for a field opened as a whole may not leave it "agent"
        errors += [f"`{k}` is yours to fill; \"agent\" is not an answer" for k, v in got.items() if v == AGENT]
        extra_check = getattr(problem, "plan_check", None)
        if callable(extra_check) and not errors:
            try:
                errors += list(extra_check(got, state) or [])
            except Exception as exc:  # noqa: BLE001
                errors += [f"the problem refused the plan: {exc!s:.120}"]
        if not errors:
            out["agent"] = f"planned in {round_ + 1} round(s): {str(got.get('why') or '')[:300]}"
            return got
        out[f"round {round_ + 1} refused"] = "\n".join(errors)
    state.say("  plan: the agent's plan was refused three times (" + "; ".join(errors)[:200] + "); the defaults stand for the open fields")
    out["agent"] = "refused three times; the defaults stand"
    return None
