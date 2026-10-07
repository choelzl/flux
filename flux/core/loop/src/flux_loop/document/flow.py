"""The flow (D542): `flow:` read, checked and folded into roles, generator and critique."""

from __future__ import annotations

from typing import Any

from .keys import TaskError


# ------------------------------------------------------------------ the flow (D542)
#: The boxes of the drawing a document may say a half for, in flow order.
FLOW_BOXES = ("validate", "orchestrate", "plan", "dse", "generate", "test", "critique",
              "calibrate", "select", "feedback", "knowledge", "extract")
_FLOW_WORDS = {"validate": ("rules", "llm"), "test": ("gate",), "critique": ("none", "llm"), "plan": ("none", "llm"),
               "calibrate": ("on", "off"), "select": ("objectives",), "feedback": ("human", "none"),
               "extract": ("none", "mined")}
#: What `flow.knowledge` may name (D648): the library is on by default; `none` turns it off.
_KNOWLEDGE_SOURCES = ("sheet", "library", "none")


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
    from ..boxes import DELEGABLE, NEVER

    raw = dict(raw)
    search_hint = None
    if isinstance(raw.get("orchestrate"), dict) and "dse" in raw["orchestrate"]:
        from ..direction import PROMPTS

        value = dict(raw["orchestrate"])
        search_hint = value.pop("dse")
        if not isinstance(search_hint, str) or search_hint not in PROMPTS:
            raise TaskError(f"flow.orchestrate.dse is one of {', '.join(PROMPTS)}, not {search_hint!r}")
        raw["orchestrate"] = value

    for box, value in raw.items():
        if not (isinstance(value, dict) and "agent" in value) or box in ("generate", "knowledge"):   # their own (D773)
            continue
        if box in NEVER:
            raise TaskError(f"flow.{box} is never delegated to an agent: it establishes facts (D460)")
        if box not in DELEGABLE:
            raise TaskError(f"flow.{box} is not a box an agent answers; those are {', '.join(sorted(DELEGABLE))}")
        if set(value) != {"agent"}:
            raise TaskError(f"flow.{box} is {{agent: <preset or spec>}}, not {sorted(value)}")
        from ..agent import agent_spec

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
        if search_hint:
            spec = roles["orchestrator"]
            if isinstance(spec, str):
                spec = {spec: {"dse": search_hint}}
            elif "name" in spec:
                spec = {**spec, "dse": search_hint}
            else:
                key = next(iter(spec))
                spec = {key: {**spec[key], "dse": search_hint}}
            roles["orchestrator"] = spec
        flow["orchestrate"] = value
        if search_hint:
            flow["orchestrate"] = {**value, "dse": search_hint}
    if "dse" in raw:
        value = raw["dse"]
        if isinstance(value, dict) and set(value) == {"agent"}:
            value = {"llm": {"agent": value["agent"]}}      # a coding agent proposes the points (D640)
        if isinstance(value, list):                              # D583: phases, in order
            from ..dse import validate_phase

            for i, spec in enumerate(value):
                try:
                    validate_phase(spec)
                except ValueError as exc:
                    raise TaskError(f"flow.dse[{i}]: {exc}") from exc
            value = {"phases": {"phases": value}}
        elif isinstance(value, str) and ":" in value:          # D602: a policy of your own
            from ..dse import validate_phase

            try:
                validate_phase(value)
            except ValueError as exc:
                raise TaskError(f"flow.dse: {exc}") from exc
            value = {"phases": {"phases": [value]}}
        if value != "none":
            from ..roles import available_roles

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
                from ..agent import agent_spec

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
