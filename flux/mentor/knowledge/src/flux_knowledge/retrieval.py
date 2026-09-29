"""Retrieval (docs/agent-surface.md, D3): a BM25 lexical index over ingested `Chunk`s, and
`knowledge_lookup()`, the typed function every caller retrieves through.

BM25, not embeddings: deterministic, offline, no API key or model download for tests. An
embedding backend, if needed, would be a second `Index` behind the same `search()` contract.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flux_knowledge.document import Chunk

_TOKEN = re.compile(r"[a-z0-9][a-z0-9.\-]*")

# Standard BM25 free parameters (Robertson/Sparck Jones): k1 is term-frequency saturation, b is
# length-normalisation strength. The field's defaults, untuned (no relevance-judgement set here).
_K1 = 1.5
_B = 0.75


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk: Chunk
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {"chunk": self.chunk.to_dict(), "score": self.score}


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
        # Standard BM25 IDF, floored at a small positive epsilon so a term in every document
        # (e.g. "the", "instruction" in this small corpus) does not go negative and penalise.
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


def build_default_index(knowledge_root: str | Path, *, repo_root: str | Path,
                        library: str | Path | None = None) -> BM25Index:
    """Build the index from every standard directory under `knowledge_root/corpus/` (one
    subdirectory per `standard_id`, e.g. `corpus/riscv-unpriv/`), using the AsciiDoc connector,
    and the library (`library`, default `knowledge_root/library`).
    A new standard is a new subdirectory, not a code change.
    """
    from flux_knowledge.connectors.adoc import ingest_adoc_directory

    knowledge_root = Path(knowledge_root)
    repo_root = Path(repo_root)
    # Accept either `<root>/corpus` (a child; tests) or `<root>/../corpus` (a sibling; the real
    # tree under mentor/).
    corpus_root = knowledge_root / "corpus"
    if not corpus_root.is_dir():
        corpus_root = knowledge_root.parent / "corpus"
    chunks: list[Chunk] = []
    for standard_dir in sorted(p for p in corpus_root.iterdir() if p.is_dir()):
        chunks.extend(
            ingest_adoc_directory(standard_dir, standard_id=standard_dir.name, repo_root=repo_root)
        )
    # The library (D407): the person's own documents, gitignored, indexed beside the corpus
    # under standard_id "library". An absent or empty library adds nothing.
    from flux_knowledge.connectors.text import ingest_library

    chunks.extend(ingest_library(Path(library) if library else knowledge_root / "library", repo_root=repo_root))
    return BM25Index(chunks)


_default_index_cache: dict[str, BM25Index] = {}


def shared_library() -> Path:
    """The shared library folder: `FLUX_LIBRARY` when set, else `mentor/knowledge/library`."""
    return Path(os.environ.get("FLUX_LIBRARY") or Path(__file__).resolve().parents[2] / "library")


def _cached_default_index() -> BM25Index:
    lib = str(shared_library())
    if lib not in _default_index_cache:
        # Provenance paths are relative to the repo root `flux/`, two levels up, so a chunk
        # cites `mentor/knowledge/corpus/...`.
        knowledge_root = Path(__file__).resolve().parents[2]
        repo_root = knowledge_root.parents[1]  # flux/
        _default_index_cache[lib] = build_default_index(knowledge_root, repo_root=repo_root, library=lib)
    return _default_index_cache[lib]


def knowledge_lookup(
    query: str, standard_id: str | None = None, *, k: int = 5, index: BM25Index | None = None
) -> list[RetrievedChunk]:
    """Retrieve the top-`k` chunks matching `query`, optionally restricted to one `standard_id`
    (e.g. `"riscv-unpriv"`).

    `index` defaults to a lazily-built, process-wide cache over `knowledge/corpus/`; pass an
    explicit index (as tests do) to avoid that global state.
    """
    idx = index if index is not None else _cached_default_index()
    if standard_id is None:
        return idx.search(query, k=k)
    # Rank the whole corpus, then filter -- never a bounded over-fetch window, which could return
    # too few hits for a standard ranked low. `search()` sorts every chunk anyway (D164).
    ranked = idx.search(query, k=len(idx))
    return [r for r in ranked if r.chunk.standard_id == standard_id][:k]
