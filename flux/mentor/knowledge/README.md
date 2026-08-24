# knowledge/ — domain knowledge / context layer

Specs, standards, and protocol documents, ingested and indexed for retrieval, exposed to agents
the same three-surfaces way as everything else (typed function / CHIA node / MCP tool) via
`knowledge_lookup`.

New relative to the original target-architecture proposal.
See [docs/decisions.md D3](../../docs/decisions.md).

## What's implemented

`flux-knowledge` (`src/flux_knowledge/`): a pure-Python BM25 lexical index (`retrieval.py`, no
embeddings, no API key, deterministic — see its module docstring for why) over `Chunk`s ingested
by `connectors/adoc.py`, plus `knowledge_lookup(query, standard_id=None, k=5)` — the typed-
function surface of docs/agent-surface.md's "one definition, three surfaces." **All three surfaces now
exist** ([decisions.md D11](../../docs/decisions.md)): `flows/chia_nodes/`'s
`flux_knowledge_lookup` wraps this function as a real `@ChiaFunction()`, and `flows/mcp/`'s
`FluxTool` exposes it as `{name}_knowledge_lookup` — verified against the real, live BM25 index
over the real ingested RISC-V corpus (`tests/integration/test_chia_flux_knowledge_and_store_live.py`),
not a synthetic fixture. `Chunk`/`RetrievedChunk` gained real `.to_dict()` methods for this
(neither had one before D11 — needed for JSON-safe MCP responses).

`corpus/` holds three differently-provenanced classes, each with its own `PROVENANCE.md`:
`riscv-unpriv/` (verbatim licensed spec text), `distributions/` (real ingested measurement data —
a ShareGPT-derived KV-cache-length percentile table, [decisions.md D87](../../docs/decisions.md)),
and `design-guidance/` (curated design wisdom in original prose, every paragraph stating inline
whether it is a repo-measured fact, a cited source's data point, or direction-only guidance,
[D244](../../docs/decisions.md)).

`corpus/riscv-unpriv/`: five hand-picked chapters of the real RISC-V unprivileged ISA manual
(CC BY 4.0 — see that directory's `PROVENANCE.md` for exact source commit and the license check),
chosen for direct relevance to `ir/architecture/examples/generic-riscv-soc-v1.yaml`. The other
standards docs/decisions.md D3 named as examples were actually checked, not left open
(docs/decisions.md D31): AMBA/AXI, JEDEC, PCIe are closed (no redistribution without a paid
license); I2C is ambiguous, treated as closed. WISHBONE B4 turned out genuinely public domain
(verified against the primary-source PDF) but still isn't ingested — nothing in this repo models a
WISHBONE-style bus, so it fails this corpus's own hand-picked-for-relevance bar, not a licensing
one. See that directory's `PROVENANCE.md` for the full per-standard finding.

See `tests/integration/test_knowledge_riscv_corpus.py` for retrieval against the real corpus, and
`tests/unit/test_knowledge_retrieval.py` / `test_knowledge_adoc_connector.py` for the BM25 index
and AsciiDoc parsing on synthetic input.

The sibling `mining/` package (`flux-knowledge-mining`, [decisions.md D243](../../docs/decisions.md))
computes typed facts from the campaign/calibration stores — deliberately *not* ingested into this
BM25 index (mined measured facts and licensed spec text have different provenance classes) — and
renders them into proposer/authoring prompts via `render_facts_for_prompt`
([D245](../../docs/decisions.md)). See `mining/README.md`.

Not implemented: any licensed *standard* beyond `riscv-unpriv` (AMBA/JEDEC/PCIe/I2C are closed,
D31); a real (non-lexical) embedding backend; `connectors/` for any format other than AsciiDoc.
