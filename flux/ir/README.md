# ir/ — Flux IR

Workload / Architecture / Mapping IR: schemas (JSON Schema + protobuf), canonicalisation, and
content-addressed hashing. The contract everything else is built behind.

See [docs/04.md §3](../docs/04.md#3-l2--the-flux-ir), amended by
[docs/00-decisions.md D1](../docs/00-decisions.md) (general-SoC superset, not DNN-only).

## Layout

- `src/flux_ir/schemas/` — the versioned JSON Schema files (`workload.schema.json`,
  `architecture.schema.json`, `mapping.schema.json`), v0.1.0. Single source of truth for what a
  valid IR document looks like. Lives inside the package (not a sibling of `src/`) so it ships as
  real package data in a built wheel, not only under an editable install.
- `workload/examples/`, `architecture/examples/`, `mapping/examples/` — reference IR documents,
  one DNN-accelerator example and one general-SoC example per category (D1), doubling as test
  fixtures for `tests/unit/test_ir_*.py`.
- `src/flux_ir/` — the implementation package (on `PYTHONPATH` under `nix develop .#python`):
  `canonical.py` (canonicalisation + content hashing, §3.4), `schemas.py` (schema loading +
  validation).

Protobuf schemas mentioned in the original proposal are not started — JSON Schema alone is
sufficient for Phase 1's exit criterion (docs/05.md).
