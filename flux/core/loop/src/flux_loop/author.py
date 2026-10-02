"""The loop from a prompt (D586). An author -- the loop's model, or a coding agent (OpenCode,
Claude Code, Codex, any command) -- turns a prompt and its files into a problem document and
the files it names (golden model, generator, checker, tests); the loop checks and runs it and
hands the author the report; the author revises it for another pass, or says it is answered.

    flux ask "a signed 8x8 multiplier, smallest at 1 GHz on ASAP7" --file spec.pdf --author opencode

The author writes the problem, never the design. The document is checked before anything runs
(it loads, uses only document keys, every tool is on PATH, every named file exists); a refused
document goes back to the author with the reason, up to `checks` times. Input files are copied
under `<workdir>/library/` (D791: the loop's library) and read through the document's `flow: {knowledge: {files: [...]}}` (added
when the author forgot them).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

__all__ = ["DOCUMENT_KEYS", "Ask", "check_document", "drive", "parse_files", "reference", "workspace", "workspace_skills"]

HERE = Path(__file__).resolve().parent
#: flux/, from core/loop/src/flux_loop/author.py
FLUX_ROOT = HERE.parents[3]

from .document import DOCUMENT_KEYS  # noqa: E402 -- the loader's own list (D590)

#: The worked examples every author reads, live from the repository so they never drift.
EXAMPLES = (("mul8", ("problem.yaml", "golden.py")),
            ("adder16", ("problem.yaml", "golden.py", "gen.py")))

DOCUMENT = "problem.yaml"
DONE = "done.txt"


def document_path(workdir: Path) -> Path:
    """The authored document: `problem.yaml`, the one name a document has (D786)."""
    return workdir / DOCUMENT


def reference() -> str:
    """The guide to the document and the two worked examples (a document with a golden model;
    a design-space exploration over a generator script)."""
    out = [(HERE / "author_reference.md").read_text().strip(), "## Worked examples"]
    for app, names in EXAMPLES:
        for name in names:
            p = FLUX_ROOT / "applications" / app / name
            if p.is_file():
                out.append(f"`{app}/{name}`:\n```\n{p.read_text().strip()}\n```")
    return "\n\n".join(out)


def workspace_skills(paths: list[str | Path], workdir: Path) -> list[Any]:
    """The skills copied under `<workdir>/skills/` (D588): the document names that folder, so
    the problem stays whole wherever it is run from."""
    from .skills import load_skills

    skills = load_skills(list(paths))
    dest = workdir / "skills"
    for sk in skills:
        shutil.copytree(sk.path, dest / sk.name, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    return load_skills([dest]) if skills else []


def workspace(files: list[str | Path], workdir: Path) -> list[Path]:
    """The input files copied under `<workdir>/library/`, the loop's library (D791; a folder is
    copied whole); the paths the document will name, relative to the workdir."""
    inputs = workdir / "library"
    inputs.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for f in files:
        src = Path(f).expanduser()
        if not src.exists():
            raise FileNotFoundError(f"input {f} does not exist")
        dst = inputs / src.name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
            out.extend(sorted(p.relative_to(workdir) for p in dst.rglob("*") if p.is_file()))
        else:
            shutil.copy2(src, dst)
            out.append(dst.relative_to(workdir))
    return out


@dataclass
class Ask:
    """One prompt-driven run: the prompt, where it works, what it was given, who authors."""

    prompt: str
    workdir: Path
    inputs: list[Path] = field(default_factory=list)       # relative to workdir
    author: Any = "model"                                   # "model", or an agent spec (preset name or object)
    skills: list[Any] = field(default_factory=list)         # D588: loaded skills, copied under <workdir>/skills
    checks: int = 3                                         # document repairs per pass
    passes: int = 0                                         # D593: a cap on the loop's passes; 0 = until stopped
    excerpt_chars: int = 40000                              # what of the inputs a MODEL author reads inline
    no_run: bool = False                                    # write and check the document only


def _inputs_block(ask: Ask, *, inline: bool) -> str:
    if not ask.inputs:
        return "INPUT FILES: none."
    lines = ["INPUT FILES (in the working directory; name them in `flow: {knowledge: {files: [...]}}` so every design "
             "prompt reads them, and use them -- a reference model, tests, a spec -- in the gate where they fit):"]
    lines += [f"  - {p}" for p in ask.inputs]
    if inline:
        from .document import read_input

        budget = ask.excerpt_chars
        for p in ask.inputs:
            if budget <= 0:
                lines.append(f"(the rest is not shown inline: {budget} characters over)")
                break
            text = read_input(ask.workdir / p)
            lines.append(f"\n{p}:\n```\n{text[:budget]}\n```")
            budget -= len(text)
    return "\n".join(lines)


def brief(ask: Ask, *, inline: bool, error: str = "", report: str = "", current: str = "", note: str = "") -> str:
    """What the author reads: the role, the ask, the inputs, the reference, and -- on a
    repair -- why the document was refused, or -- between passes -- what the loop found."""
    parts = [
        "YOU WRITE THE PROBLEM, NOT THE DESIGN. A design loop will generate candidate designs, check each with "
        "the gate you define, measure the survivors with the stages you define and decide by the objectives you "
        f"define. Your job is the problem document -- a file named exactly `{DOCUMENT}` -- and every file it names "
        "(golden model, generator, checker, tests), written in the working directory.",
        f"THE ASK:\n{ask.prompt.strip()}",
        _inputs_block(ask, inline=inline),
        reference(),
    ]
    if ask.skills:
        from .skills import skill_index

        names = ", ".join(sk.name for sk in ask.skills)
        parts.append(f"SKILLS given with the ask ({names}), in `skills/`: the loop hands them to whoever writes the "
                     "design; name the folder in the document (`skills: [skills]`) and use them yourself where they "
                     "apply to writing the problem.\n" + skill_index(ask.skills, tools=not inline))
    if current:
        parts.append(f"THE CURRENT `{DOCUMENT}`:\n```yaml\n{current}\n```")
    if note:
        parts.append(f"THE PERSON WHO ASKED READ YOUR DOCUMENT and says:\n{note}\nRevise `{DOCUMENT}` (and its "
                     "files) accordingly; keep what they did not question.")
    elif error:
        parts.append(f"THE DOCUMENT WAS REFUSED, before anything ran:\n{error}\nFix it: rewrite `{DOCUMENT}` and "
                     "whatever file it names that is wrong or missing.")
    elif report:
        parts.append(
            "THE LOOP RAN YOUR DOCUMENT. Its report:\n" + report +
            f"\n\nIf this answers the ask, you are done: say so ({'`done: true`' if inline else f'write the word DONE to `{DONE}`'}). "
            f"Otherwise revise `{DOCUMENT}` (and its files) so the next pass comes closer: a gate that let a wrong design "
            "through, a goal that was not the ask's, a stage that measured the wrong thing, a search that stopped too early.")
    return "\n\n".join(parts) + "\n"


#: How a model author replies: each file as a `FILE <name>` line and a fenced block. Not a JSON
#: schema: files escaped inside JSON strings under constrained decoding came back empty (D586).
REPLY_SHAPE = (
    "REPLY SHAPE: every file you write, whole, as a line `FILE <path>` followed by a fenced block:\n"
    "FILE problem.yaml\n```yaml\n...\n```\nFILE golden.py\n```python\n...\n```\n"
    "Then one line `WHY: <one line>`. When the ask is answered and nothing needs to change, reply with the "
    "single line `DONE` and a `WHY:` line instead.")

_FILE = re.compile(r"^FILE[ \t]+`?([^\s`]+)`?[ \t]*\n```[^\n]*\n(.*?)\n```", re.M | re.S)


def parse_files(text: str) -> tuple[dict[str, str], bool, str]:
    """(files, done, why) from a model author's reply: `FILE <path>` blocks (a JSON
    `{"files": {...}}` is read too), a `DONE` line, a `WHY:` line."""
    files = {m.group(1): m.group(2) for m in _FILE.finditer(text or "")}
    done = bool(re.search(r"^\s*DONE\s*$", text or "", re.M))
    why = (re.search(r"^WHY:\s*(.*)$", text or "", re.M) or [None, ""])[1]
    if not files and not done:
        from .model import _json

        doc = _json(text or "")
        if isinstance(doc, dict):
            files = {str(k): str(v) for k, v in (doc.get("files") or {}).items()}
            done, why = bool(doc.get("done")), str(doc.get("why") or why)
    return files, done, why


def _write_files(workdir: Path, files: dict[str, str]) -> list[str]:
    """The model author's files, written inside the working directory only."""
    written = []
    root = workdir.resolve()
    for name, text in (files or {}).items():
        dst = (workdir / name).resolve()
        if root not in dst.parents and dst != root:
            raise ValueError(f"{name}: outside the working directory")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text if text.endswith("\n") else text + "\n")
        written.append(name)
    return written


