"""A coding agent as the generator (D575): Claude Code, Codex CLI, OpenCode or any terminal
tool that takes a brief and writes a file. The loop hands it a work directory, a brief
(`PROMPT.md`: the design prompt, plus the prior artifact and failure on a repair) and a time
limit, then reads the artifact and runs its own build, check and judge as for a model reply.

    generate: {agent: opencode}                                  # a preset
    generate: {agent: {preset: opencode, questions: model, max_questions: 2}}
    generate: {agent: {command: [my-agent, "{prompt_file}", "{artifact}"], timeout_s: 900}}

Questions (D585): a turn that exits cleanly, wrote no artifact and ends on a question is a
question, and `questions:` says who answers it --
    decide    (the default) nobody: the brief says so, and a question that comes anyway is
              answered "choose yourself, say what you chose, write the file"
    model     the loop's model, as the designer who wrote the brief
    operator  the person at the TUI's prompt line (or the run's feedback channel), within
              `wait_s`; unanswered, the model answers, else "choose yourself"
The answer goes back into the SAME session (OpenCode `--session`, Claude Code `--resume`);
an agent the loop cannot resume gets a fresh run whose brief carries the exchange. At most
`max_questions` a draft; every exchange is said in the log and kept on the candidate.

Sessions (D669): a generate agent keeps ONE session per part until the part is admitted -- the
first draft, the gate's repairs and the critic's send-backs resume it with a short message (what
failed, the file, "fix it"); an agent that cannot resume gets the full brief again. A decision
box's agent takes `session: turn` (a fresh agent every turn, the default) or `session: pass`
(one session per box for the pass, resumed turn after turn):
    critique: {agent: {preset: opencode, session: pass}}

The brief goes on the agent's stdin (D672), and a resume's message too: no argument carries it,
so its size is not bounded by the command line. A custom command that names `{prompt}`,
`{prompt_file}` or `{answer}` gets it there instead (a `{prompt}` over INLINE_MAX through a file).

Substitutions in a command: `{prompt}` (the brief's text), `{prompt_file}` (its path),
`{artifact}` (where to write), `{workdir}`, `{part}`, `{name}`, `{python}`, `{workbench}` (the
agents' folder of tools and notes, D677; "" without one); in a `resume` command also
`{session}` and `{answer}`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

__all__ = ["AgentSpec", "DECIDE", "SESSIONS", "Exchange", "PRESETS", "Turn", "agent_brief", "agent_spec", "converse", "missing_agent", "question_in", "run_turn"]

#: The agents this repository knows how to call headless: the first turn, the turn that
#: resumes its session with an answer, and how its output says the session and its words.
#: The prompt (and a resume's answer) goes on stdin: no argument-size limit, nothing on the
#: command line (D672). Each reads stdin when its prompt argument is left out (codex: `-`).
#: The design is the loop's to run (D673): the agent has a shell for reading, searching and
#: computing (python3, pdftotext), without the commands that compile, simulate, synthesize or
#: test, and without `bash` / `sh` (a `bash -c` would pass by the list). Best effort: the brief
#: says it too, and Codex, whose shell is its only tool, has only the brief.
#: Not iverilog / vvp (D685): an agent may simulate its own draft with Icarus.
DENIED = ("verilator", "yosys", "openroad", "sta", "klayout", "champsim", "timeloop-model",
          "timeloop-mapper", "gcc", "g++", "cc", "c++", "clang", "clang++", "make", "cmake", "ninja", "pytest",
          "flux rtl", "flux task", "flux run", "bash", "sh")
_CLAUDE_DENY = ("--allowedTools", "Bash", "--disallowedTools", "AskUserQuestion", *(f"Bash({c}:*)" for c in DENIED))
# D710: and paths outside its working folder -- the loop's own files, its log, an Ask's loop --
# which OpenCode otherwise asks for, and `opencode run` cannot ask: refused. The sandbox is the
# boundary, and the shell it is allowed reads them anyway.
_OPENCODE_DENY = {"permission": {"external_directory": "allow",
                                 "bash": {"*": "allow", **{k: "deny" for c in DENIED for k in (c, f"{c} *")}}}}

PRESETS: dict[str, dict[str, Any]] = {
    "claude": {"argv": ("claude", "-p", "--permission-mode", "acceptEdits", "--output-format", "stream-json",
                        "--verbose", "--include-partial-messages", *_CLAUDE_DENY),
               "resume": ("claude", "-p", "--resume", "{session}", "--permission-mode", "acceptEdits",
                          "--output-format", "stream-json", "--verbose", "--include-partial-messages", *_CLAUDE_DENY),
               "output": "claude", "add_dir": ("--add-dir",)},
    "codex": {"argv": ("codex", "exec", "--full-auto", "-"), "resume": None, "output": "text"},
    "opencode": {"argv": ("opencode", "run", "--format", "json", "--thinking", "--dir", "{workdir}"),
                 "resume": ("opencode", "run", "--format", "json", "--thinking", "--dir", "{workdir}", "--session", "{session}"),
                 "output": "opencode", "config": {"OPENCODE_CONFIG_CONTENT": _OPENCODE_DENY}},
}
OUTPUTS = ("text", "opencode", "claude")
POLICIES = ("decide", "model", "operator")
SESSIONS = ("turn", "pass")


@dataclass(frozen=True)
class AgentSpec:
    tool: str
    argv: tuple[str, ...]
    resume: tuple[str, ...] | None = None
    output: str = "text"
    timeout_s: float = 1800.0
    questions: str = "decide"
    max_questions: int = 2
    wait_s: float = 300.0
    session: str = "turn"          # a decision box's span: a fresh agent each turn, or one per pass (D669)
    config: tuple[tuple[str, str], ...] = ()   # JSON merged into these variables of the agent's environment (D673)
    add_dir: tuple[str, ...] = ()  # the option that opens a folder outside the work directory (the workbench, D677)
    probe: tuple[tuple[str, int], ...] | None = (("gate", 20), ("stages", 3))   # `flux probe` per turn (D678); None = off
    allowed: tuple[str, ...] = ()  # denied commands this agent was given back (`allow:`, D678)


def agent_spec(spec: Any) -> AgentSpec:
    """The document's `agent:` value: a preset's name, or an object with `preset` or
    `command` (+ `resume`, `output`) and `timeout_s`, `questions`, `max_questions`, `wait_s`,
    `session` (turn | pass)."""
    if isinstance(spec, str):
        spec = {"preset": spec}
    if not isinstance(spec, dict):
        raise ValueError("agent: a preset's name or {preset|command, timeout_s, questions, ...}")
    known = {"preset", "command", "resume", "output", "name", "timeout_s", "questions", "max_questions", "wait_s", "bin", "args",
             "session", "probe", "allow"}
    unknown = sorted(set(spec) - known)
    if unknown:
        raise ValueError(f"agent: {', '.join(unknown)} is not one of {', '.join(sorted(known))}")
    questions = str(spec.get("questions") or "decide")
    if questions not in POLICIES:
        raise ValueError(f"agent.questions is one of {', '.join(POLICIES)}, not {questions!r}")
    session = str(spec.get("session") or "turn")
    if session not in SESSIONS:
        raise ValueError(f"agent.session is one of {', '.join(SESSIONS)}, not {session!r}")
    common = dict(timeout_s=float(spec.get("timeout_s") or 1800.0), questions=questions,
                  max_questions=int(spec.get("max_questions", 2)), wait_s=float(spec.get("wait_s") or 300.0),
                  session=session, probe=_probe(spec.get("probe", True)))
    allow = spec.get("allow") or []
    if allow == "all":
        allow = list(DENIED)                          # the restriction lifted for this agent (D678)
    if isinstance(allow, str) or not all(isinstance(a, str) for a in allow):
        raise ValueError("agent.allow is `all`, or a list of denied commands to give back, e.g. [verilator, yosys]")
    unknown_allow = sorted(set(allow) - set(DENIED))
    if unknown_allow:
        raise ValueError(f"agent.allow: {', '.join(unknown_allow)} is not denied; denied: {', '.join(DENIED)}")
    if spec.get("command") and (spec.get("bin") or spec.get("args")):
        raise ValueError("agent: `bin` and `args` adjust a preset; with your own `command`, write them there")
    if spec.get("command"):
        argv = tuple(str(t) for t in spec["command"])
        output = str(spec.get("output") or "text")
        if output not in OUTPUTS:
            raise ValueError(f"agent.output is one of {', '.join(OUTPUTS)}, not {output!r}")
        first = next((t for t in argv if not t.startswith("{")), argv[0])       # `{python} my-agent.py`: the script
        resume = tuple(str(t) for t in spec["resume"]) if spec.get("resume") else None
        return AgentSpec(str(spec.get("name") or Path(first).name), argv, resume, output, **common)
    preset = str(spec.get("preset") or "")
    if preset not in PRESETS:
        raise ValueError(f"agent {preset!r} is not a preset; presets: {', '.join(PRESETS)}; or give `command: [...]`")
    p = PRESETS[preset]
    # the executable alone may differ per machine (an installed name, a path): the document's
    # `bin`, else FLUX_<PRESET>_BIN, else the preset's own; the arguments stay the preset's (D670)
    exe = str(spec.get("bin") or os.environ.get(f"FLUX_{preset.upper()}_BIN") or p["argv"][0])
    exe = os.path.expanduser(exe)
    # extra arguments, e.g. OpenCode's `--agent flux`: the document's `args`, else FLUX_<PRESET>_ARGS
    extra = spec.get("args")
    if extra is None:
        import shlex

        extra = shlex.split(os.environ.get(f"FLUX_{preset.upper()}_ARGS", ""))
    if isinstance(extra, str) or not all(isinstance(a, (str, int, float)) for a in extra):
        raise ValueError("agent.args is a list of arguments, e.g. [--agent, flux]")
    extra = tuple(str(a) for a in extra)
    argv = _with_args((exe, *_allowed(p["argv"][1:], allow)), extra)
    resume = _with_args((exe, *_allowed(p["resume"][1:], allow)), extra) if p["resume"] else None
    config = tuple((k, json.dumps(_allowed_config(v, allow))) for k, v in (p.get("config") or {}).items())
    return AgentSpec(preset, argv, resume, p["output"], **common, config=config, add_dir=tuple(p.get("add_dir") or ()),
                     allowed=tuple(allow))


def _probe(value: Any) -> tuple[tuple[str, int], ...] | None:
    """`probe:` (D678): true (the default budget), false (off), or {gate: N, <stage>: N, stages: N}."""
    from .probe import PROBE_DEFAULT

    if value is True or value is None:
        return tuple(PROBE_DEFAULT.items())
    if value is False:
        return None
    if not isinstance(value, dict) or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value.values()):
        raise ValueError("agent.probe is true, false, or {gate: N, <stage>: N, stages: N} (probes per turn)")
    return tuple({**PROBE_DEFAULT, **{str(k): int(v) for k, v in value.items()}}.items())


def _allowed(argv: tuple[str, ...], allow: list[str]) -> tuple[str, ...]:
    """A preset's arguments without the denies `allow` gives back (D678)."""
    gone = {f"Bash({c}:*)" for c in allow}
    return tuple(a for a in argv if a not in gone)


