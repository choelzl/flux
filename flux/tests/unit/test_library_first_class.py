"""The library is first-class for every document (D648): excerpts for the statement, contract and
parts reach the prompts by default, `flow.knowledge: none` turns them off, a document may add its
own folder, a coding agent's brief carries a LIBRARY section with the files' paths, `task check`
says what the library holds, and a draft's row names the papers its prompt carried."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

DOC = {"id": "sq",
       "statement": "An integer square root unit using a non-restoring digit recurrence.",
       "contract": "The module isqrt takes a 16-bit radicand and returns an 8-bit root.",
       "parts": {"core": "the digit recurrence loop"},
       "flow": {"test": {"test": ["true"]}}}


@pytest.fixture
def lib(tmp_path, monkeypatch) -> Path:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "recurrence.md").write_text(
        "# Digit recurrence square root\n\nA non-restoring digit recurrence computes the integer square root "
        "one bit per step, with a remainder register and no multiplier.\n")
    (shared / "cordic.txt").write_text("CORDIC rotates vectors to compute sine and cosine with shifts and adds.\n")
    monkeypatch.setenv("FLUX_LIBRARY", str(shared))
    return shared


def _state(tmp_path):
    from flux_loop import LoopRequest, LoopState

    return LoopState(request=LoopRequest(db=str(tmp_path / "r.db")), say=lambda _m: None, proposer=None, feedback=None)


def test_a_plain_document_reads_the_library_by_default_and_not_with_knowledge_none(lib, tmp_path):
    from flux_loop import PromptProblem, TaskSpec

    prob = PromptProblem(TaskSpec.from_dict(DOC))
    assert [s.key for s in prob.knowledge().sources] == ["library", "papers", "digest"], "D791: digested unsaid"
    prefix = prob.prompt_prefix("core", _state(tmp_path))
    assert "[recurrence.md] A non-restoring digit recurrence" in prefix
    assert "[cordic.txt]" in prefix.split("the library's papers", 1)[1]     # the one-line index lists every paper
    off = PromptProblem(TaskSpec.from_dict({**DOC, "flow": {**DOC.get("flow", {}), "knowledge": "off"}}))
    assert off.knowledge() is None and "recurrence.md" not in off.prompt_prefix("core", _state(tmp_path))
    from flux_loop import TaskError

    with pytest.raises(TaskError, match="stands alone"):
        TaskSpec.from_dict({**DOC, "flow": {**DOC.get("flow", {}), "knowledge": {"off": True, "digest": "opencode"}}})


def test_an_empty_library_adds_nothing(tmp_path, monkeypatch):
    from flux_loop import PromptProblem, TaskSpec

    monkeypatch.setenv("FLUX_LIBRARY", str(tmp_path / "none"))
    assert PromptProblem(TaskSpec.from_dict(DOC)).knowledge() is None


def test_a_document_s_own_folder_is_indexed_with_the_shared_one(lib, tmp_path):
    from flux_loop import PromptProblem, TaskSpec

    home = tmp_path / "doc"
    (home / "library").mkdir(parents=True)
    (home / "library" / "remainder.md").write_text(
        "The remainder register of the digit recurrence square root holds nine bits for a 16-bit radicand.\n")
    task = TaskSpec.from_dict(DOC, base=home)
    prefix = PromptProblem(task).prompt_prefix("core", _state(tmp_path))
    assert "[remainder.md]" in prefix and "[recurrence.md]" in prefix
    from flux_loop import TaskError

    with pytest.raises(TaskError, match=r"flow.knowledge keys \['library'\] are not known"):    # D791: library/ alone
        TaskSpec.from_dict({**DOC, "flow": {**DOC.get("flow", {}), "knowledge": {"library": "papers"}}}, base=home)


def test_an_agent_brief_carries_the_library_section_with_paths(lib, tmp_path):
    from flux_loop import PromptProblem, TaskSpec
    from flux_loop.agent import agent_brief, library_section
    from flux_loop.task import library_queries

    task = TaskSpec.from_dict(DOC)
    prob = PromptProblem(task)
    section = library_section(prob, library_queries(task, task.parts), _state(tmp_path))
    brief = agent_brief(body="write it", prefix="", artifact=tmp_path / "a.sv", workdir=tmp_path, language="SystemVerilog",
                        part="core", prior=None, failure="", library=section)
    assert "LIBRARY (" in brief and f"  {lib / 'recurrence.md'}" in brief
    assert brief.index("LIBRARY (") < brief.index("HOW TO ANSWER")


def test_task_check_says_what_the_library_holds(lib, tmp_path, capsys, monkeypatch):
    from flux_cli.main import main

    doc = tmp_path / "sq" / "problem.yaml"
    doc.parent.mkdir()
    doc.write_text(yaml.safe_dump({k: v for k, v in DOC.items() if k != "id"}))
    main(["task", "check", str(doc)])
    assert "library: 2 documents (0 PDFs, pdftotext " in capsys.readouterr().out
    monkeypatch.setenv("FLUX_LIBRARY", str(tmp_path / "empty"))
    main(["task", "check", str(doc)])
    assert f"library: empty -- drop papers in library/ beside the document, or in {tmp_path / 'empty'}" in capsys.readouterr().out


def test_a_draft_s_row_names_the_papers_its_prompt_carried(lib, tmp_path):
    from flux_llm import ScriptedProposer
    from flux_loop import PromptProblem, TaskSpec, request_for, run_loop
    from flux_store import CampaignStore

    db = str(tmp_path / "r.db")
    out = run_loop(PromptProblem(TaskSpec.from_dict(DOC)), request_for(TaskSpec.from_dict(DOC), db=db),
                   proposer=ScriptedProposer([json.dumps({"artifact": "x", "why": "-"})] * 4), log=lambda _m: None)
    assert "recurrence.md" in out.provenance["library"]
    store = CampaignStore(db)
    cid = store.list_campaigns()[-1]["campaign_id"]
    cited = [((t.candidate or {}).get("meta") or {}).get("provenance", {}).get("library") for t in store.trials(cid)]
    assert any(c and "recurrence.md" in c for c in cited)
