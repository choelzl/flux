"""Domain knowledge / context layer (docs/agent-surface.md, docs/decisions.md D3): ingest specs and
standards, index them, and expose retrieval to agents via `knowledge_lookup`.
"""

from __future__ import annotations

from .context import LIBRARY_HEADER, library_context
from .document import Chunk
from .mentor import (Corpus, KnowledgeSource, Library, Mentor, Mined, Notes,
                     RecordReadback)
from .retrieval import BM25Index, RetrievedChunk, build_default_index, knowledge_lookup, tokenize

__all__ = [
    "LIBRARY_HEADER",
    "library_context",
    "Chunk",
    "Corpus",
    "KnowledgeSource",
    "Library",
    "Mentor",
    "Mined",
    "Notes",
    "RecordReadback",
    "BM25Index",
    "RetrievedChunk",
    "build_default_index",
    "knowledge_lookup",
    "tokenize",
]
