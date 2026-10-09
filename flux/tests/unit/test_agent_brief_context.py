"""Actionable paper context and task-first agent briefs, without source anchoring on exploration."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from flux_knowledge import BM25Index, Chunk
from flux_knowledge.digest import RECIPE
from flux_knowledge.library import agent_section
from flux_loop.agent import agent_brief
from flux_loop.novelty import explore_brief, fresh_context
from flux_loop.types import Candidate, LoopRequest, LoopState, Scored
from flux_store import CampaignStore


@pytest.fixture()
def papers(tmp_path, monkeypatch):
    paths = [str(tmp_path / f"paper-{i}.pdf") for i in range(6)]
    chunks = [Chunk(id=f"code{i}", standard_id="library", source_path=str(tmp_path / f"impl-{i}.sv"),
                    heading=None, text="radix " * 20 + "implementation " * 30) for i in range(30)]
    for i, path in enumerate(paths):
        chunks.append(Chunk(id=f"paper{i}", standard_id="library", source_path=path, heading=None,
                            text="radix " * (i + 1) + "LEXICAL_EXCERPT " * (49 - i)))
    index = BM25Index(chunks)
    monkeypatch.setattr("flux_knowledge.library.index_for", lambda _folders: index)
    db = str(tmp_path / "papers.db")
    digests = [f"Paper {i}: a method\nIMPLEMENTATION: use {i + 2} stages.\nNUMBERS: exactly {i + 3} bits.\n"
               + "Keep the intermediate remainder signed and test boundary cases. " * 12
               + f"\nPITFALL: avoid overflow in test {i}. END_OF_DIGEST_{i}" for i in range(6)]
    store = CampaignStore(db)
    for path, digest in zip(paths, digests):
        store.results.put_document("digest", {"source": path, "digest": digest, "recipe": RECIPE})
    return db, paths, digests, store


def test_nearest_papers_carry_full_digests_despite_higher_ranked_implementation_files(papers):
    db, paths, digests, _store = papers
    section = agent_section(["radix", "radix"], db=db, n=2)
    for i in (5, 4):
        assert paths[i] in section and digests[i] in section
        assert section.count(f"END_OF_DIGEST_{i}") == 1
    assert section.index(digests[5]) < section.index(digests[4])
    assert all(f"END_OF_DIGEST_{i}" not in section for i in range(4))
    assert "LEXICAL_EXCERPT" not in section
    assert "impl-0.sv" in section, "reference implementations remain available by path"


def test_missing_digest_is_explicit_and_never_replaced_by_a_lexical_excerpt(papers):
    db, paths, digests, store = papers
    store.results.put_document("digest", {"source": paths[5], "digest": "", "recipe": RECIPE})
    section = agent_section("radix", db=db, n=2)
    assert f"[{Path(paths[5]).name}] {paths[5]}\nDigest unavailable" in section
    assert digests[4] in section and "LEXICAL_EXCERPT" not in section
    assert "END_OF_DIGEST_3" not in section, "the nearest N papers, not N arbitrary stored digests"


def test_agent_task_precedes_a_large_static_prefix_and_keeps_contract_and_handoff(tmp_path):
    body = "THIS TURN: write a new algorithm. EXPLORE: take a reasoned risk."
    prefix = "CONTRACT: exact results.\n\nKNOWLEDGE: " + "large static context " * 6000 + "\n\nREPLY SHAPE: JSON"
    brief = agent_brief(body=body, prefix=prefix, artifact=tmp_path / "draft.py", workdir=tmp_path,
                        language="Python", part="core", prior=None, failure="", library="LIBRARY: FULL DIGEST")
    assert brief.startswith(body + "\n\nCONTRACT:")
    assert brief.index("EXPLORE:") < brief.index("KNOWLEDGE:") < brief.index("LIBRARY:")
    assert "REPLY SHAPE" not in brief and "HOW TO ANSWER" in brief and str(tmp_path / "draft.py") in brief


@pytest.mark.parametrize("header", [
    "# INTENT: digit recurrence\n# Use a remainder register.\n",
    "// INTENT: digit recurrence\n// Use a remainder register.\n",
    "/* INTENT: digit recurrence\n * Use a remainder register.\n */\n",
    "/* Copyright 2026 */\n/* INTENT: digit recurrence\n * Use a remainder register.\n */\n",
    '"""INTENT: digit recurrence\nUse a remainder register.\n"""\n',
])
def test_explore_keeps_intent_and_latest_measurements_without_implementation(header):
    cand = Candidate("incumbent#7", header + "\nIMPLEMENTATION_MARKER = 42\n" + "source " * 20000,
                     subgoal="core", meta={"why": "old summary"})
    state = LoopState(request=LoopRequest(), say=lambda _m: None, proposer=None, feedback=None,
                      scored=[Scored(cand, "screen", {"cycles": 8}), Scored(cand, "confirm", {"cycles": 6, "area": 12})])
    text = explore_brief("Explore another algorithm", cand, state)
    assert "INTENT: digit recurrence\nUse a remainder register." in text
    assert "MEASURED (confirm): cycles 6, area 12" in text and "cycles 8" not in text
    assert "IMPLEMENTATION_MARKER" not in text and "source source" not in text and "```" not in text
    assert len(text) < 1000


def test_explore_uses_prior_run_measurements_and_does_not_invent_missing_intent():
    cand = Candidate("old#1", "SECRET_SOURCE", meta={"why": "use lookup tables"})
    old = Scored(cand, "confirm", {"cycles": 3})
    state = SimpleNamespace(scored=[], _history=[old])
    brief = explore_brief("Try another structure", cand, state)
    assert "INTENT: use lookup tables" in brief and "cycles 3" in brief and "SECRET_SOURCE" not in brief
    unknown = explore_brief("Explore", Candidate("new", "SECRET_SOURCE"))
    assert "INTENT: not recorded" in unknown and "no recorded numbers available" in unknown


@pytest.mark.parametrize("fails", [False, True])
def test_fresh_context_preserves_incumbents_and_keeps_better_new_prototypes(fails):
    state = LoopState(request=LoopRequest(), say=lambda _: None, proposer=None, feedback=None)
    state.best["core"] = (1, Candidate("old", "OLD_SOURCE"), "old failure")
    state.prototypes["core"] = "OLD_VERIFIED_PROTOTYPE"
    state.proto_best["core"] = (3, "OLD_REFUSED_PROTOTYPE", "old failure")
    state.part("core").sessions["generate"] = object()
    state.prototypes["other"] = "OTHER_PART"

    def experiment():
        with fresh_context(state, "core"):
            assert "core" not in state.best and "core" not in state.prototypes and "core" not in state.proto_best
            assert not state.part("core").sessions
            assert state.prototypes["other"] == "OTHER_PART"
            if fails:
                raise RuntimeError("generation failed")
            state.prototypes["core"] = "NEW_VERIFIED_PROTOTYPE"
            state.proto_best["core"] = (2, "NEW_REFUSED_PROTOTYPE", "new failure")

    if fails:
        with pytest.raises(RuntimeError, match="generation failed"):
            experiment()
    else:
        experiment()
    assert state.best["core"][1].artifact == "OLD_SOURCE"
    assert state.prototypes["core"] == ("OLD_VERIFIED_PROTOTYPE" if fails else "NEW_VERIFIED_PROTOTYPE")
    assert state.proto_best["core"][1] == ("OLD_REFUSED_PROTOTYPE" if fails else "NEW_REFUSED_PROTOTYPE")
