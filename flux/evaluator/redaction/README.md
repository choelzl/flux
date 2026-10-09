# redaction/ — PDK confidentiality policy

`policy.py` (docs/decisions.md D94/D96): a registry of PDKs with their confidentiality, and
`require_not_confidential(pdk_name)`, which an RTL application's `rtl.py measure` (`measure()`)
calls before any synthesis runs. A PDK registered confidential is
refused (`ConfidentialPdkError`); an unregistered one is refused too (`UnknownPdkError`), never
assumed either way.

Only `asap7` is registered, `confidential=False` (BSD-3-Clause, checked against its upstream
`LICENSE`, D92), so the guard never fires in practice. `tests/unit/test_redaction_policy.py`
exercises it with a synthetic confidential entry.

Not implemented: turning absolute numbers into relative deltas or rankings before they reach a
model. The earlier helpers for that had no caller and were removed.
