# core/ — native evaluation core

Rust: candidate enumeration, native cost model, batch evaluation. Hot path — no per-candidate
allocation, SoA layouts, exposed to Python via PyO3/nanobind.

See [docs/04.md §9](../docs/04.md#9-performance-engineering). Sequenced for Phase 3
([docs/05.md](../docs/05.md)) — adapters first, native rewrite only after profiling.
