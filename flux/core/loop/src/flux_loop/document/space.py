"""The design space (D553): knobs, components, seeds and the points a search walks."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .keys import TaskError


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