def _model_turn(ask: Ask, proposer: Any, text: str) -> tuple[bool, str]:
    """(done, what happened): the model writes the files as `FILE` blocks."""
    reply = proposer.propose(text + "\n" + REPLY_SHAPE)
    files, done, why = parse_files(getattr(reply, "text", str(reply)))
    written = _write_files(ask.workdir, files)
    return done and not written, f"wrote {', '.join(written) or 'nothing'}" + (f" -- {why[:200]}" if why else "")


def _agent_turn(ask: Ask, text: str, say: Callable[[str], None]) -> tuple[bool, str]:
    from .agent import DECIDE, agent_spec, converse

    spec = agent_spec(ask.author)
    prompt_file = ask.workdir / "AUTHOR-BRIEF.md"
    prompt_file.write_text(text)
    target = ask.workdir / DOCUMENT
    done = ask.workdir / DONE
    done.unlink(missing_ok=True)
    found = document_path(ask.workdir)
    before = found.read_text() if found.is_file() else None
    subs = {"prompt": text, "prompt_file": str(prompt_file), "artifact": str(target), "workdir": str(ask.workdir),
            "part": "problem", "name": "problem", "python": __import__("sys").executable, "home": str(ask.workdir)}
    if ask.skills:
        from .skills import install

        install(ask.skills, ask.workdir)
    turn, _asked = converse(spec, subs, workdir=ask.workdir, artifact=target, prompt_file=prompt_file,
                            answer=lambda _q: (DECIDE, "decide"), say=say)
    if not turn.ok:
        return False, f"the author {spec.tool} exited {turn.rc}: {(turn.stderr or turn.text)[-300:]}"
    found = document_path(ask.workdir)
    now = found.read_text() if found.is_file() else None
    if done.is_file() and "DONE" in done.read_text().upper():
        return True, "said the ask is answered"
    return False, "wrote " + (found.name if now != before else "nothing new")


