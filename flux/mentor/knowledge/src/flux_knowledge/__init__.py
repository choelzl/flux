"""Domain knowledge / context layer (docs/agent-surface.md, docs/decisions.md D3): ingest specs and
standards, index them, and expose retrieval to agents via `knowledge_lookup`.
"""

from __future__ import annotations

from .document import Chunk
from .retrieval import BM25Index, RetrievedChunk, build_default_index, knowledge_lookup, tokenize

__all__ = [
    "Chunk",
    "BM25Index",
    "RetrievedChunk",
    "build_default_index",
    "knowledge_lookup",
    "tokenize",
]
