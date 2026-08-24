# interfaces/cli — the `flux` command

The `flux` command: the loop's commands (`flux ask`, `flux task check|run`, `flux run`/`status`/
`stop`/`attach`, `flux report`, `flux rtl test|measure`, `flux knowledge`, `flux gc`;
see [docs/usage-guide.md](../../../docs/usage-guide.md) and `flux --help`) and the three IR commands
this page describes, `flux import`, `flux eval` and `flux replay`.

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
  report per-metric OK/MISMATCH. This is docs/stores.md's "deterministic replay of any published
  result is a single command," made concrete and checkable rather than just printing a cached
  value back.

Package: `flux-cli` (on `PYTHONPATH` under `nix develop`, which also puts a `flux`
wrapper script — `python3 -c "from flux_cli.main import main; main()"` — on `PATH`; see
`flake.nix`'s `shellHook`). Deliberately does **not** depend on
`flux-evaluator-zigzag`/`flux-evaluator-timeloop` — see `registry.py`'s module docstring for why;
`flux import` works with nothing but `flux-ir` on `PYTHONPATH`.

Hand-written argparse. `flux task run <doc> --json FILE` writes the run's answer as JSON for a
script or an agent.

See [docs/agent-surface.md](../../../docs/agent-surface.md) for how scripts and agents drive it; the IR commands date from the
accelerator era's Phase 1.
