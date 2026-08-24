# knowledge/library/ — your papers and documents, locally

Drop papers, notes, and reference documents here and they join the same BM25 index the
protocols and the corpus feed: `knowledge_lookup("...", standard_id="library")` retrieves
them, and every agent surface that carries `knowledge_lookup` (typed function, CHIA node,
MCP tool) sees them the same way.

- **Formats**: `.md`, `.txt`, `.adoc`, `.pdf` — anywhere under this folder,
  subdirectories fine. PDFs are extracted through `pdftotext` (poppler) at index time;
  on a machine without it they are skipped with a note, never indexed as raw bytes.
- **Never committed.** The `.gitignore` beside this file ignores everything but itself
  and this README: these are other people's works (papers, vendor docs), which is exactly
  why they cannot be pushed. `corpus/` is the opposite deal — licensed standards with a
  PROVENANCE.md, committed on purpose. If a document belongs to the project and may be
  shared, it goes in `corpus/` or `protocols/` with provenance, not here.
- **Chunking** is per paragraph with the nearest preceding heading attached, same as the
  corpus connector; retrieval is lexical (BM25), so a paper is findable by its own words.
