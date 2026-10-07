"""The commands a document says (D580): their placeholders, `flux ...` heads, the tools they need."""

from __future__ import annotations

import re
from typing import Any, Iterable, TYPE_CHECKING

from .keys import TaskError

if TYPE_CHECKING:  # pragma: no cover
    from .gate import Gate
    from .stages import Stage


#: `{name}` in a command: the loop's own (`BUILTIN_SUBS`) or a knob of `space:` (D581);
#: a name neither is stays as written (a script's own braces are its business)
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")
_PLACEHOLDER_ANY = re.compile(r"\{(\w+)\}")      # D879: any knob a command says, in a script too
BUILTIN_SUBS = ("artifact", "workdir", "name", "part", "python", "home", "failure", "attempt",
                "prompt", "prompt_file", "point", "params", "history", "state", "parts")


#: what `flux rtl measure` prints (D628): a stage running it need not list them
RTL_METRICS = ("fmax_mhz", "area_um2", "power_w", "cell_count")
RTL_STAT_METRICS = ("area_um2", "cell_count")      # `--stage stat`: nothing timed (D662)


def rtl_tools_kind(cmd: Iterable[str] | None) -> str:
    """"test", "proto", "measure" for a `flux rtl ...` command, else ""."""
    toks = list(cmd or ())
    try:
        at = toks.index("rtl")
    except ValueError:
        return ""
    if at == 0 or "flux" not in " ".join(toks[:at]) or at + 1 >= len(toks):
        return ""
    return toks[at + 1]


def _flux_rtl_tools(cmd: Iterable[str]) -> list[str]:
    """The tools a `flux rtl lint|test|measure` or `flux prog count|size` command runs (D600): they
    may be missing outside the Nix dev shell, and the command itself is Python, so `task check`
    must name them."""
    toks = list(cmd)
    at = next((i for i, t in enumerate(toks) if t in ("rtl", "prog")), None)
    if not at or "flux" not in " ".join(toks[:at]):
        return []
    sub = toks[at + 1] if at + 1 < len(toks) else ""
    if toks[at] == "prog":                # D661: `time` falls back to a Python loop without hyperfine
        return {"count": ["valgrind"], "size": ["size"]}.get(sub, [])
    if sub in ("test", "lint"):
        return ["verilator"]
    if sub == "measure":
        if _stage_of(toks) == "stat":
            return ["yosys"]              # D662: Yosys alone, nothing timed
        return ["yosys", "openroad"]      # synthesis too: its timing is OpenROAD's OpenSTA
    return []


def _stage_of(toks: list[str]) -> str:
    """A `flux rtl measure` command's `--stage` (synth when it says none)."""
    for i, t in enumerate(toks):
        if t == "--stage" and i + 1 < len(toks):
            return toks[i + 1]
        if t.startswith("--stage="):
            return t.split("=", 1)[1]
    return "synth"


def _digest_of(text: str | None) -> str:
    import hashlib

    return hashlib.sha256((text or "").encode()).hexdigest()


def _inferred_language(gate: Any, stages: Any) -> str | None:
    """The language a document need not say (D832): the one the tools its checks and stages name
    take -- `flux rtl ...` is SystemVerilog, a ChampSim build C++ -- from the tool catalog's
    `languages`. A tool that takes several (your own script, `flux prog`) decides nothing; None
    when nothing decides."""
    from ..toolbox import TOOLS

    hdl = {"systemverilog", "verilog"}

    def said(argv: Any) -> str:
        toks = list(argv or ())
        if toks[:5] == ["{python}", "-W", "ignore", "-m", "flux_cli.main"]:
            toks = ["flux", *toks[5:]]
        return " ".join(toks)

    cmds = [said(c.run) for c in (gate or ())] + [said(st.command) for st in (stages or ()) if st.command]
    found: set[str] | None = None
    for cmd in cmds:
        for t in TOOLS:
            head = str(t.get("run") or "").split("{")[0].strip()
            langs = set(t.get("languages") or ())
            if len(head.split()) < 2 or not langs or not cmd.startswith(head):
                continue                                   # `{python} {script}`, `{command}`: no word on it
            if len(langs) == 1 or langs <= hdl:
                found = langs if found is None else (found & langs or found)
    if not found:
        return None
    return "systemverilog" if found <= hdl and "systemverilog" in found else (next(iter(found)) if len(found) == 1 else None)


