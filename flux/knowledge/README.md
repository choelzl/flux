# knowledge/ — domain knowledge / context layer

Specs, standards, and protocol documents, ingested and indexed for retrieval, exposed to agents
the same three-surfaces way as everything else (typed function / CHIA node / MCP tool) via
`knowledge_lookup`.

New relative to the original target-architecture proposal.
See [docs/00-decisions.md D3](../docs/00-decisions.md).

## What's implemented

`flux-knowledge` (`src/flux_knowledge/`): a pure-Python BM25 lexical index (`retrieval.py`, no
embeddings, no API key, deterministic — see its module docstring for why) over `Chunk`s ingested
by `connectors/adoc.py`, plus `knowledge_lookup(query, standard_id=None, k=5)` — the typed-
function surface of docs/04.md §7.2's "one definition, three surfaces." The CHIA-node and MCP-tool
surfaces don't exist (`flows/chia_nodes/`, `flows/mcp/` are still empty), same gap as everywhere
else `@flux_tool` would apply.

`corpus/riscv-unpriv/`: five hand-picked chapters of the real RISC-V unprivileged ISA manual
(CC BY 4.0 — see that directory's `PROVENANCE.md` for exact source commit and the license check),
chosen for direct relevance to `ir/architecture/examples/generic-riscv-soc-v1.yaml`. AMBA/JEDEC
and every other standard docs/00-decisions.md D3 names are examples of what the layer *could*
hold, not things it holds today — their licensing is still an open question in that doc, and
nothing in this repo yet generates or evaluates interconnects or memory controllers that would
use them. Don't add them without doing that check first.

See `tests/integration/test_knowledge_riscv_corpus.py` for retrieval against the real corpus, and
`tests/unit/test_knowledge_retrieval.py` / `test_knowledge_adoc_connector.py` for the BM25 index
and AsciiDoc parsing on synthetic input.

Not implemented: any standard beyond `riscv-unpriv`; a real (non-lexical) embedding backend;
`connectors/` for any format other than AsciiDoc.
