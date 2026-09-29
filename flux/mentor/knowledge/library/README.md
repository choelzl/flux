# knowledge/library/ — your papers and documents, locally

Drop papers, notes and reference implementations here. Every problem on this machine reads them
(D648); `flow: {knowledge: none}` in a document turns that off.

## Adding papers

- Copy the files in, subfolders fine. **Formats**: `.pdf`, `.md`, `.txt`, `.adoc`, and source
  (`.sv`, `.v`, `.vhd`, `.py`, `.cpp`, `.c`, `.scala`, ...; a `test/`, `tests/` or `.git/` folder
  is skipped).
- PDFs are read through `pdftotext` (poppler, in the dev shell). Without it they are skipped
  with a note, never indexed as raw bytes.
- `flux task check <document>` says how many documents the library holds and whether PDFs can be
  read. Nothing to rebuild: each run indexes the folder when it starts.
- A document may add a folder of its own beside it: `knowledge: {library: papers}`.
- `FLUX_LIBRARY=<dir>` points the shared library elsewhere.

## What uses them

- **Prompts**: excerpts retrieved (BM25, lexical) for the statement, contract and each part,
  each cited by file name, plus one line per paper. The share of the model's window knowledge
  may take bounds them (D548).
- **The plan**: the planner sees the one-line index and may name a method from it.
- **The `knowledge(query)` tool**: searches the whole library, not only the excerpts carried.
- **Coding agents** (Claude Code, Codex, OpenCode): their briefs carry a LIBRARY section, the
  one-line index and the absolute paths of the files nearest the question, to open themselves.
- **Digests**: `flow: {knowledge: [digest]}` has the model write each document's key points
  once into the run's store (D576).
- **The record**: each draft's row names the papers its prompt cited; `flux report` sums them.

## Never committed

The `.gitignore` beside this file ignores everything but itself and this README: these are other
people's works (papers, vendor docs), which is why they cannot be pushed. `corpus/` is the
opposite deal: licensed standards with a PROVENANCE.md, committed on purpose. A document that
belongs to the project and may be shared goes in `corpus/` with provenance, not here.

Chunking is per paragraph with the nearest preceding heading attached (source code per
construct), so a paper is findable by its own words.
