"""D735-D737, D791: a loop's own references -- its `library/` folder -- join its library without
a word in the document, digested whenever the library is on, and the sandbox mounts the
libraries a run reads."""

from __future__ import annotations

import types
from pathlib import Path

from flux_cli import sandbox
from flux_loop import load_task
from flux_loop.document import library_folders

DOC = ("statement: the fastest\nlanguage: python\nobjectives:\n  - {metric: t, direction: minimize}\n"
       "flow:\n  test: 'python3 check.py {artifact}'\n  measure:\n    b: {command: 'python3 b.py {artifact}', metrics: [t]}\n")


def test_the_loops_library_folder_is_its_library(tmp_path, monkeypatch):
    (tmp_path / "kp.problem.yaml").write_text(DOC)
    for f in ("library/papers", "inputs", "research", "out"):
        (tmp_path / f).mkdir(parents=True)
    (tmp_path / "library/papers/sqrt.md").write_text("# Fast inverse square root\n\nA Newton step after a magic-constant guess halves the error.\n")
    (tmp_path / "library/notes.txt").write_text("Booth recoding halves the partial products of a multiplier in hardware.\n")
    (tmp_path / "inputs/old.txt").write_text("Read by nobody: inputs/ is no library folder (D791).\n")
    (tmp_path / "research/a.pdf").write_bytes(b"%PDF-1.4 elsewhere")
    task = load_task(str(tmp_path / "kp.problem.yaml"))
    assert library_folders(task) == (str((tmp_path / "library").resolve()),), "library/ alone, not inputs/, research/ or out/"
    monkeypatch.setenv("FLUX_LIBRARY", str(tmp_path / "no-shared-library"))
    from flux_loop.task import PromptProblem

    mentor = PromptProblem(task).knowledge()
    assert mentor is not None, "an empty shared library: the loop's own library alone"
    papers = mentor.source("papers").render(None)
    assert "sqrt.md" in papers and "notes.txt" in papers and "old.txt" not in papers
    assert any("sqrt.md" in line for line in mentor.source("library").lookup("magic constant newton square root", k=2))


def test_the_sandbox_mounts_the_libraries_a_run_reads(tmp_path, monkeypatch):
    shared, home = tmp_path / "shared-lib", tmp_path / "loop"
    for d in (shared, home):
        d.mkdir()
    (home / "kp.problem.yaml").write_text(DOC)
    monkeypatch.setenv("FLUX_LIBRARY", str(shared))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    args = types.SimpleNamespace(file=str(home / "kp.problem.yaml"), db=None, out=None, json=None)
    ro, _ = sandbox.mounts_for(args, "task run")
    assert str(shared.resolve()) in ro
    assert Path(home).resolve().as_posix() in ro


def test_the_library_is_digested_once_by_the_model_its_own_papers_first(tmp_path, monkeypatch):
    """D753, D791: no `flux knowledge digest` to remember and no `digest:` to say -- the Setup
    digests the library with the run's model, once each, the loop's own papers first."""
    from types import SimpleNamespace

    from flux_loop import PromptProblem, TaskSpec

    monkeypatch.setenv("FLUX_LIBRARY", str(tmp_path / "shared"))
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/other.md").write_text("Another paper on caches, from the shared library, long enough to be a paper.\n")
    (tmp_path / "loop/library").mkdir(parents=True)
    (tmp_path / "loop/library/adders.md").write_text("Prefix adders: Kogge-Stone has log2(n) levels and fan-out 2. " * 20)
    task = TaskSpec.from_dict({"id": "x",
                               "statement": "an 8-bit adder",
                               "language": "verilog",
                               "objectives": [],
                               "flow": {"test": {"test": ["true"]}}}, base=tmp_path / "loop")

    class Model:
        def __init__(self):
            self.prompts = []

        def propose(self, prompt):
            self.prompts.append(prompt)
            return SimpleNamespace(text="Kogge-Stone prefix adder notes\nlog2(n) levels, fan-out 2\n- the construct it uses, with the widths and the latency it reports, for a designer to reuse as stated")

    model = Model()
    digest = next(s for s in PromptProblem(task).knowledge().sources if type(s).__name__ == "Digest")
    state = SimpleNamespace(request=SimpleNamespace(db=str(tmp_path / "r.db")), proposer=model, say=lambda _m: None)
    digest.make_now(state)                         # the Setup's (D782: a prompt only reads)
    text = digest.render(state)
    assert "log2(n) levels, fan-out 2" in text and "[adders.md]" in text
    assert len(model.prompts) == 2 and "adders.md" in model.prompts[0], "its own paper first, then the shared library's"
    digest.make_now(state)
    assert len(model.prompts) == 2, "once: the record keeps it"


