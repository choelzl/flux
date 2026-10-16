"""Paths hidden from a loop's AI agents (D1159): `flow.knowledge.hidden` -- tests, checks, a gate's files and
their parameters, which an agent could read to game the gate. Not a secret: the loop's own tools (its gate, its
stages, its scripts) read them as ever, and what the gate says of a design still reaches the agent.

Each agent turn is told so in its brief, and each agent's own permission settings refuse the paths:

  Claude Code  `--settings` with `permissions.deny`: Read(...) and Edit(...) on each path (absolute, `//...`, and
               relative to the turn's folder when it is inside it), Bash(...) on a command naming it, and the Grep
               and Glob tools (where a build has them; its Read rules hold for them only best-effort)
  OpenCode     its config (`OPENCODE_CONFIG_CONTENT`): `permission.read` and `permission.edit` patterns for the
               path relative to its project, `permission.bash` patterns for a command naming it, and its `grep` and
               `glob` tools denied: they honour no `read` rule (a grep over the folder printed a hidden file's lines)
  Codex        no per-path rule a user's config can say (its `deny_read` is an administrator's requirement): the
               brief's line only

A hidden path here is (absolute, as its loop said it): the absolute path or glob in the loop's folder, and the
same relative to that folder, which a command names it by.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from flux_knowledge.hidden import hidden as hidden_now, hide, is_hidden  # noqa: F401 -- re-exported

Hidden = Sequence[tuple[str, str]]

__all__ = ["claude_settings", "hidden_line", "hidden_now", "hidden_subs", "is_hidden", "opencode_permission",
           "turn_hidden", "with_hidden"]

_GLOB = re.compile(r"[*?\[]")


def hidden_subs(task: Any) -> dict[str, str]:
    """What an agent turn's substitutions carry of the task's hidden paths: {"hidden": json} or {}."""
    got = list(getattr(task, "hidden", ()) or ())
    return {"hidden": json.dumps([list(h) for h in got])} if got else {}


def turn_hidden(subs: dict[str, str], own: Iterable[str] = (), workdir: str | Path = "") -> list[tuple[str, str]]:
    """The turn's hidden paths: the task's (`subs["hidden"]`), the agent's own (`own`, relative to the loop's
    folder `subs["home"]`), and each one under the workbench also as the turn sees it, through its link."""
    try:
        got: list[tuple[str, str]] = [(str(h[0]), str(h[1])) for h in json.loads(subs.get("hidden") or "[]") if isinstance(h, list) and len(h) == 2]
    except ValueError:
        got = []
    home = subs.get("home") or "."
    for rel in own:
        got.append((str((Path(home) / rel).resolve()) if not _GLOB.search(rel) else str(Path(home).resolve() / rel), rel))
    bench = subs.get("workbench") or ""
    if bench and workdir:
        root = str(Path(bench).resolve())
        for a, rel in list(got):
            if a == root or a.startswith(root + "/"):
                got.append((str(Path(workdir) / "workbench") + a[len(root):], rel))
    return list(dict.fromkeys(got))


def hidden_line(hidden: Hidden) -> str:
    """The brief's line: the paths off limits, as the loop names them."""
    if not hidden:
        return ""
    names = list(dict.fromkeys(f"`{rel}`" for _a, rel in hidden))
    return ("OFF LIMITS: " + ", ".join(names) + " (in the loop's folder) are hidden from you. Do not read, list, search "
            "or copy them, nor run a command on them: the loop's own checks use them, and what they find reaches you "
            "in the loop's verdicts.")


def _rel_to(path: str, workdir: str | Path) -> str:
    try:
        rel = os.path.relpath(path, str(Path(workdir).resolve()))
    except ValueError:
        return ""
    return "" if rel.startswith("..") else rel


def _commands(rel: str) -> list[str]:
    """Patterns of a command naming `rel`: at its start, after a space, or after a folder (`../x`, `/abs/x`), the
    path itself, a file in it, or followed by more words."""
    rel = rel.strip("/")
    return [f"{a}{rel}{b}" for a in ("", "* ", "*/") for b in ("", "/*", " *")]


def claude_settings(hidden: Hidden, workdir: str | Path = "") -> dict[str, Any]:
    """The `--settings` of a Claude Code turn: `permissions.deny` -- Read and Edit (which `cat`, `head` and the
    like in Bash honour) on each path, absolute (`//...`) and relative to the turn's folder when inside it, Bash on a
    command that names it, and the Grep and Glob tools: Claude Code holds them to Read rules only best-effort, and
    a search over the folder would list and quote the paths (D1169). Its shell's `grep` and `find` remain, under
    the Bash and Read rules. (Claude Code 2.1's native build has neither tool: its searches go through Bash.)"""
    deny: list[str] = ["Grep", "Glob"]
    for a, rel in hidden:
        forms = [f"/{a}"] + ([f"./{r}"] if (r := _rel_to(a, workdir)) else [])
        for f in forms:
            for tool in ("Read", "Edit"):
                deny += [f"{tool}({f})", f"{tool}({f}/**)"]
        deny += [f"Bash({c})" for c in _commands(rel)]
    return {"permissions": {"deny": list(dict.fromkeys(deny))}}


