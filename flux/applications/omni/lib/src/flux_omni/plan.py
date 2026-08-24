"""Typed plans: what the model proposes, what the executor refuses or runs.

A plan is a JSON list of steps. Each step names one catalog tool, gives its arguments,
and may bind its result to a name later steps reference as `"$name"` (the whole result)
or `"$name.path.to[0].field"` (a fragment). Validation happens BEFORE execution, against
the introspected catalog signature, and a bad step produces a `Refusal` naming exactly
what was wrong -- the model repairs against that text next round, the same
fail-loudly-with-the-reason posture as `InvalidLLMProposal` (docs/decisions.md D57).

Three meta-tools exist beyond the catalog, because "generate input files as needed" is
part of the omni brief: `write_file` (text, sandboxed under the run's workdir -- an
absolute path or a `..` escape is refused, not normalized), `load_ir` (reads one Flux IR
YAML/JSON document from the run workdir or the repo's bundled examples -- read-only, same
path rules), and `note` (records free text into the run log, executes nothing). They are
validated here, not in `catalog.py`, so the catalog stays exactly the MCP surface.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flux_llm import strip_markdown_fence

from .catalog import ToolSpec

META_TOOLS = ("write_file", "load_ir", "describe", "note")
_REF_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)((?:\.[A-Za-z0-9_]+|\[\d+\])*)$")


@dataclass(frozen=True, slots=True)
class Step:
    tool: str
    args: dict[str, Any]
    bind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "args": self.args, "bind": self.bind}


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why a proposed step will not run. `step_index` is the position in the proposal."""

    step_index: int
    tool: str
    reason: str

    def render(self) -> str:
        return f"step {self.step_index} ({self.tool}): {self.reason}"


@dataclass(frozen=True, slots=True)
class Proposal:
    """One parsed model reply: steps to run, or a conclusion, or both empty on refusal."""

    steps: tuple[Step, ...] = ()
    done: bool = False
    conclusion: str = ""
    parse_error: str | None = None


def parse_proposal(raw_text: str) -> Proposal:
    """Model reply -> Proposal. Never raises: a malformed reply becomes `parse_error`,
    which the loop feeds back verbatim so the model can repair it."""
    text = strip_markdown_fence(raw_text).strip()
    # Models often wrap the object in prose; take the outermost {...} if the whole
    # string is not itself JSON.
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return Proposal(parse_error=f"no JSON object found in reply: {raw_text[:200]!r}")
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            return Proposal(parse_error=f"invalid JSON ({exc}): {raw_text[:200]!r}")
    if not isinstance(parsed, dict):
        return Proposal(parse_error=f"expected a JSON object, got {type(parsed).__name__}")

    raw_steps = parsed.get("steps", [])
    if not isinstance(raw_steps, list):
        return Proposal(parse_error=f"'steps' must be a list, got {type(raw_steps).__name__}")
    steps = []
    for i, s in enumerate(raw_steps):
        if not isinstance(s, dict) or not isinstance(s.get("tool"), str):
            return Proposal(parse_error=f"step {i} is not an object with a 'tool' string: {s!r}")
        args = s.get("args", {})
        if not isinstance(args, dict):
            return Proposal(parse_error=f"step {i}: 'args' must be an object, got {args!r}")
        bind = s.get("bind")
        if bind is not None and not isinstance(bind, str):
            return Proposal(parse_error=f"step {i}: 'bind' must be a string, got {bind!r}")
        steps.append(Step(tool=s["tool"], args=args, bind=bind))
    return Proposal(
        steps=tuple(steps),
        done=bool(parsed.get("done", False)),
        conclusion=str(parsed.get("conclusion", "") or ""),
    )


def load_plan_file(path: str | Path) -> tuple[Step, ...]:
    """A saved/canned plan: JSON list of step objects (the `Step.to_dict()` shape)."""
    parsed = json.loads(Path(path).read_text())
    if isinstance(parsed, dict):  # accept a full provenance file too
        parsed = parsed.get("executed_plan", parsed.get("steps"))
    if not isinstance(parsed, list):
        raise ValueError(f"{path}: expected a JSON list of steps")
    return tuple(Step(tool=s["tool"], args=s.get("args", {}), bind=s.get("bind"))
                 for s in parsed)


