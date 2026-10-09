"""Flux result/artifact store (docs/stores.md): content-addressed IR documents and
Evaluator results with full lineage.
"""

from __future__ import annotations

from .campaign import CampaignStore, CampaignStoreError, Trial
from .store import ResultStore

__all__ = ["ResultStore", "CampaignStore", "CampaignStoreError", "Trial"]