def opencode_permission(hidden: Hidden) -> dict[str, Any]:
    """OpenCode's `permission` for the paths: `read` and `edit` match a file by its path relative to the project
    (the turn's folder, its git root, or `/`), so each path as its loop names it, at the start or after a folder;
    `bash` a command that names it. The longest pattern wins, so each deny outranks the `*` allow. Its `grep` and
    `glob` tools are denied outright (D1169): OpenCode 1.18 checks them against their own rules, not `read`'s, by
    the folder searched -- a grep over the loop's folder printed a hidden file's lines, a glob listed it. Its shell's
    `grep` and `find` remain, under the `bash` rules."""
    files: dict[str, str] = {}
    bash: dict[str, str] = {}
    for _a, rel in hidden:
        r = rel.strip("/")
        for p in (r, f"{r}/*", f"*/{r}", f"*/{r}/*"):
            files[p] = "deny"
        for c in _commands(r):
            bash[c] = "deny"
    return {"permission": {"read": {"*": "allow", **files}, "edit": {"*": "allow", **files}, "bash": bash,
                           "grep": "deny", "glob": "deny"}}


def with_hidden(kind: str, cmd: list[str], env: dict[str, str], hidden: Hidden, workdir: str | Path) -> tuple[list[str], dict[str, str]]:
    """The turn's command and environment with the agent's own rules refusing the hidden paths, by its kind."""
    if not hidden:
        return cmd, env
    if kind == "claude":
        cmd = [*cmd, "--settings", json.dumps(claude_settings(hidden, workdir))]
    elif kind == "opencode":
        try:
            base = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
        except ValueError:
            base = {}
        add = opencode_permission(hidden)["permission"]
        perm = dict(base.get("permission") or {}) if isinstance(base, dict) else {}
        for key, rules in add.items():
            if isinstance(rules, str):                     # a tool denied whole (grep, glob)
                perm[key] = rules
                continue
            had = perm.get(key)
            had = had if isinstance(had, dict) else ({"*": had} if isinstance(had, str) else {})
            perm[key] = {**had, **rules}
        env = {**env, "OPENCODE_CONFIG_CONTENT": json.dumps({**(base if isinstance(base, dict) else {}), "permission": perm})}
    return cmd, env


def matches(path: str, hidden: Hidden) -> bool:
    """Whether `path` is one of the hidden paths or in one of them."""
    return is_hidden(path, [a for a, _r in hidden])


def said_hidden(raw: Any, base: Any, what: str = "flow.knowledge.hidden") -> tuple[str, ...]:
    """The paths `flow.knowledge.hidden` says, checked: a list of files, folders or globs relative to the loop's
    folder, inside it, never the document itself (nor a glob that would take it). A ValueError says what is wrong."""
    from .document.keys import ALT_SUFFIXES, DOCUMENT_FILE

    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(p, str) and p.strip() for p in raw):
        raise ValueError(f"{what} is a list of files, folders or globs in the loop's folder, e.g. [tests/, golden.sv]")
    out = []
    for p in raw:
        p = p.strip()
        clean = p.rstrip("/")
        if Path(p).is_absolute() or p.startswith("~"):
            raise ValueError(f"{what}: {p!r} is said relative to the loop's folder, e.g. tests/")
        if ".." in Path(clean).parts or clean in ("", "."):
            raise ValueError(f"{what}: {p!r} is not inside the loop's folder" if clean not in ("", ".")
                             else f"{what}: {p!r} is the whole loop folder; name the files or folders to hide")
        if "/" not in clean and any(fnmatch.fnmatchcase(n, clean) for n in (DOCUMENT_FILE, "problem.yml", "x" + ALT_SUFFIXES[0])) \
                or clean in (DOCUMENT_FILE, "problem.yml") or clean.endswith(ALT_SUFFIXES):
            raise ValueError(f"{what}: {p!r} is the document itself; the agents read it")
        if base is not None and not _GLOB.search(clean):
            real = (Path(base) / clean).resolve()
            if not real.is_relative_to(Path(base).resolve()):
                raise ValueError(f"{what}: {p!r} leads outside the loop's folder")
        out.append(p)
    return tuple(dict.fromkeys(out))


def absolute(said: Iterable[str], base: Any) -> tuple[tuple[str, str], ...]:
    """Each path said, absolute in the loop's folder `base` (the current folder for an inline document)."""
    root = Path(base if base is not None else ".").resolve()
    return tuple((str(root / p.rstrip("/")) if _GLOB.search(p) else str((root / p.rstrip("/")).resolve()), p.rstrip("/") or p)
                 for p in said)