def resolve_refs(value: Any, bindings: dict[str, Any]) -> Any:
    """Replace `"$name.path"` strings (anywhere in a nested arg structure) with the bound
    fragment. Unknown names or paths raise KeyError/IndexError with the reference text --
    validation converts that to a Refusal."""
    if isinstance(value, str):
        m = _REF_RE.match(value)
        if m is None:
            return value
        name, path = m.group(1), m.group(2)
        if name not in bindings:
            raise KeyError(f"reference {value!r}: nothing bound to {name!r}")
        node = bindings[name]
        for part in re.findall(r"\.([A-Za-z0-9_]+)|\[(\d+)\]", path):
            key, idx = part
            try:
                node = node[int(idx)] if idx else node[key]
            except (KeyError, IndexError, TypeError) as exc:
                raise KeyError(f"reference {value!r}: {exc}") from exc
        return node
    if isinstance(value, dict):
        return {k: resolve_refs(v, bindings) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_refs(v, bindings) for v in value]
    return value


def validate_step(
    index: int,
    step: Step,
    catalog: dict[str, ToolSpec],
    bound_names: set[str],
    workdir: Path,
) -> Refusal | None:
    """Everything checkable without running the tool. `bound_names` are binds established
    by earlier steps (this proposal's own earlier steps included), so forward references
    are refused."""
    if step.tool in META_TOOLS:
        if step.tool == "write_file":
            path = step.args.get("path")
            if not isinstance(path, str) or not isinstance(step.args.get("text"), str):
                return Refusal(index, step.tool, "write_file needs string 'path' and 'text'")
            candidate = Path(path)
            if candidate.is_absolute() or ".." in candidate.parts:
                return Refusal(
                    index, step.tool,
                    f"path {path!r} escapes the run workdir; use a relative path")
        if step.tool == "describe":
            target = step.args.get("tool")
            if not isinstance(target, str):
                return Refusal(index, step.tool, "describe needs a string 'tool'")
            if target not in catalog and target not in META_TOOLS:
                return Refusal(index, step.tool,
                               f"no tool named {target!r} to describe; "
                               f"available: {', '.join(sorted(catalog))}")
        if step.tool == "load_ir":
            path = step.args.get("path")
            if not isinstance(path, str):
                return Refusal(index, step.tool, "load_ir needs a string 'path'")
            candidate = Path(path)
            if candidate.is_absolute() or ".." in candidate.parts:
                return Refusal(
                    index, step.tool,
                    f"path {path!r} must be relative (resolved against the run workdir, "
                    "then the Flux repo root for bundled examples)")
        return _check_refs(index, step, bound_names)
    spec = catalog.get(step.tool)
    if spec is None:
        return Refusal(
            index, step.tool,
            f"unknown tool; available: {', '.join(sorted(catalog))}, "
            f"plus meta-tools {', '.join(META_TOOLS)}")
    known = {p.name for p in spec.params}
    unknown = set(step.args) - known
    if unknown:
        return Refusal(index, step.tool, f"unknown argument(s) {sorted(unknown)}; "
                                         f"this tool takes {sorted(known)}")
    missing = {p.name for p in spec.params if p.required} - set(step.args)
    if missing:
        return Refusal(index, step.tool, f"missing required argument(s) {sorted(missing)}")
    return _check_refs(index, step, bound_names)


def _check_refs(index: int, step: Step, bound_names: set[str]) -> Refusal | None:
    if step.bind is not None and not step.bind.isidentifier():
        return Refusal(index, step.tool,
                       f"bind {step.bind!r} must be a bare identifier (no '$' -- the '$' "
                       "prefix belongs to references, e.g. \"$name.field\")")
    def refs_in(value: Any) -> list[str]:
        if isinstance(value, str) and value.startswith("$"):
            return [value]
        if isinstance(value, dict):
            return [r for v in value.values() for r in refs_in(v)]
        if isinstance(value, list):
            return [r for v in value for r in refs_in(v)]
        return []

    for ref in refs_in(step.args):
        m = _REF_RE.match(ref)
        if m is None:
            return Refusal(index, step.tool,
                           f"malformed reference {ref!r} (want $name.field or $name[0])")
        if m.group(1) not in bound_names:
            return Refusal(index, step.tool,
                           f"reference {ref!r} names no earlier bind "
                           f"(bound so far: {sorted(bound_names) or 'none'})")
    return None
