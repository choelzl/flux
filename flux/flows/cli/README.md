# flows/cli — the `flux` command

The three commands docs/05.md's Phase 1 checklist names: `flux import`, `flux eval`,
`flux replay`.

- **`flux import <file> [--kind K] [--store DB]`** — load a YAML/JSON IR document, auto-detect
  or take `--kind` (workload/architecture/mapping), validate it against the matching schema,
  print its content hash. With `--store`, persists it into a `flux-store` `ResultStore`.
- **`flux eval --workload W [--arch A] --backend {zigzag,timeloop} [--metrics m1,m2] [--store DB]`**
  — validate the workload (and architecture, if given), build a `Candidate`, run it through the
  named backend, print the resulting `Result` as JSON. With `--store`, persists the input
  document(s) *and* the result together, so a later `flux replay` is self-contained.
- **`flux replay RESULT_ID --store DB`** — look up a stored result, fetch the exact workload/
  architecture documents that produced it (by their recorded content hash), infer which backend
  produced it from `Result.provenance.evaluator`, re-run that backend on those same inputs, and
  report per-metric OK/MISMATCH. This is docs/04.md §8's "deterministic replay of any published
  result is a single command," made concrete and checkable rather than just printing a cached
  value back.

Package: `flux-cli` (on `PYTHONPATH` under `nix develop .#python`, which also puts a `flux`
wrapper script — `python3 -c "from flux_cli.main import main; main()"` — on `PATH`; see
`flake.nix`'s `shellHook`). Deliberately does **not** depend on
`flux-evaluator-zigzag`/`flux-evaluator-timeloop` — see `registry.py`'s module docstring for why;
`flux import` works with nothing but `flux-ir` on `PYTHONPATH`.

Not a generated surface: doc 04 §7.2's "one definition, three surfaces" (typed function / CHIA
node / MCP tool) is what `flows/` is meant to converge on — `flows/chia_nodes/` and `flows/mcp/`
are both still empty. This CLI is hand-written argparse, a real and usable stepping stone, not
that end state.

See [docs/05.md Phase 1](../../docs/05.md#phase-1--spine-68-weeks) and
[docs/04.md §7](../../docs/04.md#7-l6--flows-and-the-agent-surface).
