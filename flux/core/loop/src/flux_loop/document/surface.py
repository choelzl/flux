"""The surface a document says (D775, D795-D797, D830) read into the forms the loop keeps."""

from __future__ import annotations

from typing import Any

from .flow import FLOW_BOXES
from .keys import TaskError, _LIFTED_KEYS


#: D791: what is read (files, sheet, text), who digests the library (agent), or off -- the
#: library is the loop's `library/` folder and the operator's, digested whenever it is on.
_KNOWLEDGE_KEYS = ("files", "sheet", "text", "off", "agent")



#: D795: every box says who works it the same way -- a word (`rules`, `model`, `off`, ...) or
#: `{by: <who>, ...its settings}`, `by` a word, an agent preset (opencode, claude, codex) or an
#: agent of one's own (`{command: [...]}`), the agent's options (session, timeout_s, ...) beside.


def _agents() -> tuple[str, ...]:
    """The agents a box may name: the presets, and those the server adds (D807)."""
    from ..agent import agent_kinds

    return tuple(agent_kinds())
#: what a document calls the boxes an agent may work (D830: never the loop's inside names, dse or extract)
_SURFACE_DELEGABLE = ("validate", "orchestrate", "plan", "generate", "critique", "select", "knowledge (digest, lessons)")
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
    if isinstance(k, dict) and "by" in k:
        raise TaskError("flow.knowledge: who digests the papers is `digest:` -- knowledge: {digest: claude} (D830)")
    if isinstance(k, dict) and isinstance(k.get("digest"), bool):
        raise TaskError("flow.knowledge.digest names who sums up the papers -- model (the default) or an agent; "
                        "the papers are always digested while the library is on (D791, D830)")
    if isinstance(k, dict) and "digest" in k:              # D830: who sums up library/'s papers, said by name
        k = dict(k)
        d = k.pop("digest")
        if isinstance(d, dict) and "by" in d:
            k.update(d)
        elif d not in ("model", None):
            k["by"] = d
        flow["knowledge"] = k = k or "model"
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
            raise TaskError(f"flow.{box} is not a box an agent answers; those are {', '.join(_SURFACE_DELEGABLE)}")
        spec = _who(box, by, opts)
        if box == "dse":
            out[box] = {**settings, **({"policy": {"agent": spec}} if settings else {"agent": spec})}
        else:
            out[box] = {**settings, "agent": spec}
    return out


def _dse_words() -> tuple[str, ...]:
    """The search policies a document names by word (D797: as its `orchestrate`)."""
    from ..roles import available_roles

    return tuple(n for n in available_roles("orchestrator") if n not in ("rules", "given", "llm", "agent", "model"))


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
