"""D885: every kind the server writes to the audit trail has a group in Admin › Insights' What filter
(D724, D733). Maintenance added kinds the filter did not know, and an "Other" group appeared; so had
sixteen others before it, unseen."""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "interfaces/web/src/flux_web"


def test_every_audit_kind_is_in_a_group():
    kinds = set()
    for p in WEB.glob("*.py"):
        kinds |= set(re.findall(r'audit\([^,()]+,\s*"([^"{}]+)"', p.read_text()))
    js = (WEB / "static/admin.js").read_text()
    block = js[js.index("const GROUPS = ["):js.index("const groupOf")]
    grouped = set(re.findall(r'"([^"]+)"', block))
    kinds.discard("network refused")
    assert kinds and kinds <= grouped, sorted(kinds - grouped)