def _command(raw: Any, what: str) -> tuple[str, ...] | None:
    """A command the document says (D580): argv tokens as a list, or one string split like
    a shell would. A command whose head is `flux` runs this flux (`{python} -m
    flux_cli.main`), so a document reads `flux rtl test {artifact} ...` and needs no
    wrapper on PATH. `{artifact}`, `{workdir}`, `{name}`, `{part}`, `{python}` and
    `{home}` are substituted at run time."""
    if raw is None:
        return None
    if isinstance(raw, str):
        import shlex

        toks = shlex.split(raw)
    elif isinstance(raw, list) and raw and all(isinstance(t, str) for t in raw):
        toks = list(raw)
    else:
        raise TaskError(f"{what} must be a command: a non-empty list of strings, or one string")
    if not toks:
        raise TaskError(f"{what} is empty")
    if toks[0] == "flux":
        # warnings off (D589): runpy and numpy warnings in a refusal mislead the model
        toks = ["{python}", "-W", "ignore", "-m", "flux_cli.main", *toks[1:]]
    return tuple(toks)


def _check_placeholders(gate: "Gate | None", stages: Iterable["Stage"], generator: dict[str, Any],
                        space: dict[str, list], baseline: dict[str, Any] | None = None) -> None:
    """Every `{name}` a command token says (not a `-c` script) must be the loop's or a knob of
    `space:`; otherwise it is a typo that would reach the tool as text (D581)."""
    known = set(BUILTIN_SUBS) | set(space)
    cmds: list[tuple[str, Iterable[str]]] = []
    if gate is not None:
        cmds += [(f"gate {c.name}", c.run) for c in gate]
    cmds += [(f"stage {r.name}", r.command or ()) for r in stages]
    cmds += [(f"estimate {r.name}", r.estimate.command) for r in stages if r.estimate and r.estimate.command]
    if generator.get("command"):
        cmds.append(("generator", generator["command"]))
    if baseline and baseline.get("command"):
        cmds.append(("baseline.command", baseline["command"]))
    said: set[str] = set()
    for what, cmd in cmds:
        for tok in cmd:
            said |= set(_PLACEHOLDER_ANY.findall(tok))
            if any(c.isspace() for c in tok):
                continue
            for m in _PLACEHOLDER.finditer(tok):
                if m.group(1) not in known:
                    raise TaskError(f"{what} says {{{m.group(1)}}}, which is neither a knob of `flow.dse.space` "
                                    f"({', '.join(space) or 'none'}) nor the loop's ({', '.join(BUILTIN_SUBS)})")
    if space and not generator.get("command") and "catalog" not in generator and not (said & set(space)) \
            and "{point}" not in str(cmds):
        # D879: a point becomes a design only through a command that says its knobs; with none, every
        # point was an empty artifact the gate refused, pass after pass (an agent spells no sweep point).
        # D911: a model neither -- the search instantiates a point through the command alone
        who = "a coding agent" if "agent" in generator else "a model"
        raise TaskError(f"flow.dse.space knobs ({', '.join(space)}) are said by no command: a search's points "
                        "become designs through `flow.generate: {command: ... {" + next(iter(space)) + "} ...}` "
                        f"(or a measure command that takes them); {who} does not spell a point")


def _knob_subs(knobs: dict[str, Any]) -> dict[str, str]:
    """A candidate's knobs as `{knob}` substitutions (D581): the scalar ones."""
    return {k: str(v) for k, v in (knobs or {}).items() if isinstance(v, (str, int, float, bool))}


def _substitute(cmd: Iterable[str], subs: dict[str, str]) -> list[str]:
    return [_PLACEHOLDER.sub(lambda m: subs.get(m.group(1), m.group(0)), tok) for tok in cmd]
