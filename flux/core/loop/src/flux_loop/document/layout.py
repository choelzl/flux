"""The loop's forms written back the way a document says them (D775, D795): what `to_dict` writes."""

from __future__ import annotations

from typing import Any

from .keys import _LIFTED_KEYS
from .surface import _AGENT_OPTS, _BY_WORDS


def _by_doc(spec: Any, more: dict[str, Any]) -> Any:
    """An agent spec written as `by` (D795): the preset's name alone when that is all."""
    if isinstance(spec, dict) and spec.get("preset"):
        who = {"by": spec["preset"], **{k: x for k, x in spec.items() if k != "preset"}}
    else:
        who = {"by": spec}
    return who["by"] if len(who) == 1 and not more and isinstance(who["by"], str) else {**who, **more}


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
    k = out.get("knowledge")                               # D830: who digests is `digest:`
    if isinstance(k, dict) and "by" in k:
        agent_opts = {x: k[x] for x in _AGENT_OPTS if x in k}
        read = {x: v for x, v in k.items() if x != "by" and x not in agent_opts}
        out["knowledge"] = {**read, "digest": {"by": k["by"], **agent_opts} if agent_opts else k["by"]}
    elif isinstance(k, str) and k not in ("off", "model"):
        out["knowledge"] = {"digest": k}
    lessons = out.pop("extract", "off")
    if lessons != "off":
        k = out.get("knowledge")
        if k is None or k == "model":
            k = {}
        elif k == "off":
            k = {"off": True}
        elif isinstance(k, str):
            k = {"digest": k}
        out["knowledge"] = {**k, "lessons": lessons}
    return out


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
