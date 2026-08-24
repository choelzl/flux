"""`flux_knowledge_lookup` — the first of D9's priority-2 nodes (docs/decisions.md D9/D11):
gives an agent tool access to the domain-knowledge layer (`flux_knowledge.knowledge_lookup`),
which existed as a real, working typed function since D3 but had no CHIA-node or MCP-tool surface
until now — an agentic proposer with no way to look up a spec passage was missing a capability
this whole project's framing assumes it has.
"""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction
from flux_knowledge import knowledge_lookup


@ChiaFunction()
def flux_knowledge_lookup(
    query: str, standard_id: str | None = None, k: int = 5
) -> list[dict[str, Any]]:
    """Retrieve the top-`k` chunks of ingested spec/standard text matching `query`, optionally
    restricted to one `standard_id` (e.g. `"riscv-unpriv"`). Thin wrapper — no new retrieval
    logic here, just `.to_dict()` serialization over `flux_knowledge`'s real BM25 index so the
    result is JSON-safe for an MCP client.
    """
    results = knowledge_lookup(query, standard_id, k=k)
    return [r.to_dict() for r in results]
