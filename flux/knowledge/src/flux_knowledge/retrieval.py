"""Retrieval (docs/04.md §7.2, docs/00-decisions.md D3): a BM25 lexical index over ingested
`Chunk`s, and `knowledge_lookup()` — the typed-function surface of docs/04.md §7.2's "one
definition, three surfaces" (`@flux_tool` also generates a CHIA node and an MCP tool; neither
exists yet, since `flows/chia_nodes/` and `flows/mcp/` are both still empty — same gap as every
other `@flux_tool` candidate in this repo, not new to knowledge_lookup specifically).

BM25, not embeddings: deterministic, offline, no API key or model download needed to run this
repo's tests — matching this project's "real, not fabricated" ethos better than a mocked
embedding call would. If semantic recall turns out to matter once the corpus grows past hand-
picked spec chapters, add a real embedding backend as a second `Index` implementation behind the
same `search()` contract, rather than faking one now.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from flux_knowledge.document import Chunk

_TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-]*")

# Standard BM25 free parameters (Robertson/Sparck Jones); k1 controls term-frequency saturation,
# b controls document-length normalisation strength. Untuned defaults are the field's own
# convention, not a value fitted to this corpus — there's no held-out relevance-judgement set to
# tune against here (a real one would need the same holdout discipline as corpus/, which this
# five-chapter spec corpus is too small to support).
_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk: Chunk
    score: float


class BM25Index:
    """An in-memory BM25 index over a fixed list of `Chunk`s, built once at construction."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks
        self._doc_tokens = [tokenize(c.text) for c in chunks]
        self._doc_lengths = [len(toks) for toks in self._doc_tokens]
        self._avg_doc_length = (
            sum(self._doc_lengths) / len(self._doc_lengths) if self._doc_lengths else 0.0
        )
        self._doc_term_counts = [Counter(toks) for toks in self._doc_tokens]

        doc_freq: Counter[str] = Counter()
        for toks in self._doc_tokens:
            doc_freq.update(set(toks))
        n = len(chunks)
        # Standard BM25 IDF, floored at a small positive epsilon rather than letting a term that
        # appears in every single document go negative (which would penalise, not just fail to
        # reward, documents containing it) — a real edge case with a corpus this small (e.g.
        # "the", "instruction" appearing in nearly every chunk).
        self._idf = {
            term: max(math.log((n - df + 0.5) / (df + 0.5) + 1.0), 1e-9)
            for term, df in doc_freq.items()
        }

    def __len__(self) -> int:
        return len(self._chunks)

    def search(self, query: str, *, k: int = 5) -> list[RetrievedChunk]:
        query_terms = tokenize(query)
        if not query_terms or not self._chunks:
            return []

        scores = [0.0] * len(self._chunks)
        for i, term_counts in enumerate(self._doc_term_counts):
            doc_len = self._doc_lengths[i]
            length_norm = 1 - _B + _B * (doc_len / self._avg_doc_length) if self._avg_doc_length else 1
            for term in query_terms:
                idf = self._idf.get(term)
                if idf is None:
                    continue
                tf = term_counts.get(term, 0)
                if tf == 0:
                    continue
                scores[i] += idf * (tf * (_K1 + 1)) / (tf + _K1 * length_norm)

        ranked = sorted(
            (i for i in range(len(self._chunks)) if scores[i] > 0),
            key=lambda i: scores[i],
            reverse=True,
        )
        return [RetrievedChunk(chunk=self._chunks[i], score=scores[i]) for i in ranked[:k]]


def build_default_index(knowledge_root: str | Path, *, repo_root: str | Path) -> BM25Index:
    """Build the index from every standard directory under `knowledge_root/corpus/` (one
    subdirectory per `standard_id`, e.g. `corpus/riscv-unpriv/`), using the AsciiDoc connector.
    Standard-agnostic on purpose: adding a new standard is "add a subdirectory + ingest it here,"
    not a code change per standard.
    """
    from flux_knowledge.connectors.adoc import ingest_adoc_directory

    knowledge_root = Path(knowledge_root)
    repo_root = Path(repo_root)
    corpus_root = knowledge_root / "corpus"
    chunks: list[Chunk] = []
    for standard_dir in sorted(p for p in corpus_root.iterdir() if p.is_dir()):
        chunks.extend(
            ingest_adoc_directory(standard_dir, standard_id=standard_dir.name, repo_root=repo_root)
        )
    return BM25Index(chunks)


_default_index_cache: BM25Index | None = None


def _cached_default_index() -> BM25Index:
    global _default_index_cache
    if _default_index_cache is None:
        knowledge_root = Path(__file__).resolve().parents[2]  # knowledge/
        repo_root = knowledge_root.parent  # flux/
        _default_index_cache = build_default_index(knowledge_root, repo_root=repo_root)
    return _default_index_cache


def knowledge_lookup(
    query: str, standard_id: str | None = None, *, k: int = 5, index: BM25Index | None = None
) -> list[RetrievedChunk]:
    """Retrieve the top-`k` chunks matching `query`, optionally restricted to one `standard_id`
    (e.g. `"riscv-unpriv"`). This is the typed-function surface docs/04.md §7.2 describes — see
    this module's docstring for what's not built yet (the CHIA node and MCP tool surfaces).

    `index` defaults to a lazily-built, process-wide cache over this repo's real
    `knowledge/corpus/` — pass an explicit index (as every test in this package does) to avoid
    depending on that global state.
    """
    idx = index if index is not None else _cached_default_index()
    if standard_id is None:
        return idx.search(query, k=k)
    # Over-fetch then filter rather than teaching BM25Index about partitioning: standard_id
    # filtering is a query-time concern, not an indexing-time one, and this corpus is small
    # enough that over-fetching is not a real cost.
    over_fetched = idx.search(query, k=max(k * 8, 50))
    return [r for r in over_fetched if r.chunk.standard_id == standard_id][:k]