def _allowed_config(config: Any, allow: list[str]) -> Any:
    if not allow or not isinstance(config, dict):
        return config
    bash = ((config.get("permission") or {}).get("bash"))
    if not isinstance(bash, dict):
        return config
    gone = {k for c in allow for k in (c, f"{c} *")}
    return {**config, "permission": {**config["permission"], "bash": {k: v for k, v in bash.items() if k not in gone}}}


def _merged(base: Any, over: Any) -> Any:
    """`over` merged into `base`, objects key by key; `over` wins elsewhere."""
    if isinstance(base, dict) and isinstance(over, dict):
        return {**base, **{k: _merged(base.get(k), v) for k, v in over.items()}}
    return over


def _config_env(spec: AgentSpec, env: dict[str, str]) -> dict[str, str]:
    """The spec's JSON config merged into what the environment already holds there (D673)."""
    for key, text in spec.config:
        try:
            base = json.loads(env.get(key) or "{}")
        except ValueError:
            base = {}
        env[key] = json.dumps(_merged(base, json.loads(text)))
    return env


def _with_args(argv: tuple[str, ...], extra: tuple[str, ...]) -> tuple[str, ...]:
    """`extra` among the tool's options: before a trailing prompt slot (`-`, `{prompt}`,
    `{answer}`), else at the end."""
    if extra and argv and argv[-1] in ("-", "{prompt}", "{answer}"):
        return (*argv[:-1], *extra, argv[-1])
    return (*argv, *extra)


