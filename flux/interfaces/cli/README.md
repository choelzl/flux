# interfaces/cli — the `flux` command

The `flux` command: the loop's commands (`flux ask`, `flux task check|run|migrate`, `flux run`/
`status`/`stop`/`attach`, `flux report`; see [docs/usage-guide.md](../../../docs/usage-guide.md)
and `flux --help`). An RTL document's tools are the application's own `python rtl.py
test|measure`.

The accelerator era's IR commands (`flux import`, `flux eval`, `flux replay`) are gone with the
evaluator registry (D954). `flux gc` (the web's Maintenance cleans up) and `flux knowledge
digest|show` (the library digests itself since D791) went in D952.

Package: `flux-cli` (on `PYTHONPATH` under `nix develop`, which also puts a `flux`
wrapper script — `python3 -c "from flux_cli.main import main; main()"` — on `PATH`; see
`flake.nix`'s `shellHook`). The CLI does not depend on application-owned tools.

Hand-written argparse. `flux task run <doc> --json FILE` writes the run's answer as JSON for a
script or an agent.

See [docs/agent-surface.md](../../../docs/agent-surface.md) for how scripts and agents drive it.
