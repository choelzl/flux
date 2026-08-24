"""A world's settings from the document's `params:` (D557, review 2 R13). Three worlds had
written this: read a dataclass's fields, take the keys the document says, coerce a list to a
tuple, keep a default where the document says null. One copy: unknown keys are refused at load
(a copied document with a typo would otherwise run the default ask and report it as the one
written, the bankmap's rule), values are coerced to the field's declared type, and a null
keeps the default unless the field is `optional` (a null that MEANS "none").
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterable

__all__ = ["ParamsError", "from_params"]


class ParamsError(ValueError):
    """A `params:` key the world cannot mean, or a value of the wrong shape."""


def from_params(cls: type, params: dict[str, Any] | None, *, optional: Iterable[str] = (),
                what: str = "") -> Any:
    """`cls(**params)`, typed: a key that is not a field of `cls` is refused with the known
    list; a list where the field is a tuple becomes one; an int, float, bool or str field
    is coerced; a null keeps the field's default unless the field is in `optional`."""
    fields = {f.name: f for f in dataclasses.fields(cls)}
    p = dict(params or {})
    unknown = sorted(set(p) - set(fields))
    if unknown:
        raise ParamsError(f"params {unknown} are not {what or cls.__name__}'s; known: "
                          + ", ".join(sorted(fields)))
    optional = set(optional)
    kw: dict[str, Any] = {}
    for k, v in p.items():
        f = fields[k]
        if v is None:
            if k in optional:
                kw[k] = None
            continue                              # a null where a value belongs keeps the default
        kw[k] = _coerce(f, v)
    return cls(**kw)


def _coerce(f: dataclasses.Field, v: Any) -> Any:
    kind = str(f.type)
    try:
        if "tuple" in kind and isinstance(v, (list, tuple)):
            return tuple(v)
        if kind.startswith("bool") and not isinstance(v, bool):
            return str(v).lower() in ("1", "true", "yes", "on")
        if kind.startswith("int") and not isinstance(v, bool):
            return int(v)
        if kind.startswith("float"):
            return float(v)
        if kind.startswith("str"):
            return str(v)
    except (TypeError, ValueError) as exc:
        raise ParamsError(f"params.{f.name}: {v!r} is not {kind} ({exc})") from exc
    return v
