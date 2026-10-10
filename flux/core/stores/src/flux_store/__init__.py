"""The loop's record (docs/records.md): `CampaignStore` and the `ResultStore` it shares a SQLite
file with -- content-addressed documents and the trials' measurements.
"""

from __future__ import annotations

from .campaign import CampaignStore, CampaignStoreError, Trial
from .store import ResultStore

__all__ = ["ResultStore", "CampaignStore", "CampaignStoreError", "Trial"]