#: What the brief says about questions, by policy.
_ASKING = {
    "decide": ("Nobody answers questions during this run: where the brief leaves a choice open, make it yourself, "
               "say in your final line what you chose, and write the file."),
    "model": ("If a choice the brief leaves open truly blocks you, you may end your reply with ONE question and write "
              "nothing yet; it will be answered and you will continue. Otherwise decide yourself and write the file."),
}
_ASKING["operator"] = _ASKING["model"]

#: Who runs what (D673): the agent writes, the loop runs.
DENIED_LINE = ("Do not compile, lint, simulate, synthesize or test the file with the raw tools: they are denied to "
               "you. ")
HANDOFF = ("The loop runs the gate and the measurements on the file you write and, when something fails, "
           "comes back to you in this session with its exact output. Use the shell to read, search and "
           "compute (python3 for a calculation, pdftotext for a PDF); write the file and end your turn.")

#: The answer when nobody answers.
DECIDE = ("Nobody is available to answer questions during this run. Choose the option you judge best for the brief, "
          "say in one line what you chose, and write the file now.")


WORKBENCH_LIST_MAX = 3000


def workbench_link(bench: str, workdir: Path) -> Path | None:
    """The workbench (D677) made if new (`tools/`, `notes/`) and linked into the agent's work
    directory as `workbench/`; None without one."""
    if not bench:
        return None
    root = Path(bench)
    for sub in ("tools", "notes"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    link = workdir / "workbench"
    if not link.exists() and not link.is_symlink():
        link.symlink_to(root, target_is_directory=True)
    return root


def _first_line(path: Path) -> str:
    try:
        with path.open(errors="replace") as fh:
            for _ in range(20):
                ln = fh.readline()
                if not ln:
                    break
                ln = ln.strip().lstrip("#").strip().strip('"').strip("'").strip()
                if ln and not ln.startswith("!") and not ln.startswith("-*-"):
                    return ln[:120]
    except OSError:
        pass
    return ""


def workbench_section(bench: str) -> str:
    """The WORKBENCH section of a brief (D677): what the folder is for, and what is in it now
    (each file with its first line), within a budget; "" without one."""
    if not bench:
        return ""
    root = Path(bench)
    files = sorted((p for p in root.rglob("*") if p.is_file() and p.suffix not in (".pyc", ".pyo")
                    and not any(q.startswith(".") or q == "__pycache__" for q in p.relative_to(root).parts)),
                   key=lambda p: str(p.relative_to(root)))
    lines, used = [], 0
    for p in files:
        first = _first_line(p)
        line = f"  workbench/{p.relative_to(root)}" + (f" -- {first}" if first else "")
        if used + len(line) > WORKBENCH_LIST_MAX:
            lines.append(f"  ... and {len(files) - len(lines)} more (look in the folder)")
            break
        lines.append(line)
        used += len(line)
    held = "\n".join(lines) if lines else "  (empty: you are the first)"
    return ("YOUR WORKBENCH: `workbench/` in your directory. It is one folder for every agent working on this "
            "problem, and it is kept across runs, so what you leave there helps the next agent, and you in a "
            "later session. The loop gives it to you and never reads it: it is your own knowledge, built while "
            "you work. Put tools you build in `workbench/tools/`: scripts that compute, generate or analyse, "
            "such as fitting coefficients, making a table, or checking an identity numerically. Give each a "
            "first line saying what it does. Put notes in `workbench/notes/`: the method, what failed and why, "
            "facts you worked out, what the next agent should know. Start each with a one-line summary. Use "
            "what is there before rebuilding it; correct a note that turned out wrong. Before you end a turn in "
            "which you worked something out (a method, a number, why a draft failed), leave it there for the "
            "next agent. The draft goes to its own path, not here. A tool may not run the design tools either.\n"
            f"What it holds now:\n{held}")


def library_section(problem: Any, question: str | list[str], state: Any = None) -> str:
    """The LIBRARY section of an agent's brief (D648): one line per paper and the absolute
    paths of the files nearest `question`, from the problem's `library` source; "" without one."""
    try:
        mentor = problem.knowledge()
        lib = mentor.source("library") if mentor is not None and hasattr(mentor, "source") else None
    except Exception:  # noqa: BLE001 -- no knowledge, no section
        return ""
    if lib is None:
        return ""
    from flux_knowledge import agent_section

    db = str(getattr(getattr(state, "request", None), "db", "") or "")
    return agent_section(question, getattr(lib, "folders", ()), db)


def agent_brief(*, body: str, prefix: str, artifact: Path, workdir: Path, language: str, part: str,
                prior: str | None, failure: str, questions: str = "decide", library: str = "",
                workbench: str = "", probes: str = "", denied: bool = True) -> str:
    """The brief an agent reads: the static prefix (contract, knowledge), the design or the
    repair prompt, the LIBRARY section, then what the loop expects of a terminal tool --
    including whether its questions will be answered."""
    # the model half's reply shape (JSON with the artifact) is not how an agent answers: it writes the file
    prefix = "\n\n".join(p for p in prefix.split("\n\n") if not p.lstrip().startswith("REPLY SHAPE"))
    parts = [p for p in (prefix.strip(), body.strip(), library.strip(), workbench.strip()) if p]
    if prior:
        parts.append(f"THE LAST DRAFT (refused: {failure.strip()[:2000] or 'see above'}):\n```\n{prior}\n```")
    parts.append(
        f"HOW TO ANSWER. You are a coding agent working in `{workdir}`. Write the complete {language} "
        f"artifact for `{part}` to `{artifact}` (create the file; that file is what gets built and tested, "
        f"nothing else is read). " + (DENIED_LINE if denied else "") + HANDOFF + (f" {probes}" if probes else "") + " When the file is written, reply with one line saying so. "
        f"{_ASKING.get(questions, _ASKING['decide'])}")
    return "\n\n".join(parts) + "\n"


@dataclass
class Turn:
    """One run of the agent: whether it exited 0, its code, its words (the text it ended on,
    parsed out of its output format), the session to resume, and the raw streams."""

    ok: bool
    rc: int
    text: str
    session: str | None = None
    stdout: str = ""
    stderr: str = ""
    resumed: bool = False          # this run resumed a session (D669)
    began: str = "fresh"           # how the conversation that ended on this turn began: fresh | resumed
    about: str = ""                # which model and tool version answered, as far as known (D696)
    tools: int = 0                 # the tool calls it made


def _parse(output: str, stdout: str) -> tuple[str, str | None]:
    """(the agent's words, its session) from its output format."""
    if output == "opencode":
        texts, session = [], None
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            session = ev.get("sessionID") or session
            if ev.get("type") == "text":
                texts.append(str((ev.get("part") or {}).get("text") or ""))
        return "\n".join(t for t in texts if t.strip()), session
    if output == "claude":
        # stream-json: the `result` event holds the answer and the session (D668); a turn
        # stopped before it (a timeout) still names its session on every event (D677)
        session = None
        for line in reversed(stdout.strip().splitlines()):
            try:
                doc = json.loads(line)
            except ValueError:
                continue
            if isinstance(doc, dict) and doc.get("type") == "result":
                return str(doc.get("result") or ""), doc.get("session_id")
            if isinstance(doc, dict) and session is None:
                session = doc.get("session_id")
        return stdout, session
    return stdout, None


def usage(output: str, stdout: str) -> dict[str, float]:
    """What a turn cost, as the agent reported it (D694): tokens in (cache reads included), out,
    read from the cache, and the cost in USD when the agent prices it. Claude's `result` event
    carries the turn's total; OpenCode says each step's in its `step_finish`, summed here."""
    got: dict[str, float] = {}

    def add(key: str, v: Any) -> None:
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
            got[key] = got.get(key, 0) + v

    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{") or ('"step_finish"' not in line and '"result"' not in line):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if output == "opencode" and ev.get("type") == "step_finish":
            part = ev.get("part") or {}
            tok = part.get("tokens") or {}
            cache = tok.get("cache") or {}
            add("tokens_in", (tok.get("input") or 0) + (cache.get("read") or 0) + (cache.get("write") or 0))
            add("tokens_out", (tok.get("output") or 0) + (tok.get("reasoning") or 0))
            add("tokens_cached", cache.get("read"))
            add("cost_usd", part.get("cost"))
        elif output == "claude" and ev.get("type") == "result":
            u = ev.get("usage") or {}
            add("tokens_in", (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                + (u.get("cache_creation_input_tokens") or 0))
            add("tokens_out", u.get("output_tokens"))
            add("tokens_cached", u.get("cache_read_input_tokens"))
            add("cost_usd", ev.get("total_cost_usd"))
    return got


def run_turn(spec: AgentSpec, argv: tuple[str, ...], subs: dict[str, str], *, workdir: Path) -> Turn:
    """The agent run once, recorded in the run's transcript (D599)."""
    import time

    from flux_llm import transcript

    t0 = time.monotonic()
    turn = _run_turn(spec, argv, subs, workdir=workdir)
    turn.resumed = spec.resume is not None and argv == spec.resume
    if turn.resumed:
        turn.began = "resumed"
        turn.session = turn.session or subs.get("session")     # an output that does not repeat its id
    transcript.record("agent", agent=spec.tool, workdir=str(workdir),
                      prompt=subs.get("answer", "") if turn.resumed else subs.get("prompt", ""), ok=turn.ok,
                      rc=turn.rc, reply=turn.text, stderr=(turn.stderr or "")[-2000:], seconds=round(time.monotonic() - t0, 2),
                      session="resumed" if turn.resumed else "fresh", session_id=turn.session or "",
                      about=turn.about, tool_calls=turn.tools, prompt_chars=len(subs.get("answer", "") if turn.resumed else subs.get("prompt", "")),
                      **usage(spec.output, turn.stdout or ""))
    return turn


def _detail(tool: str, args: Any) -> str:
    """One line for a tool call: the command, the file or the pattern it was given."""
    if not isinstance(args, dict):
        return tool
    for key in ("command", "filePath", "file_path", "path", "pattern", "query", "url", "description"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            v = " ".join(v.split())
            if key in ("filePath", "file_path", "path") and "/" in v:
                v = v.rsplit("/", 1)[1] or v
            return f"{tool}: {v[:100]}" + ("..." if len(v) > 100 else "")
    return tool


class _Live:
    """What a running agent is doing, read from its output as it streams (D668, D675): each tool
    call with its command or file, the tail of its thinking and of its words, and the last tool
    output, in the fields the model's own turns use."""

    TAIL = 1500
    OUT_TAIL = 400

    def __init__(self, output: str) -> None:
        self.output, self.tools, self.words, self.thinking, self.result = output, [], "", "", ""
        self.streamed = False                      # claude: the words came token by token
        self.thought_tokens = 0                    # claude: redacted thinking, counted
        # D676: signs of life, so a silent agent says why -- the model and version it started
        # with, its status (requesting, an API retry, an error), a rate limit that holds it,
        # its stderr, and how long since its last output line
        self.agent = self.status = self.limit = self.err = ""
        self.lines, self.last = 0, None

    def feed_err(self, line: str) -> None:
        self.err = (self.err + line)[-self.OUT_TAIL:]

    def _system(self, ev: dict[str, Any]) -> None:
        sub = str(ev.get("subtype") or "")
        if sub == "init":
            model, version = ev.get("model"), ev.get("claude_code_version")
            self.agent = ", ".join(str(x) for x in (model, version and f"Claude Code {version}") if x)
            return
        if sub == "status":
            self.status = str(ev.get("status") or "")
            return
        skip = {"type", "subtype", "uuid", "session_id"}
        rest = ", ".join(f"{k} {v}" for k, v in ev.items() if k not in skip and v not in (None, "", [], {}))
        self.status = f"{sub}: {rest}"[:300] if rest else sub

    def _rate(self, ev: dict[str, Any]) -> None:
        info = ev.get("rate_limit_info") or {}
        st = str(info.get("status") or "")
        if st in ("", "allowed"):
            self.limit = ""
            return
        when = info.get("resetsAt")
        at = ""
        if isinstance(when, (int, float)):
            import time as _t

            at = ", resets " + _t.strftime("%a %H:%M", _t.localtime(when))
        self.limit = f"{st} ({info.get('rateLimitType') or 'limit'}{at})"

    def _say(self, text: str, *, sep: str = "\n") -> None:
        self.words = (self.words + text + sep)[-self.TAIL:]

    def _think(self, text: str, *, sep: str = "\n") -> None:
        self.thinking = (self.thinking + text + sep)[-self.TAIL:]

    def feed(self, line: str, now: float | None = None) -> None:
        self.lines += 1
        self.last = now
        if self.output == "text":
            self.words = (self.words + line)[-self.TAIL:]
            return
        try:
            ev = json.loads(line)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        if self.output == "opencode":
            part = ev.get("part") or {}
            kind = ev.get("type")
            if kind == "tool_use":
                state = part.get("state") or {}
                self.tools.append(_detail(str(part.get("tool") or "tool"), state.get("input")))
                out = state.get("output") or state.get("error")
                if isinstance(out, str) and out.strip():
                    self.result = out.strip()[-self.OUT_TAIL:]
            elif kind == "text":
                self._say(str(part.get("text") or ""))
            elif kind == "reasoning":                # with --thinking
                self._think(str(part.get("text") or ""))
            elif kind == "step_start":
                self.status = "model step"
            elif kind == "step_finish":
                self.status = "step done"
            elif kind == "error":
                err = ev.get("error") or {}
                msg = (err.get("data") or {}).get("message") if isinstance(err, dict) else None
                self.status = f"error: {msg or err}"[:300]
            return
        kind = ev.get("type")                        # claude stream-json
        if kind == "system":
            self._system(ev)
        elif kind == "rate_limit_event":
            self._rate(ev)
        elif kind == "stream_event":                   # --include-partial-messages: token by token
            e = ev.get("event") or {}
            d = e.get("delta") or {}
            if e.get("type") == "message_start":
                self.status = "responding"
            if e.get("type") == "content_block_delta":
                if d.get("type") == "text_delta":
                    self.streamed = True
                    self._say(str(d.get("text") or ""), sep="")
                elif d.get("type") == "thinking_delta":
                    if d.get("thinking"):
                        self._think(str(d["thinking"]), sep="")
                    else:                            # redacted: only its size
                        self.thought_tokens += int(d.get("estimated_tokens") or 0)
            elif e.get("type") == "content_block_stop" and self.words and not self.words.endswith("\n"):
                self.words += "\n"
        elif kind == "assistant":
            for c in (ev.get("message") or {}).get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    self.tools.append(_detail(str(c.get("name") or "tool"), c.get("input")))
                elif c.get("type") == "text" and not self.streamed:
                    self._say(str(c.get("text") or ""))
                elif c.get("type") == "thinking" and c.get("thinking") and not self.thinking:
                    self._think(str(c["thinking"]))
        elif kind == "user":                         # a tool's result
            for c in (ev.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    body = c.get("content")
                    if isinstance(body, list):
                        body = "\n".join(str(b.get("text") or "") for b in body if isinstance(b, dict))
                    if str(body or "").strip():
                        self.result = str(body).strip()[-self.OUT_TAIL:]

    def fields(self, now: float | None = None, t0: float | None = None) -> dict[str, str]:
        out = {}
        if self.agent:
            out["agent"] = self.agent
        if now is not None:
            out["output"] = (f"{self.lines} lines, the last {now - self.last:.0f}s ago" if self.last is not None
                             else "none yet" + (f" after {now - t0:.0f}s" if t0 is not None else ""))
        if self.status:
            out["status"] = self.status
        if self.limit:
            out["rate limit"] = self.limit
        if self.err.strip():
            out["stderr"] = self.err
        if self.tools:
            shown = self.tools[-8:]
            first = len(self.tools) - len(shown) + 1
            out["tool calls"] = "\n".join(f"{first + i}. {t}" for i, t in enumerate(shown))   # numbered: the count
        if self.result:
            out["last tool output"] = self.result
        if self.thinking.strip():
            out["thinking (live tail)"] = self.thinking
        elif self.thought_tokens:
            out["thinking"] = f"about {self.thought_tokens:,} tokens (the tool does not show its thinking)"
        if self.words.strip():
            out["reply (live tail)"] = self.words
        return out


#: The largest prompt passed inline; Linux refuses one argument over 128 KiB (MAX_ARG_STRLEN)
#: with E2BIG, so a longer one goes through a file the agent reads (D671).
INLINE_MAX = 100_000


def _inline(subs: dict[str, str], workdir: Path) -> dict[str, str]:
    """`subs` with a `{prompt}` or `{answer}` too long for one argument written to a file in
    `workdir`, and the argument replaced by an instruction to read that file."""
    out = dict(subs)
    for key in ("prompt", "answer"):
        text = out.get(key)
        if text is None or len(text.encode()) <= INLINE_MAX:
            continue
        path = workdir / f".flux-{key}-{hashlib.sha256(text.encode()).hexdigest()[:12]}.md"
        path.write_text(text)
        out[key] = (f"Your instructions are in the file {path} ({len(text):,} characters, too long to pass "
                    f"here). Read that whole file first and follow it exactly.")
    return out


def _run_turn(spec: AgentSpec, argv: tuple[str, ...], subs: dict[str, str], *, workdir: Path) -> Turn:
    """The agent run once, its output streamed into the running task's row as it comes (D668).
    A missing binary or a timeout is a refusal with its own words, never a crash."""
    import os
    import queue
    import threading
    import time

    from flux_profile import progress

    from .observe import _phase

    cmd = [t.format(**_inline(subs, workdir)) for t in argv]
    if spec.add_dir and subs.get("workbench"):
        cmd += [*spec.add_dir, subs["workbench"]]  # its real path: a tool that checks it sees past the link (D677)
    home = subs.get("home")
    if spec.add_dir and home and home != "." and not Path(home).resolve().is_relative_to(Path(str(workdir)).resolve()):
        cmd += [*spec.add_dir, str(Path(home).resolve())]   # D710: the loop's folder, read from outside the workdir
    if shutil.which(cmd[0]) is None and not Path(cmd[0]).is_file():
        return Turn(False, 127, "", stderr=f"{cmd[0]} is not on PATH (the coding agent named by the document)")
    # stdin closed: an agent that reads a piped prompt from stdin (OpenCode) would otherwise
    # block on the loop's inherited socket until the timeout.
    # PWD set too (D586): OpenCode takes its project directory from `PWD`, not the cwd.
    env = _config_env(spec, {**os.environ, "PWD": str(workdir)})
    if subs.get("probe"):
        env["FLUX_PROBE"] = subs["probe"]            # `flux probe` finds its turn's context (D678)
    # the prompt on stdin unless the command names a slot for it (D672); a resume's answer
    # comes in `answer`. With nothing to send, stdin is closed so no agent waits on it.
    slots = {m for t in argv for m in re.findall(r"\{(\w+)\}", t)}
    feed = None if slots & {"prompt", "prompt_file", "answer"} else subs.get("answer", subs.get("prompt"))
    proc = subprocess.Popen(cmd, cwd=str(workdir), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.PIPE if feed is not None else subprocess.DEVNULL,
                            text=True, env=env, bufsize=1)
    lines: queue.Queue = queue.Queue()
    err: list[str] = []

    def write() -> None:
        try:
            proc.stdin.write(feed)
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    live, out = _Live(spec.output), []
    live.agent = _about(cmd[0], env)                  # D702: the model and version from the start (Claude Code says its own)

    def errors() -> None:
        for ln in proc.stderr:
            err.append(ln)
            live.feed_err(ln)                         # shown live too (D676)

    readers = [threading.Thread(target=lambda: [lines.put(ln) for ln in proc.stdout] and None, daemon=True),
               threading.Thread(target=errors, daemon=True)]
    if feed is not None:
        readers.append(threading.Thread(target=write, daemon=True))
    for t in readers:
        t.start()
    t0 = time.monotonic()
    timed_out = False
    shown = 0.0
    with _phase(f"agent: {spec.tool}", why=subs.get("name") or subs.get("part") or "") as row:
        while True:
            try:
                line = lines.get(timeout=1.0)
                out.append(line)
                live.feed(line, time.monotonic())
            except queue.Empty:
                if proc.poll() is not None and not readers[0].is_alive():
                    break
            now = time.monotonic()
            if now - t0 > spec.timeout_s and proc.poll() is None:
                proc.kill()
                timed_out = True
            if now - shown >= 1.0:                    # the row, at most once a second
                shown = now
                progress(elapsed=f"{now - t0:.0f}s", **live.fields(now, t0))
        for t in readers:
            t.join(timeout=5)
        row.update(live.fields())
        row["exit"] = proc.returncode
    stdout = "".join(out)
    text, session = _parse(spec.output, stdout)
    about = live.agent
    if timed_out:                                     # its session kept: a later turn may resume it
        return Turn(False, 124, "", session, stdout=stdout, stderr=f"the agent ran past {spec.timeout_s:.0f}s and was stopped",
                    about=about, tools=len(live.tools))
    return Turn(proc.returncode == 0, proc.returncode, text, session, stdout, "".join(err), about=about, tools=len(live.tools))


_VERSIONS: dict[str, str] = {}


def _about(exe: str, env: dict[str, str]) -> str:
    """An agent that does not say which model answered (OpenCode): the model its configuration
    names, and the tool's version (asked once per process)."""
    model = ""
    try:
        model = str(json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}").get("model") or "")
    except ValueError:
        pass
    if exe not in _VERSIONS:
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
            _VERSIONS[exe] = (r.stdout.strip().splitlines() or [""])[0][:60] if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            _VERSIONS[exe] = ""
    version = _VERSIONS[exe]
    return ", ".join(x for x in (model, version and f"{Path(exe).name} {version}") if x)


_CODE = re.compile(r"```.*?```", re.S)


def question_in(text: str) -> str | None:
    """The question an agent ended its turn on, or None: the last paragraph of its words
    (code fences aside), when it asks -- a line ending in `?`, the options under it kept."""
    words = _CODE.sub("", text or "").strip()
    if not words:
        return None
    last = [p for p in re.split(r"\n\s*\n", words) if p.strip()]
    tail = "\n\n".join(last[-2:]).strip()
    return tail[-1500:] if any(ln.rstrip().endswith("?") for ln in tail.splitlines()) else None


@dataclass
class Exchange:
    question: str
    answer: str
    by: str                                  # decide | model | operator


def converse(spec: AgentSpec, subs: dict[str, str], *, workdir: Path, artifact: Path,
             answer: Callable[[str], tuple[str, str]], say: Callable[[str], None] = lambda _m: None,
             prompt_file: Path | None = None, session: str | None = None,
             message: str = "") -> tuple[Turn, list[Exchange]]:
    """The agent's turn, and every question it ends on answered (by `answer(question) ->
    (text, by)`) until it writes the artifact, stops asking, fails, or has asked
    `max_questions`. The answer resumes its session; without one, a fresh run's brief
    carries the exchange.

    Given a `session` (D669), the turn resumes it with `message` instead of the brief; a
    session that cannot be resumed (gone, failed, out of context) starts fresh from
    `subs["prompt"]`, the full brief."""
    before = artifact.read_text() if artifact.is_file() else None     # a prototype's file exists to be edited

    def wrote() -> bool:
        return artifact.is_file() and artifact.read_text() != before

    if session and spec.resume:
        turn = run_turn(spec, spec.resume, {**subs, "session": session, "answer": message}, workdir=workdir)
        if (not turn.ok and not wrote()) or overflowed(turn):
            why = "out of context" if overflowed(turn) else f"exited {turn.rc}"
            say(f"  agent {spec.tool}: session {session} could not be resumed ({why}); a fresh session starts from the brief")
            if prompt_file is not None:
                prompt_file.write_text(subs["prompt"])
            turn = run_turn(spec, spec.argv, subs, workdir=workdir)
    else:
        turn = run_turn(spec, spec.argv, subs, workdir=workdir)
    began = turn.began
    asked: list[Exchange] = []
    while (turn.ok and not wrote() and len(asked) < spec.max_questions
           and (q := question_in(turn.text)) is not None):
        text, by = answer(q)
        asked.append(Exchange(q, text, by))
        say(f"  agent {spec.tool} asks: {q.splitlines()[0][:160]}" + (" ..." if "\n" in q else ""))
        say(f"  answered by the {by}: {text.splitlines()[0][:160] if text else '(nothing)'}")
        if spec.resume and turn.session:
            turn = run_turn(spec, spec.resume, {**subs, "session": turn.session, "answer": text}, workdir=workdir)
        else:
            brief = subs["prompt"] + "".join(f"\n\nYOU ASKED: {e.question}\nTHE ANSWER: {e.answer}" for e in asked)
            if prompt_file is not None:
                prompt_file.write_text(brief)
            turn = run_turn(spec, spec.argv, {**subs, "prompt": brief}, workdir=workdir)
    # An agent that ends a turn without writing its file is nudged to write it (D618).
    for n in range(NUDGES):
        full = overflowed(turn)
        if (not turn.ok and not full) or wrote() or question_in(turn.text) is not None:
            break
        nudge = (f"You have not {'changed' if before is not None else 'written'} `{artifact}` yet. Write the "
                 f"complete file now with your file-writing tool; the loop checks it. Then reply with one line "
                 f"saying the file is written.")
        if full:
            # A session that outgrew the context window fails on every resume, so a fresh
            # session starts from the brief and the file.
            nudge = (f"A previous session on this task ran out of context. `{artifact}` holds its last work, if any: "
                     f"read it, keep what is right, and finish the task. Keep tool output short (pipe long "
                     f"output short. " + nudge)
            say(f"  agent {spec.tool} ran out of context; a fresh session continues ({n + 1} of {NUDGES})")
        else:
            say(f"  agent {spec.tool} ended without writing {artifact.name}; nudged ({n + 1} of {NUDGES})")
        if spec.resume and turn.session and not full:
            turn = run_turn(spec, spec.resume, {**subs, "session": turn.session, "answer": nudge}, workdir=workdir)
        else:
            brief = subs["prompt"] + f"\n\n{nudge}"
            if prompt_file is not None:
                prompt_file.write_text(brief)
            turn = run_turn(spec, spec.argv, {**subs, "prompt": brief}, workdir=workdir)
            began = "fresh"                  # the conversation now continues in a new session
    turn.began = began
    return turn, asked


#: How many times a turn that ended without the file is resumed with a nudge (D618)
NUDGES = 2

_OVERFLOW = re.compile(r"exceeds the (available )?context|context (length|size|window) (exceeded|has been exceeded)|"
                       r"maximum context length|prompt is too long|too many tokens", re.I)


def overflowed(turn: "Turn") -> bool:
    """Whether the agent's session outgrew the model's context window (the server's words in its
    output or its error stream)."""
    return bool(_OVERFLOW.search("\n".join((turn.text or "", turn.stdout or "", turn.stderr or ""))))


def missing_agent(spec: Any) -> list[str]:
    """The agent's binary when it is not on PATH, for `tools_missing`."""
    try:
        head_argv = agent_spec(spec).argv
    except ValueError:
        return []
    head = head_argv[0].format(python=sys.executable, prompt="", prompt_file="", artifact="", workdir="", part="", name="")
    return [] if (shutil.which(head) or Path(head).is_file()) else [head]
