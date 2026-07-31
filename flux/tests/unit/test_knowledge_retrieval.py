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
