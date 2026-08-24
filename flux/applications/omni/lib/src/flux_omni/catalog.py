"""The Flux tool catalog, introspected from the MCP surface rather than hand-listed.

`flux_mcp.tool.FluxTool` is already the curated "one definition, three surfaces" tool
inventory (docs/agent-surface.md): every method is a stateless wrapper over one
`flux_chia_nodes` function, with a JSON-safe return and an agent-oriented Google-style
docstring. This module makes it the FOURTH surface -- an in-process catalog for the omni
loop -- by replaying `setup()`'s own registrations against a recorder instead of a real
MCP server. A hand-maintained list here would rot exactly the way D95/D96 measured, so
there isn't one: what `setup()` registers is what omni offers, automatically.

`object.__new__(FluxTool)` deliberately bypasses `ChiaTool.__init__`/`__post_init__`,
because those spin up a real Ray-actor-backed uvicorn server and the catalog needs the
method table, not a network endpoint. The methods themselves never touch instance state
(verified by reading them: each is a pure call-and-serialize wrapper), so binding them to
this hollow instance is sound.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class ParamSpec:
    name: str
    annotation: str
    required: bool
    default: Any
    doc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.annotation,
            "required": self.required,
            "default": None if self.default is inspect.Parameter.empty else self.default,
            "doc": self.doc,
        }


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    summary: str
    params: tuple[ParamSpec, ...]
    fn: Callable[..., Any] = field(compare=False, repr=False)

    def render(self) -> str:
        """One tool in full: name, one-line summary, then each parameter on its own line --
        the shape a model can copy arguments out of. This is what the `describe` meta-tool
        returns; the catalog itself shows only `signature()` (D377: the full render of all
        58 tools is ~21k tokens, which is not a prompt, it is a denial of service against
        the serving window)."""
        lines = [f"### {self.name}", self.summary]
        for p in self.params:
            req = "required" if p.required else f"optional, default {p.default!r}"
            doc = f" -- {p.doc}" if p.doc else ""
            lines.append(f"- {p.name} ({p.annotation}; {req}){doc}")
        return "\n".join(lines)

    def signature(self) -> str:
        """One line: required params spelled out, optionals folded into a count -- the
        `describe` meta-tool expands them on demand. Keeps the 58-tool menu near 3k
        tokens instead of 5k+ (optionals dominate: several tools take 15+)."""
        parts = [p.name for p in self.params if p.required]
        optional = sum(1 for p in self.params if not p.required)
        if optional:
            parts.append(f"+{optional} optional")
        summary = self.summary if len(self.summary) <= 140 else self.summary[:137] + "..."
        return f"{self.name}({', '.join(parts)}) -- {summary}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "params": [p.to_dict() for p in self.params],
        }


class _Recorder:
    """Stands in for the MCP server object during `setup()`: records what would have been
    registered, registers nothing anywhere."""

    def __init__(self) -> None:
        self.tools: list[tuple[Callable[..., Any], str]] = []

    def add_tool(self, fn: Callable[..., Any], name: str) -> None:
        self.tools.append((fn, name))


def _docstring_parts(fn: Callable[..., Any]) -> tuple[str, dict[str, str]]:
    """(summary, {param: first sentence of its Args: entry}) from a Google-style docstring."""
    doc = inspect.getdoc(fn) or ""
    summary = doc.split("\n\n", 1)[0].replace("\n", " ").strip()
    arg_docs: dict[str, str] = {}
    lines = doc.split("\n")
    in_args = False
    current: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped == "Args:":
            in_args = True
            continue
        if in_args:
            if stripped and not line.startswith(" "):
                break  # left the indented Args block
            if stripped.endswith(":") and " " not in stripped:
                break  # a following section like Returns:
            head, sep, rest = stripped.partition(": ")
            if sep and " " not in head and head.isidentifier():
                current = head
                arg_docs[current] = rest.strip()
            elif current and stripped:
                arg_docs[current] += " " + stripped
    # Keep only the first sentence of each: the catalog is a menu, not the manual.
    for k, v in arg_docs.items():
        arg_docs[k] = v.split(". ")[0].rstrip(".") + "." if v else ""
    return summary, arg_docs


def build_catalog(subset: list[str] | None = None) -> dict[str, ToolSpec]:
    """Every tool `FluxTool.setup()` registers, as `{bare_name: ToolSpec}` with callable
    `fn`s bound to a server-less instance. `subset` (bare names, without the `flux_`
    prefix or with it -- both accepted) narrows the surface; unknown names in it raise,
    because a silently-ignored filter is how a demo lies about what the model saw.
    """
    from flux_mcp.tool import FluxTool

    hollow = object.__new__(FluxTool)
    hollow.name = "flux"
    hollow.mcp = _Recorder()
    FluxTool.setup(hollow)

    catalog: dict[str, ToolSpec] = {}
    for fn, registered_name in hollow.mcp.tools:
        bare = registered_name.removeprefix("flux_")
        if bare == "omni_run":
            continue  # omni never offers itself: no recursive self-dispatch (D383)
        summary, arg_docs = _docstring_parts(fn)
        params = []
        for pname, p in inspect.signature(fn).parameters.items():
            if pname == "self":
                continue
            ann = p.annotation
            ann_str = "Any" if ann is inspect.Parameter.empty else (
                ann if isinstance(ann, str) else getattr(ann, "__name__", str(ann)))
            params.append(ParamSpec(
                name=pname,
                annotation=ann_str,
                required=p.default is inspect.Parameter.empty,
                default=p.default,
                doc=arg_docs.get(pname, ""),
            ))
        catalog[bare] = ToolSpec(name=bare, summary=summary, params=tuple(params), fn=fn)

    if subset is not None:
        wanted = {n.removeprefix("flux_") for n in subset}
        unknown = wanted - catalog.keys()
        if unknown:
            raise KeyError(
                f"unknown tool(s) in subset: {sorted(unknown)}; known: {sorted(catalog)}")
        catalog = {k: v for k, v in catalog.items() if k in wanted}
    return catalog


def render_catalog(catalog: dict[str, ToolSpec]) -> str:
    """The menu the model sees every round: one signature line per tool. Full parameter
    docs come from the `describe` meta-tool on demand, so the round prompt stays a few
    thousand tokens however large the surface grows."""
    return "\n".join(spec.signature() for _, spec in sorted(catalog.items()))
