"""The library, digested (D576): once per document into the flux store, read by the
generator's prefix and the orchestrator's plan."""

from __future__ import annotations

from flux_knowledge import BM25Index, Chunk, Digest, digest_library, digests_in, index_lines
from flux_llm import Reply, ScriptedProposer


def _index():
    return BM25Index([
        Chunk(id="lib/pace#0", standard_id="library", source_path="mentor/knowledge/library/PACE.pdf", heading=None,
              text="PACE: piecewise polynomial approximation of exp with 16 segments reaches 1 ULP at FP16."),
        Chunk(id="lib/pace#1", standard_id="library", source_path="mentor/knowledge/library/PACE.pdf", heading=None,
              text="The table holds 64 entries of 13 bits; latency 3 cycles at 1 GHz on 7 nm."),
        Chunk(id="lib/fpnew#0", standard_id="library", source_path="mentor/knowledge/library/fpnew/fpnew_fma.sv", heading=None,
              text="module fpnew_fma; the classifier handles subnormals by a leading-zero count."),
        Chunk(id="corpus/x#0", standard_id="riscv", source_path="corpus/x.adoc", heading=None, text="not the library"),
    ])


class _Model:
    model = "scripted-digester"

    def __init__(self):
        self.prompts: list[str] = []

    def propose(self, prompt, **kw):
        self.prompts.append(prompt)
        name = prompt.split("DOCUMENT `", 1)[1].split("`", 1)[0]
        return Reply.of(f"{name}: a method\n- the number it states\n- a pitfall")


def test_a_document_is_digested_once_and_kept_in_the_store(tmp_path):
    db = str(tmp_path / "d.db")
    model = _Model()
    made = digest_library(db, model, index=_index())
    assert [d["source"].rsplit("/", 1)[-1] for d in made] == ["PACE.pdf", "fpnew_fma.sv"]
    assert made[0]["recipe"] and made[0]["model"] == "scripted-digester" and made[0]["digest"].startswith("PACE.pdf: a method")
    assert "16 segments reaches 1 ULP" in model.prompts[0] and "At most 900 characters" in model.prompts[0]
    assert digest_library(db, model, index=_index()) == [] and len(model.prompts) == 2          # nothing to do twice
    held = digests_in(db)
    assert set(p.rsplit("/", 1)[-1] for p in held) == {"PACE.pdf", "fpnew_fma.sv"}
    assert index_lines(db) == ["  [PACE.pdf] PACE.pdf: a method", "  [fpnew_fma.sv] fpnew_fma.sv: a method"]
    changed = [("mentor/knowledge/library/PACE.pdf", "a revised paper"), ("mentor/knowledge/library/fpnew/fpnew_fma.sv", "module fpnew_fma; the classifier handles subnormals by a leading-zero count.")]
    again = digest_library(db, model, documents=changed)
    assert [d["source"].rsplit("/", 1)[-1] for d in again] == ["PACE.pdf"], "a changed document is digested again, an unchanged one is not"


def test_the_source_makes_the_missing_digests_when_the_run_has_a_model_and_says_so_otherwise(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import flux_knowledge.digest as dg

    monkeypatch.setattr(dg, "library_documents", lambda index=None, standard_id="library": [("mentor/knowledge/library/PACE.pdf", "the paper's text")])
    db = str(tmp_path / "d.db")
    said = []
    no_model = SimpleNamespace(request=SimpleNamespace(db=db), proposer=None, say=said.append)
    assert Digest().render(no_model) == ""                                     # nothing stored, no model to make it
    with_model = SimpleNamespace(request=SimpleNamespace(db=db), proposer=_Model(), say=said.append)
    text = Digest().render(with_model)
    assert text.startswith("[PACE.pdf]\nPACE.pdf: a method") and any("1 library document(s) to digest" in m for m in said)
    assert Digest().render(no_model) == text, "the next run reads the store, model or not"
    assert Digest().render(SimpleNamespace(request=SimpleNamespace(db=""), proposer=None, say=said.append)) == ""


def test_a_document_asks_for_digests_and_the_planner_reads_the_index(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import flux_knowledge.digest as dg
    from flux_loop import LoopRequest, LoopState, PromptProblem, TaskSpec
    from flux_loop.task import describe_flow

    monkeypatch.setattr(dg, "library_documents", lambda index=None, standard_id="library": [("mentor/knowledge/library/PACE.pdf", "the paper's text")])
    db = str(tmp_path / "d.db")
    doc = {"id": "t", "statement": "x", "parts": ["a", "b"], "gate": {"test": ["true"]}, "flow": {"knowledge": ["digest", "mined"]}}
    task = TaskSpec.from_dict(doc)
    assert task.roles["knowledge"] == {"sources": {"names": ["mined", "digest"]}}
    prob = PromptProblem(task)
    assert [s.key for s in prob.knowledge().sources] == ["mined", "digest"]
    assert any(line.startswith("knowledge: mined, digest") or "digest" in line for line in describe_flow(task, prob) if line.startswith("knowledge"))
    state = LoopState(request=LoopRequest(db=db), say=lambda _m: None, proposer=_Model(), feedback=None)
    prefix = prob.prompt_prefix("a", state)
    assert "KEY POINTS FROM THE LIBRARY" in prefix and "PACE.pdf: a method" in prefix
    prompt, _schema = prob.plan_prompt(["a", "b"], state, None)
    assert "THE LIBRARY, digested" in prompt and "[PACE.pdf] PACE.pdf: a method" in prompt
    plain = PromptProblem(TaskSpec.from_dict({**doc, "flow": {}}))
    assert plain.library_index(state) == []


def test_the_cli_digests_and_shows(tmp_path, monkeypatch, capsys):
    import json

    import flux_knowledge.digest as dg
    from flux_cli.main import main

    monkeypatch.setattr(dg, "library_documents", lambda index=None, standard_id="library": [("mentor/knowledge/library/PACE.pdf", "the paper's text")])
    db = str(tmp_path / "d.db")
    replies = tmp_path / "r.json"
    replies.write_text(json.dumps(["PACE: a method\n- 1 ULP at 16 segments"]))
    assert main(["knowledge", "show", "--db", db]) == 1
    assert main(["knowledge", "digest", "--db", db, "--replies", str(replies)]) == 0
    out = capsys.readouterr().out
    assert "1 library document(s) to digest" in out and "1 digest(s) made" in out
    assert main(["knowledge", "show", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "== mentor/knowledge/library/PACE.pdf" in out and "PACE: a method" in out and "1 digest(s)" in out
