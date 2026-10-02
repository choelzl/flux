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
        return Reply.of(f"{name}: a method\n- the number it states\n- a pitfall\n- the construct it uses, with the widths and the latency it reports, for a designer to reuse as stated")


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


def test_the_setup_makes_the_missing_digests_when_the_run_has_a_model_and_says_so_otherwise(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import flux_knowledge.digest as dg

    monkeypatch.setattr(dg, "library_documents", lambda index=None, standard_id="library": [("mentor/knowledge/library/PACE.pdf", "the paper's text")])
    db = str(tmp_path / "d.db")
    said = []
    no_model = SimpleNamespace(request=SimpleNamespace(db=db), proposer=None, say=said.append)
    assert Digest().render(no_model) == ""                                     # nothing stored, no model to make it
    with_model = SimpleNamespace(request=SimpleNamespace(db=db), proposer=_Model(), say=said.append)
    Digest().make_now(with_model)                  # the Setup's (D782): a prompt only reads
    text = Digest().render(with_model)
    assert text.startswith("[PACE.pdf]\nPACE.pdf: a method") and any("1 library document(s) to digest" in m for m in said)
    assert Digest().render(no_model) == text, "the next run reads the store, model or not"
    assert Digest().render(SimpleNamespace(request=SimpleNamespace(db=""), proposer=None, say=said.append)) == ""


def test_a_document_asks_for_digests_and_the_planner_reads_the_index(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import flux_knowledge.digest as dg
    from flux_loop import LoopRequest, LoopState, PromptProblem, TaskSpec
    from flux_loop.document import describe_flow

    monkeypatch.setattr(dg, "library_documents", lambda index=None, standard_id="library": [("mentor/knowledge/library/PACE.pdf", "the paper's text")])
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "PACE.md").write_text("PACE: piecewise approximation of exp, 16 segments, 1 ULP at FP16.\n")
    monkeypatch.setenv("FLUX_LIBRARY", str(tmp_path / "shared"))
    db = str(tmp_path / "d.db")
    doc = {"id": "t",
           "statement": "x",
           "parts": ["a", "b"],
           "flow": {"knowledge": {"lessons": "mined"}, "test": {"test": ["true"]}}}   # D791: the digest unsaid
    task = TaskSpec.from_dict(doc)
    assert task.roles["knowledge"] == "mined"
    prob = PromptProblem(task)
    assert [s.key for s in prob.knowledge().sources] == ["library", "papers", "digest", "mined"]
    assert any("its papers digested" in line for line in describe_flow(task, prob) if line.startswith("knowledge"))
    state = LoopState(request=LoopRequest(db=db), say=lambda _m: None, proposer=_Model(), feedback=None)
    prob.digest(state)                             # the Setup's (D782: a prompt only reads)
    prefix = prob.prompt_prefix("a", state)
    assert "KEY POINTS FROM THE LIBRARY" in prefix and "PACE.pdf: a method" in prefix
    prompt, _schema = prob.plan_prompt(["a", "b"], state, None)
    assert "THE LIBRARY, one line per paper" in prompt and "[PACE.md]" in prompt, "the papers source lists the library"
    off = PromptProblem(TaskSpec.from_dict({**doc, "flow": {**doc["flow"], "knowledge": "off"}}))
    assert off.library_index(state) == []


def test_the_cli_digests_and_shows(tmp_path, monkeypatch, capsys):
    import json

    import flux_knowledge.digest as dg
    from flux_cli.main import main

    monkeypatch.setattr(dg, "library_documents", lambda index=None, standard_id="library": [("mentor/knowledge/library/PACE.pdf", "the paper's text")])
    db = str(tmp_path / "d.db")
    replies = tmp_path / "r.json"
    replies.write_text(json.dumps(["PACE: a method\n- 1 ULP at 16 segments\n- the table size, the widths and the latency it reports, for a designer to reuse"]))
    assert main(["knowledge", "show", "--db", db]) == 1
    assert main(["knowledge", "digest", "--db", db, "--replies", str(replies)]) == 0
    out = capsys.readouterr().out
    assert "1 library document(s) to digest" in out and "1 digest(s) made" in out
    assert main(["knowledge", "show", "--db", db]) == 0
    out = capsys.readouterr().out
    assert "== mentor/knowledge/library/PACE.pdf" in out and "PACE: a method" in out and "1 digest(s)" in out


def test_a_setup_digests_a_few_its_own_papers_first_and_stops_when_the_digester_fails(tmp_path, monkeypatch):
    """D782: a library of hundreds of files is digested a few a pass -- the loop's own papers
    first, papers before sources -- and a digester that keeps failing ends the pass's share,
    saying why, instead of failing on every file."""
    docs = [(f"mentor/knowledge/library/src/f{i}.cpp", f"code {i}") for i in range(5)] + \
           [("mentor/knowledge/library/z.pdf", "a shared paper"), ("/loop/library/mine.md", "the loop's own")]
    db = str(tmp_path / "d.db")
    said: list[str] = []
    made = digest_library(db, _Model(), documents=docs, limit=3, first=["/loop/library"], say=said.append)
    assert [d["source"].rsplit("/", 1)[-1] for d in made] == ["mine.md", "z.pdf", "f0.cpp"]
    assert any("3 now, 4 in the passes after" in m for m in said), said
    nxt = digest_library(db, _Model(), documents=docs, limit=3)
    assert [d["source"].rsplit("/", 1)[-1] for d in nxt] == ["f1.cpp", "f2.cpp", "f3.cpp"], "the rest, a few a pass"

    def broken(path, prompt, text=""):
        raise RuntimeError("opencode exited 1: An authentication key is required")

    said.clear()
    monkeypatch.setenv("FLUX_DIGESTS", str(tmp_path / "nothing-kept"))      # D794: none kept from above
    assert digest_library(str(tmp_path / "e.db"), None, documents=docs, ask=broken, say=said.append) == []
    assert sum("not digested" in m for m in said) == 3, "three tries, not one a file"
    assert any("3 failures in a row" in m and "authentication key" in m for m in said), said


def test_an_answer_that_is_a_tool_call_or_a_fragment_is_no_digest(tmp_path):
    """D785: Qwen coder through OpenCode answered with a tool call written as text -- its message
    is the digest; a few characters are not one."""
    import json

    from flux_knowledge.digest import unwrapped

    long = "PACE: piecewise-affine approximation; 16 segments reach 1 ULP; a 5-bit index, a 12-bit slope, 2 cycles"
    assert unwrapped(json.dumps({"arguments": {"message": long}})) == long
    assert unwrapped(json.dumps({"message": long})) == long and unwrapped(long) == long
    assert unwrapped('{ "arguments": {') == '{ "arguments": {', "a fragment stays what it is"
    answers = iter([json.dumps({"arguments": {"message": long}}), '{ "arguments": {'])
    said: list[str] = []
    made = digest_library(str(tmp_path / "d.db"), None, documents=[("a/PACE.pdf", "x"), ("a/other.pdf", "y")],
                          ask=lambda p, prompt, text="": (next(answers), "opencode"), say=said.append)
    assert [d["digest"] for d in made] == [long], "the message kept; the fragment not"
    assert any("the answer is 16 characters" in m for m in said), said


def test_a_digest_is_kept_for_the_next_loop_and_never_asked_twice(tmp_path, monkeypatch):
    """D794: a document digested once -- by any loop or run of this home -- is taken from the
    kept digests by the next, keyed by its content; a changed document is asked again."""
    monkeypatch.setenv("FLUX_DIGESTS", str(tmp_path / "kept"))
    docs = [("a/PACE.pdf", "PACE: piecewise approximation, 16 segments, 1 ULP"), ("b/other.pdf", "another paper")]
    first = _Model()
    made = digest_library(str(tmp_path / "one.db"), first, documents=docs)
    assert len(first.prompts) == 2 and not any(d.get("reused") for d in made)
    assert len(list((tmp_path / "kept").glob("*.json"))) == 2
    said: list[str] = []
    second = _Model()
    again = digest_library(str(tmp_path / "two.db"), second, documents=[("elsewhere/PACE.pdf", docs[0][1])] + docs[1:], say=said.append)
    assert second.prompts == [] and all(d["reused"] for d in again), "another loop's record: no call"
    assert set(digests_in(str(tmp_path / "two.db"))) == {"elsewhere/PACE.pdf", "b/other.pdf"}
    assert any("2 document(s) taken from the digests kept before" in m for m in said), said
    changed = digest_library(str(tmp_path / "two.db"), second, documents=[("b/other.pdf", "another paper, revised")])
    assert len(second.prompts) == 1 and not changed[0].get("reused"), "new content is digested"

