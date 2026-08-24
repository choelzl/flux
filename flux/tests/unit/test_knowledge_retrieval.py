"""Unit tests for flux_knowledge.retrieval: BM25 ranking behaviour over synthetic chunks, no real
corpus involved. See tests/integration/test_knowledge_riscv_corpus.py for retrieval against the
real ingested RISC-V corpus.
"""

from __future__ import annotations

from flux_knowledge.document import Chunk
from flux_knowledge.retrieval import BM25Index, knowledge_lookup, tokenize


def _chunk(id_: str, text: str, standard_id: str = "test-standard") -> Chunk:
    return Chunk(id=id_, standard_id=standard_id, source_path="synthetic.adoc", heading=None, text=text)


def test_tokenize_lowercases_and_splits_on_non_alnum():
    assert tokenize("The fence.i Instruction!") == ["the", "fence.i", "instruction"]


def test_search_ranks_the_more_relevant_document_first():
    index = BM25Index([
        _chunk("a", "The quick brown fox jumps over the lazy dog."),
        _chunk("b", "fence.i synchronizes instruction fetch with prior stores to memory."),
        _chunk("c", "Control and status registers hold hart-local configuration state."),
    ])
    results = index.search("instruction fetch fence synchronization", k=3)
    assert results[0].chunk.id == "b"


def test_search_returns_empty_for_a_query_with_no_matching_terms():
    index = BM25Index([_chunk("a", "completely unrelated content about butterflies")])
    assert index.search("fence.i csr hart", k=5) == []


def test_search_respects_k():
    index = BM25Index([_chunk(str(i), f"instruction fetch fence number {i}") for i in range(10)])
    assert len(index.search("instruction fetch fence", k=3)) == 3


def test_empty_index_returns_empty_results():
    index = BM25Index([])
    assert index.search("anything", k=5) == []


def test_scores_are_positive_and_descending():
    index = BM25Index([
        _chunk("a", "fence.i fence.i fence.i instruction fetch"),
        _chunk("b", "fence.i mentioned once, mostly about something else entirely unrelated"),
    ])
    results = index.search("fence.i", k=2)
    assert all(r.score > 0 for r in results)
    assert results[0].score >= results[1].score


def test_knowledge_lookup_filters_by_standard_id():
    index = BM25Index([
        _chunk("a1", "fence.i instruction fetch fence", standard_id="riscv-unpriv"),
        _chunk("b1", "fence.i instruction fetch fence", standard_id="other-standard"),
    ])
    results = knowledge_lookup("fence.i instruction fetch", standard_id="riscv-unpriv", k=5, index=index)
    assert len(results) == 1
    assert results[0].chunk.standard_id == "riscv-unpriv"


def test_knowledge_lookup_without_standard_id_searches_everything():
    index = BM25Index([
        _chunk("a1", "fence.i instruction fetch fence", standard_id="riscv-unpriv"),
        _chunk("b1", "fence.i instruction fetch fence", standard_id="other-standard"),
    ])
    results = knowledge_lookup("fence.i instruction fetch", k=5, index=index)
    assert {r.chunk.id for r in results} == {"a1", "b1"}


def test_retrieved_chunk_to_dict_is_json_safe():
    import json

    index = BM25Index([_chunk("a1", "fence.i instruction fetch fence")])
    result = knowledge_lookup("fence.i instruction fetch", k=1, index=index)[0]
    d = result.to_dict()
    json.dumps(d)  # raises if anything non-JSON-safe leaked through
    assert d["chunk"]["id"] == "a1"
    assert d["chunk"]["text"] == "fence.i instruction fetch fence"
    assert d["score"] > 0


def test_standard_id_filter_finds_matches_that_rank_below_a_fixed_window():
    """`knowledge_lookup` used to over-fetch a bounded window (`max(k * 8, 50)`) and *then* filter
    by standard_id, so a standard whose matches all ranked below that window returned zero hits
    while real matches existed. Single-standard corpora can't expose it; `build_default_index`'s
    "add a subdirectory per standard" design means multi-standard corpora are the intent.
    """
    chunks = [
        _chunk(f"a{i}", "cache coherence protocol " * 3, standard_id="std-a") for i in range(59)
    ]
    chunks.append(_chunk("b0", "cache " + "filler word here " * 40, standard_id="std-b"))
    index = BM25Index(chunks)

    # The one std-b chunk is a genuine match, but ranks last of the 60.
    assert [r.chunk.standard_id for r in index.search("cache", k=len(index))].index("std-b") == 59

    results = knowledge_lookup("cache", "std-b", k=5, index=index)
    assert [r.chunk.id for r in results] == ["b0"]