def _adders(tmp_path, monkeypatch, knowledge=None, flow=None):
    from flux_loop import TaskSpec

    monkeypatch.setenv("FLUX_LIBRARY", str(tmp_path / "shared"))
    (tmp_path / "shared").mkdir(exist_ok=True)
    (tmp_path / "loop/library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "loop/library/adders.md").write_text("Prefix adders: Kogge-Stone has log2(n) levels and fan-out 2. " * 20)
    doc = {"id": "x",
           "statement": "an 8-bit adder",
           "language": "verilog",
           "objectives": [],
           "flow": {"test": {"test": ["true"]}}}
    if knowledge is not None:
        doc["flow"]["knowledge"] = knowledge
    if flow is not None:
        doc["flow"] = {**doc["flow"], **flow}
    return TaskSpec.from_dict(doc, base=tmp_path / "loop")


def test_the_papers_are_digested_in_the_setup(tmp_path, monkeypatch):
    """D771: the Setup's `knowledge: digest` digests what is new, said in the task pane; a prompt
    after it only reads what is stored."""
    from types import SimpleNamespace

    from flux_loop import PromptProblem

    asked = []
    model = SimpleNamespace(model="m1", propose=lambda p: asked.append(p) or SimpleNamespace(text="adders\nlog2(n) levels\n- the construct it uses, with the widths and the latency it reports, for a designer to reuse as stated"))
    problem = PromptProblem(_adders(tmp_path, monkeypatch))
    assert problem.digesting()
    state = SimpleNamespace(request=SimpleNamespace(db=str(tmp_path / "r.db")), proposer=model, say=lambda _m: None)
    got = problem.digest(state)
    assert got["digested"] == 1 and got["in all"] == 1 and got["new"] == "adders.md" and got["by"] == "m1"
    assert problem.digest(state)["digested"] == 0 and len(asked) == 1, "once"
    digest = next(s for s in problem.knowledge().sources if type(s).__name__ == "Digest")
    assert "log2(n) levels" in digest.render(state) and len(asked) == 1, "the prompt reads what the Setup made"


def test_an_agent_the_document_names_digests_the_papers(tmp_path, monkeypatch):
    """D771, D773: `flow: {knowledge: {by: …}}` -- one agent turn per paper, told where the file is."""
    import sys
    from types import SimpleNamespace

    import pytest

    from flux_loop import PromptProblem, TaskError
    from flux_loop.agent_check import agents_used

    fake = tmp_path / "agent.py"
    fake.write_text("import sys\nbrief = sys.stdin.read()\nassert 'adders.md' in brief and 'paper.txt' in brief\n"
                    "print('Prefix adders, read by the agent\\nKogge-Stone: log2(n) levels' + ' -- and the widths, the latency and the area it reports' * 3)\n")
    spec = {"command": [sys.executable, str(fake)], "output": "text", "timeout_s": 60}
    task = _adders(tmp_path, monkeypatch, flow={"knowledge": {"by": spec}})
    problem = PromptProblem(task)
    model = SimpleNamespace(model="m1", propose=lambda p: (_ for _ in ()).throw(AssertionError("not the model")))
    state = SimpleNamespace(request=SimpleNamespace(db=str(tmp_path / "r.db")), proposer=model, say=lambda _m: None)
    got = problem.digest(state)
    assert got["digested"] == 1, got
    digest = next(s for s in problem.knowledge().sources if type(s).__name__ == "Digest")
    assert "read by the agent" in digest.render(state)
    oc = _adders(tmp_path, monkeypatch, flow={"knowledge": {"by": "opencode"}})
    assert agents_used(oc) == ["opencode"], "its Test gates a start"
    assert oc.flow["knowledge"] == {"agent": "opencode"} and oc.digest_by == "opencode"
    assert type(oc).from_dict(oc.to_dict(), base=tmp_path / "loop").digest_by == "opencode", "written back as read"
    with pytest.raises(TaskError, match="flow.knowledge"):
        _adders(tmp_path, monkeypatch, flow={"knowledge": {"by": "someone"}})
    with pytest.raises(TaskError, match=r"flow.knowledge keys \['digest'\] are not known"):
        _adders(tmp_path, monkeypatch, {"digest": True})


def test_an_agent_digests_the_whole_library_not_only_the_loops_own(tmp_path, monkeypatch):
    """D774, D791: the shared library's papers are digested too -- a loop whose papers are all in
    the shared library still has a Digest in its Setup; `knowledge: off`, none."""
    import sys
    from types import SimpleNamespace

    from flux_loop import PromptProblem, TaskSpec

    monkeypatch.setenv("FLUX_LIBRARY", str(tmp_path / "shared"))
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/caches.md").write_text("Caches: a victim cache of 4 lines removes most conflict misses. " * 10)
    (tmp_path / "loop").mkdir()
    fake = tmp_path / "agent.py"
    fake.write_text("import sys\nb = sys.stdin.read()\nprint('digest of ' + ('caches.md' if 'caches.md' in b else 'other') + ': a victim cache of 4 lines removes most conflict misses' * 3)\n")
    spec = {"command": [sys.executable, str(fake)], "output": "text", "timeout_s": 60}
    doc = {"id": "x",
           "statement": "a cache",
           "language": "python",
           "objectives": [],
           "flow": {"test": {"test": ["true"]}}}
    assert PromptProblem(TaskSpec.from_dict(doc, base=tmp_path / "loop")).digesting(), "D791: unsaid, the library is digested"
    off = {**doc, "flow": {**doc["flow"], "knowledge": "off"}}
    assert not PromptProblem(TaskSpec.from_dict(off, base=tmp_path / "loop")).digesting()
    problem = PromptProblem(TaskSpec.from_dict({**doc, "flow": {**doc.get("flow", {}), "knowledge": {"by": spec}}}, base=tmp_path / "loop"))
    assert problem.digesting()
    state = SimpleNamespace(request=SimpleNamespace(db=str(tmp_path / "r.db")), proposer=None, say=lambda _m: None)
    got = problem.digest(state)
    assert got["digested"] >= 1 and "caches.md" in got["new"], got
