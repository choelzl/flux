"""`flux_mine_knowledge` — the CHIA node surface for `flux_knowledge_mining`
(docs/decisions.md D243): typed, provenance-carrying facts computed from campaign and
calibration stores. See that package's docstring for the four rules that keep mined facts
factual (computed never asserted; measured language; scope + not_established as fields;
pointers to the exact rows)."""

from __future__ import annotations

from typing import Any

from chia.base.ChiaFunction import ChiaFunction


@ChiaFunction()
def flux_mine_knowledge(
    campaign_db_paths: list[str] | None = None,
    calibration_db_paths: list[str] | None = None,
    facts_db_path: str | None = None,
) -> dict[str, Any]:
    """Mine every fact the given stores support: estimator bias ranges (calibration residual
    families), rung-measured points, observed one-doubling ratios between measured candidate
    pairs, exact refusal patterns, and completed campaigns' frontier outcomes. Every fact
    carries its evidence, its scope, an explicit `not_established` line, and pointers to the
    store rows it was computed from. Nothing is asserted, fitted, or extrapolated; unusable
    stores and non-done campaigns are counted in `skipped`, never silently dropped.

    `facts_db_path` (docs/decisions.md D250) persists the mined facts to a FactStore —
    content-addressed, so re-mining the same stores is idempotent. The report then carries
    `fact_ids` alongside the facts."""
    from flux_knowledge_mining import FactStore, mine_knowledge

    mined = mine_knowledge(
        campaign_db_paths=campaign_db_paths, calibration_db_paths=calibration_db_paths
    )
    out = mined.to_dict()
    if facts_db_path is not None:
        with FactStore(facts_db_path) as store:
            out["fact_ids"] = store.put_facts(mined.facts)
    return out


@ChiaFunction()
def flux_recall_facts(
    facts_db_path: str,
    kind: str | None = None,
    contains: str | None = None,
    verify: bool = False,
) -> dict[str, Any]:
    """Recall persisted facts (docs/decisions.md D250): filter by fact kind and/or a
    case-insensitive substring of the statement. `verify=True` re-derives each recalled fact
    from the store rows it points at and labels it `intact` (same statement still derivable),
    `dangling` (source store gone/unreadable), or `superseded` (the store's evidence moved on)
    — a recalled fact is never silently trusted across time. The returned facts render for
    prompts with the same boundary-preserving renderer campaigns and authoring already use."""
    from flux_knowledge_mining import FactStore

    with FactStore(facts_db_path) as store:
        stored = store.facts(kind=kind, contains=contains)
        out: dict[str, Any] = {"facts": []}
        for s in stored:
            entry = s.to_dict()
            if verify:
                entry["verification"] = store.verify(s)
            out["facts"].append(entry)
    return out