# ---- the document's text: `flow.knowledge.hidden` set, the rest as written ---------------------------------------
def set_hidden_text(text: str, paths: Sequence[str]) -> str:
    """The document's YAML with `flow.knowledge.hidden` set to `paths` (removed when empty): a targeted edit of its
    lines, the rest as written, checked by reading it back; a document said another way is written again whole."""
    import yaml

    doc = yaml.safe_load(text) or {}
    if not isinstance(doc, dict):
        raise ValueError("the document is not a mapping of keys")
    want = _with_hidden_doc(doc, list(paths))
    try:
        edited = _edit_lines(text, list(paths))
        if edited is not None and yaml.safe_load(edited) == want:
            return edited
    except yaml.YAMLError:
        pass
    return yaml.safe_dump(want, sort_keys=False, allow_unicode=True, width=110, default_flow_style=None)


def _with_hidden_doc(doc: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    import copy

    out = copy.deepcopy(doc)
    flow = out.get("flow") if isinstance(out.get("flow"), dict) else None
    if flow is None:
        if not paths:
            return out
        flow = out["flow"] = {}
    said = flow.get("knowledge")
    k: dict[str, Any] = ({"off": True} if said is False or said == "off" else dict(said) if isinstance(said, dict)
                         else {} if said is None else {"digest": said})
    if paths:
        k["hidden"] = paths
    else:
        k.pop("hidden", None)
    if k == {"off": True}:
        flow["knowledge"] = "off" if flow.get("knowledge") == "off" else False if flow.get("knowledge") is False else k
    elif k:
        flow["knowledge"] = k
    else:
        flow.pop("knowledge", None)
    if not flow:
        out.pop("flow", None)
    return out


def _scalar(p: str) -> str:
    import yaml

    return yaml.safe_dump(p, default_flow_style=True, width=1 << 20).strip().removesuffix("...").strip()


def _edit_lines(text: str, paths: list[str]) -> str | None:
    """`hidden: [..]` put in place among the lines, or None when the document is not laid out as blocks there."""
    value = "[" + ", ".join(_scalar(p) for p in paths) + "]"
    lines = text.split("\n")

    def block(start: int, indent: int) -> int:
        """The end of the block whose children are indented more than `indent`, from `start`."""
        i = start
        while i < len(lines):
            ln = lines[i]
            if ln.strip() and not ln.lstrip().startswith("#") and len(ln) - len(ln.lstrip()) <= indent:
                break
            i += 1
        while i > start and not lines[i - 1].strip():
            i -= 1
        return i

    def child(start: int, end: int, key: str) -> tuple[int, int] | None:
        """(line, indent) of `key:` among the direct children of lines[start:end]."""
        ind = None
        for i in range(start, end):
            ln = lines[i]
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            n = len(ln) - len(ln.lstrip())
            ind = n if ind is None else ind
            if n == ind and re.match(rf"{re.escape(key)}\s*:(\s|$)", ln.lstrip()):
                return i, n
        return None

    top = child(0, len(lines), "flow")
    if top is None:
        if not paths:
            return text
        return text.rstrip("\n") + f"\nflow:\n  knowledge:\n    hidden: {value}\n"
    fi, _ = top
    if lines[fi].split(":", 1)[1].split("#")[0].strip():
        return None                                         # `flow: {...}` inline
    fend = block(fi + 1, 0)
    first = next((i for i in range(fi + 1, fend) if lines[i].strip() and not lines[i].lstrip().startswith("#")), None)
    ind = (len(lines[first]) - len(lines[first].lstrip())) if first is not None else 2
    kn = child(fi + 1, fend, "knowledge")
    if kn is None:
        if not paths:
            return text
        lines[fi + 1:fi + 1] = [f"{' ' * ind}knowledge:", f"{' ' * (ind * 2)}hidden: {value}"]
        return "\n".join(lines)
    ki, kind = kn
    rest = lines[ki].split(":", 1)[1].split("#")[0].strip()
    if rest:
        if rest not in ("off", "false"):
            return None                                     # `knowledge: {...}` or a word: written whole
        if not paths:
            return text
        lines[ki:ki + 1] = [f"{' ' * kind}knowledge:", f"{' ' * (kind + ind)}'off': true", f"{' ' * (kind + ind)}hidden: {value}"]
        return "\n".join(lines)
    kend = block(ki + 1, kind)
    sub = next((i for i in range(ki + 1, kend) if lines[i].strip() and not lines[i].lstrip().startswith("#")), None)
    sind = (len(lines[sub]) - len(lines[sub].lstrip())) if sub is not None else kind + ind
    hi = child(ki + 1, kend, "hidden")
    if hi is None:
        if paths:
            lines[kend:kend] = [f"{' ' * sind}hidden: {value}"]
        return "\n".join(lines)
    h, _ = hi
    hend = block(h + 1, sind)
    if paths:
        lines[h:hend] = [f"{' ' * sind}hidden: {value}"]
    else:
        del lines[h:hend]
        if not any(ln.strip() and not ln.lstrip().startswith("#") for ln in lines[ki + 1:kend - (hend - h)]):
            del lines[ki]                                   # knowledge said nothing else
    return "\n".join(lines)
