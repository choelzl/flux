# generation/ — RTL generation

Candidate -> generated RTL. **Gated**: do not wire this up for real designs until calibration/
and the independent validity checker in flows/ are live for the workload class in question —
see the Phase 3.5 gating rationale.

New relative to the original target-architecture proposal.
See [docs/00-decisions.md D2](../docs/00-decisions.md) and
[docs/05.md Phase 3.5](../docs/05.md).
