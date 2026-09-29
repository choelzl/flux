"""Domain knowledge / context layer (docs/agent-surface.md, D3): ingest specs and
standards, index them, and expose retrieval to agents via `knowledge_lookup`.
"""

from __future__ import annotations

from .context import LIBRARY_HEADER, library_context
from .digest import Digest, digest_library, digests_in, index_lines
from .document import Chunk
from .library import agent_section, cited_files, index_for, paper_lines, status
from .mentor import (Corpus, KnowledgeSource, Library, Mentor, Mined, Notes, Papers,
                     RecordReadback)
from .retrieval import BM25Index, RetrievedChunk, build_default_index, knowledge_lookup, tokenize

__all__ = [
    "Digest", "digest_library", "digests_in", "index_lines",
    "LIBRARY_HEADER", "agent_section", "cited_files", "index_for", "paper_lines", "status", "Papers",
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