def _home_files(doc: dict[str, Any]) -> list[str]:
    """Every `{home}/<file>` a command of the document names."""
    tokens: list[str] = []

    def walk(x: Any) -> None:
        if isinstance(x, str):
            tokens.extend(re.findall(r"\{home\}/([^\s\"']+)", x))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk({k: doc.get(k) for k in ("flow", "generator")})          # D775: the gate and the stages are in flow
    return sorted(set(tokens))


def _golden_fault(task: Any, workdir: Path) -> str:
    """Why the golden model a `--golden` gate names cannot judge anything, or "" (D589): it
    must import, declare its ports, make its vectors (`golden()` called on every one) and
    answer every output port each time."""
    import importlib.util
    import traceback

    from .golden_proto import golden_check

    cmd = golden_check(task)
    if "--golden" not in cmd or cmd.index("--golden") + 1 >= len(cmd):
        return ""
    raw = cmd[cmd.index("--golden") + 1].replace("{home}", str(workdir))
    path = Path(raw)
    try:
        from flux_codegen_rtl_harness import Golden, golden_vectors

        spec = importlib.util.spec_from_file_location("authored_golden", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        g = Golden.from_module(mod)
        rows = golden_vectors(g)
    except Exception as exc:  # noqa: BLE001 -- the golden's own fault, said to its author
        tail = "".join(traceback.format_exception(exc)[-3:]).strip()
        return (f"the golden model {path.name} cannot run: {type(exc).__name__}: {exc}\n{tail[-800:]}\n"
                "It must declare PORTS and `def golden(**inputs) -> {output_name: value}` (a dict), and VECTORS, "
                "if given, as a list of dicts of inputs; compute every expected value in golden(), never by hand.")
    outs = {p["name"] for p in g.ports if p["dir"] == "out"}
    for r in rows:
        if set(r["expected"]) != outs:
            return (f"the golden model {path.name} answers {sorted(r['expected'])} for {r['inputs']}, not the output "
                    f"ports {sorted(outs)}: golden() returns one value per output port, by name")
    # an unsigned output whose top bit no vector sets, corners included, disagrees with its
    # declared width: live, `s` declared 17 bits was returned as `total & 0xFFFF` (D622)
    for p in g.ports:
        bits = int(p["bits"])
        if p["dir"] != "out" or not p.get("unsigned") or bits < 2 or g.ulp.get(p["name"]) is not None:
            continue
        top = max((int(r["expected"][p["name"]]) for r in rows), default=0)
        if top < (1 << (bits - 1)):
            return (f"the golden model {path.name} never sets the top bit of output `{p['name']}` ({bits} bits) over "
                    f"{len(rows)} vectors, corner cases included: its width or golden() is wrong. Make the "
                    f"declared width match what golden() returns (and the contract say the same).")
    return ""


def check_document(workdir: Path, inputs: list[Path] = (), skills: bool = False) -> tuple[Any, Any, str]:
    """(task, problem, "") when the authored document can run, else (None, None, why): it
    loads, says only document keys, names only files that exist, needs only tools on PATH.
    Input files the document forgot are added to its `knowledge.files`."""
    import yaml

    from .document import TaskError, task_in
    from .task import PromptProblem

    path = document_path(workdir)
    if not path.is_file():
        return None, None, f"there is no `{DOCUMENT}` in {workdir}"
    try:
        doc = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        return None, None, f"`{DOCUMENT}` is not YAML: {exc}"
    if not isinstance(doc, dict):
        return None, None, f"`{DOCUMENT}` is not a mapping of keys"
    unknown = sorted(set(doc) - DOCUMENT_KEYS)
    if unknown:
        return None, None, f"keys a document does not have: {', '.join(unknown)}; the keys are {', '.join(sorted(DOCUMENT_KEYS))}"
    missing = [f for f in _home_files(doc) if not (workdir / f).is_file()]
    if missing:
        return None, None, f"the commands name {', '.join(missing)} beside the document, and it is not there: write it"
    flow = doc.get("flow") if isinstance(doc.get("flow"), dict) else {}
    know = flow.get("knowledge") if isinstance(flow.get("knowledge"), dict) else {}
    named = set(know.get("files") or [])
    forgot = [str(p) for p in inputs if str(p) not in named]
    changed = False
    if forgot and flow.get("knowledge") != "off":     # D775: what is read is said in flow.knowledge
        doc["flow"] = {**flow, "knowledge": {**know, "files": sorted(named | set(forgot))}}
        changed = True
    if skills and not doc.get("skills"):              # D588: the ask's skills go with the problem
        doc["skills"] = ["skills"]
        changed = True
    if changed:
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
    try:
        task = task_in(doc, workdir)
        problem = PromptProblem(task)
    except (TaskError, ValueError) as exc:
        return None, None, f"the document does not load: {exc}"
    why = _golden_fault(task, workdir)
    if why:
        return None, None, why
    tools = problem.tools_missing()
    if tools:
        return None, None, f"tools not on PATH: {', '.join(tools)} (use the tools this machine has: see the reference)"
    return task, problem, ""


def _phase(name: str, why: str):
    """A task row in the TUI when one is attached (flux_profile's listener); nothing otherwise."""
    try:
        from flux_profile import phase

        return phase(name, why=why)
    except Exception:  # noqa: BLE001 -- the author runs fine uninstrumented
        import contextlib

        return contextlib.nullcontext()


def drive(ask: Ask, *, run_pass: Callable[..., Any], proposer: Any = None,
          say: Callable[[str], None] = print,
          review: Callable[[Path, Any, Any], str | None] | None = None,
          loop_proposer: Any = None) -> dict[str, Any]:
    """Author, check, run, report, revise -- pass after pass until stopped (D593), or
    `ask.passes` when it caps them. Returns the document's path, the last task, problem and
    loop result, the report lines, and the authoring history.

    `run_pass(task, problem, explore)` runs one pass; `explore` counts consecutive passes that
    ended at rest. The author's DONE settles the document (it is asked again only once the loop
    is at rest) but never ends the run. `review(path, task, problem)` (D587), when given, sees
    every checked document before it runs: None runs it; a note sends it back to the author."""
    import inspect

    from .passes import between_passes, carrying, mark
    from .task import task_report_lines

    takes_explore = len(inspect.signature(run_pass).parameters) >= 3

    ask.workdir.mkdir(parents=True, exist_ok=True)
    inline = ask.author in (None, "model")
    if inline and proposer is None:
        raise ValueError("the model authors the document, and there is no model: give one, or an agent author")
    history: list[dict[str, Any]] = []
    task = problem = out = None
    report: list[str] = []

    def turn(text: str) -> tuple[bool, str]:
        with _phase("author: " + ("the model" if inline else str(getattr(ask.author, "get", lambda *_: None)("preset") or ask.author)),
                    "writes the problem document and its files"):
            return _model_turn(ask, proposer, text) if inline else _agent_turn(ask, text, say)

    n = rests = 0
    settled = False
    run_mark = object()             # D738: the passes share one search
    while True:
        current = document_path(ask.workdir).read_text() if document_path(ask.workdir).is_file() else ""
        if n and (not settled or rests):
            done, what = turn(brief(ask, inline=inline, report="\n".join(report), current=current))
            history.append({"pass": n + 1, "turn": "revise", "what": what})
            say(f"author (revise): {what}")
            now = document_path(ask.workdir).read_text() if document_path(ask.workdir).is_file() else ""
            settled = bool(done)
            if done or now == current:
                say("author: the document stands; the loop goes on improving the design"
                    + (" (exploring: it came to rest)" if rests else ""))
        error = ""
        for attempt in range(ask.checks + 1):
            if n == 0 and attempt == 0:
                done, what = turn(brief(ask, inline=inline))
                history.append({"pass": 1, "turn": "write", "what": what})
                say(f"author: {what}")
            elif error:
                current = document_path(ask.workdir).read_text() if document_path(ask.workdir).is_file() else ""
                done, what = turn(brief(ask, inline=inline, error=error, current=current))
                history.append({"pass": n + 1, "turn": "repair", "what": what, "refused": error})
                say(f"author (repair): {what}")
            task, problem, error = check_document(ask.workdir, ask.inputs, skills=bool(ask.skills))
            if not error:
                break
            say(f"document refused: {error}")
        while not error and review is not None:
            note = review(document_path(ask.workdir), task, problem)
            if not note:
                break
            current = document_path(ask.workdir).read_text()
            _done, what = turn(brief(ask, inline=inline, note=note, current=current))
            history.append({"pass": n + 1, "turn": "review", "note": note, "what": what})
            say(f"author (your note): {what}")
            for _attempt in range(ask.checks + 1):
                task, problem, error = check_document(ask.workdir, ask.inputs, skills=bool(ask.skills))
                if not error:
                    break
                say(f"document refused: {error}")
                _done, what = turn(brief(ask, inline=inline, error=error, current=document_path(ask.workdir).read_text()
                                         if document_path(ask.workdir).is_file() else ""))
                history.append({"pass": n + 1, "turn": "repair", "what": what, "refused": error})
        if error:
            return {"document": str(document_path(ask.workdir)), "error": error, "history": history, "result": out,
                    "task": task, "problem": problem, "report": report}
        digest = hashlib.sha256(document_path(ask.workdir).read_bytes()).hexdigest()[:12]
        if not ask.no_run:
            say(f"pass {n + 1}: running {task.id} (document {digest})")
        mark("pass", n=n + 1, explore=rests)
        with carrying(run_mark):
            out = run_pass(task, problem, rests) if takes_explore else run_pass(task, problem)
        report = task_report_lines(task, out, problem)
        history.append({"pass": n + 1, "turn": "run", "document": digest,
                        "decision": out.decision.candidate.name if out.decision is not None else None})
        (ask.workdir / "authoring.json").write_text(json.dumps(history, indent=2) + "\n")
        n += 1
        go, rests, _fb = between_passes(out, n, passes=ask.passes, rests=rests, proposer=(proposer, loop_proposer), say=say)
        if not go:
            break
    (ask.workdir / "authoring.json").write_text(json.dumps(history, indent=2) + "\n")
    return {"document": str(document_path(ask.workdir)), "error": "", "history": history, "result": out,
            "task": task, "problem": problem, "report": report}


def write_golden(task: Any, proposer: Any, say: Callable[[str], None] = print, attempts: int = 3) -> str:
    """Write the golden model a document's gate names when it does not exist (D604): from the
    statement and contract, checked like an author's (`_golden_fault`) and repaired from what
    the check said. "" when the file is there and runs; else why not. The run records that the
    golden was model-written, since such a golden can be wrong in ways no check sees."""
    from .golden_proto import golden_path

    path = golden_path(task)
    if path is None or path.is_file():
        return ""
    if proposer is None:
        return f"{path.name} does not exist and there is no model to write it"
    guide = (HERE / "author_reference.md").read_text()
    ask = (f"Write the GOLDEN MODEL `{path.name}` for this problem -- the Python reference the gate tests every "
           f"design against. It is the specification: what the design must compute, never how.\n\n"
           f"THE PROBLEM: {task.statement}\n\nTHE CONTRACT: {task.contract or '(none)'}\n\n"
           f"The rules for golden models are in this guide (PORTS, golden(**inputs), COUNT, VECTORS, "
           f"TOLERANCE_ULP, CLOCK/LATENCY, float bit patterns):\n\n{guide}\n\n"
           f"Reply with the file as one block:\nFILE {path.name}\n```python\n...\n```\nWHY: <one line>")
    error = ""
    for n in range(attempts):
        prompt = ask + (f"\n\nYOUR LAST {path.name} WAS REFUSED: {error}\nFix it and send the whole file again." if error else "")
        reply = proposer.propose(prompt)
        files, _done, why = parse_files(getattr(reply, "text", str(reply)))
        body = files.get(path.name) or next(iter(files.values()), None)
        if not body:
            error = f"the reply carried no FILE {path.name} block"
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.rstrip() + "\n")
        error = _golden_fault(task, Path(task.home or "."))
        if not error:
            say(f"{path.name} written by the model (attempt {n + 1}){': ' + why if why else ''} -- the gate "
                f"measures every design against it; read it before trusting a decision")
            return ""
        say(f"{path.name} (attempt {n + 1}) refused: {error.splitlines()[0][:160]}")
    if path.is_file():
        path.rename(path.with_suffix(".refused.py"))     # never leave a golden that cannot judge
    return f"the model could not write a golden model that runs in {attempts} attempt(s): {error[:300]}"
