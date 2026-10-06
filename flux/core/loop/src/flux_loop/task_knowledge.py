"""`PromptProblem`'s knowledge (D462, D648, D771): the knowledge role's mentor with the library in
front, the papers digested in Setup (by the model or a coding agent), and the sections and text it
adds to a prompt; `library_queries`, the lookups a statement makes. A mixin of
`flux_loop.task.PromptProblem` (D891).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .types import LoopState
from .document import TaskSpec


#: Words a lookup does without: grammar, and the interface boilerplate every module shares
#: (a query of port names finds port lists, not methods).
_STOP = frozenset("a an and are as at be by each every for from in into is it its of on one or that the "
                  "this to with which when must should may not no only then than possible exactly named "
                  "module input output logic wire reg port ports bit bits clock reset signed unsigned "
                  "systemverilog verilog".split())


def library_queries(task: TaskSpec, parts: Any = (), n: int = 8, words: int = 12) -> list[str]:
    """A few short lookups for the library (D648): the statement's first two sentences, the
    contract's first, each part's statement -- their content words, `words` at most each."""
    def sentences(text: str, k: int) -> list[str]:
        out = []
        for sent in re.split(r"(?<=[.!?])\s+|\n\s*\n", text or ""):
            w = list(dict.fromkeys(x for x in re.findall(r"[A-Za-z][A-Za-z0-9_+\-]*[A-Za-z0-9]", sent)
                                   if x.lower() not in _STOP))
            if len(w) >= 3:
                out.append(" ".join(w[:words]))
            if len(out) >= k:
                break
        return out

    got = sentences(task.statement, 2) + sentences(task.contract, 1)
    for p in parts or ():
        got += sentences(f"{p.name.replace('_', ' ')}: {p.statement}", 1)
    return list(dict.fromkeys(got))[:n]


def _agent_digest(spec: Any):
    """D771: `ask(path, prompt, text) -> (digest, by)` -- one coding agent turn per paper, in a
    scratch directory of its own. D785: the paper's text is a file there (`paper.txt`) the agent
    reads with its tools, in pieces as it needs, not the brief itself -- a paper's 30,000 tokens
    overflowed a model's context; the brief is the instructions and where the original is."""
    import shutil
    import tempfile

    from .agent import agent_spec, run_turn

    agent = agent_spec(spec)

    def ask(path: str, prompt: str, text: str = "") -> tuple[str, str]:
        work = Path(tempfile.mkdtemp(prefix="flux-digest-"))
        try:
            how = prompt.split("\nDOCUMENT `", 1)[0].strip().replace("the document below", "the document")   # no text
            if text:
                (work / "paper.txt").write_text(text)
            brief = ((f"The document is `paper.txt` in your working directory: the text of {path}. Read it with "
                      "your tools, in parts if it is long; open the original file for a table or a figure the text garbles. "
                      if text else f"The document is the file {path}: read it with your tools. ")
                     + "Do not write any file and call no tool to answer: reply with the key points as plain text.\n\n" + how)
            (work / "BRIEF.md").write_text(brief)
            subs = {"prompt": brief, "prompt_file": str(work / "BRIEF.md"), "artifact": str(work / "digest.md"),
                    "workdir": str(work), "part": "digest", "name": f"digest {Path(path).name}", "home": str(work)}
            turn = run_turn(agent, agent.argv, subs, workdir=work)
            if not turn.ok:                       # D782: an agent that says why on stdout (OpenCode) is heard too
                said = " ".join((turn.stderr or "").split())[-300:] or " ".join((turn.stdout or "").split())[-300:]
                raise RuntimeError(f"{agent.tool} exited {turn.rc}: {said or 'nothing said'}")
            return turn.text, f"{agent.tool}" + (f" ({turn.about})" if turn.about else "")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    return ask


class KnowledgeMixin:
    """The library, the digests and the knowledge role's text, for `PromptProblem` (D891)."""

    def knowledge(self) -> Any | None:
        """The knowledge role's mentor with the library in front (D648): excerpts retrieved for
        the statement, contract and parts, and one line per paper -- for every document, unless
        `flow.knowledge` is `none` or the library is empty. Made once, so each source is read once."""
        if "_mentor" not in self.__dict__:
            from .document import library_on

            role = self.roles().knowledge
            self._mentor = role
            from .document import library_folders

            folders = library_folders(self.task)          # D735: the loop's own papers too
            if library_on(self.task) and (role is None or hasattr(role, "sources")):
                from flux_knowledge import Library, Mentor, Papers
                from flux_knowledge.library import library_files

                if library_files(folders):
                    lib = [Library(lambda _s: library_queries(self.task, self.parts), folders=folders),
                           Papers(folders=folders)]
                    from .document import own_library

                    own = own_library(self.task)
                    from flux_knowledge import Digest

                    from .task import model_use           # D891: task.py imports this module

                    rest = [x for x in getattr(role, "sources", ()) if type(x).__name__ != "Digest"]
                    # D791: the whole library -- the shared papers and the loop's own, its own first --
                    # digested whenever the library is on, by the model or `flow.knowledge.agent`;
                    # D793: only for a loop that reads them -- a sweep with no model has no prompt
                    if model_use(self.task) or self.task.digest_by is not None:
                        lib.append(Digest(folders=folders, whole=True, own=own))
                    self._mentor = (Mentor(lib) if role is None else
                                    Mentor([*lib, *rest], budget=role.budget, share=role.share))
        return self._mentor

    def digesting(self) -> bool:
        """Whether this loop digests papers at all (D771): its own, or the library's by `flow.knowledge`."""
        return any(type(x).__name__ == "Digest" for x in getattr(self.knowledge(), "sources", ()))

    def digest(self, state: LoopState) -> dict[str, Any]:
        """D771: the papers not digested yet, digested in the run's Setup -- by its model, or by
        the coding agent `knowledge: {digest: {agent: …}}` names, which reads each file itself.
        What was done, for the task pane; {} when the loop digests nothing."""
        mentor = self.knowledge()
        sources = [x for x in getattr(mentor, "sources", ()) if type(x).__name__ == "Digest"]
        if not sources:
            return {}
        ask = _agent_digest(self.task.digest_by) if self.task.digest_by is not None else None
        out: dict[str, Any] = {}
        for src in sources:
            if ask is not None:
                src.ask = ask
            for k, v in src.make_now(state).items():
                out[k] = (out[k] + v) if isinstance(v, int) and isinstance(out.get(k), int) else v
        return out

    def mentor_sections(self, state: LoopState) -> list[tuple[str, str]]:
        out = [("task", self.task.statement + ("\n\n" + self.task.contract if self.task.contract else ""))]
        if self.task.knowledge:
            out.append(("knowledge", self.task.knowledge))
        mentor = self.knowledge()
        if mentor is not None:                  # the knowledge role's sections (D462)
            out.extend(mentor.sections(state))
        return out

    def _role_knowledge(self, state: LoopState, focus: str | None = None) -> str:
        """The knowledge role's text for a prompt (D462), beside the document's `knowledge`;
        `focus` (the part in hand) decides what is kept when the window is short (D550).
        Help, never a gate: an unreadable source contributes nothing."""
        mentor = self.knowledge()
        if mentor is None:
            return ""
        try:
            return mentor.prefix(state, focus=focus).strip()
        except Exception:  # noqa: BLE001
            return ""
