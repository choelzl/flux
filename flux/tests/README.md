# tests/ — unit, integration, conformance, golden

`e2e/web_ui.py` runs the web interface in headless Firefox against its own temporary server,
users, and loop data. Run it from `flux/` with
`nix develop --command python3 tests/e2e/web_ui.py`. It exits nonzero on a failed check and
saves failure screenshots under the browser fixture's `shots/` folder.

The independent flows in `e2e/web_ui_general.py` cover deep-link reloads and browser history,
delayed file responses and retries, concurrent edits and unsaved drafts, literal rendering of
file and agent text, logout/re-login isolation, and admin restart confirmations and results.
Run these flows with:

```sh
FLUX_E2E_STEPS="reload and browser history,delayed files and retry,concurrent file edits,safe text rendering,logout and re-login,admin restart all" \
  nix develop --command python3 tests/e2e/web_ui.py
```

`FLUX_E2E_HOME=/absolute/path` chooses an isolated browser profile and upload fixture folder.
For Snap Firefox, use a path that Firefox can access, such as one under
`~/snap/firefox/common/`. No model or coding agent is needed by these flows.

`e2e/web_ui_lifecycle.py` also covers fullscreen drafts (selection, scroll, Escape and navigation),
raw file output, reset warnings/cancellation and retained inputs/settings, and older starts'
logs, complete agent turns, design source, reloads and delayed responses. It uses real loop
records and API responses, without models or agents:

```sh
FLUX_E2E_STEPS="fullscreen draft and raw,reset confirmation,run history isolation" \
  nix develop --command python3 tests/e2e/web_ui.py
```

`FLUX_E2E_STEPS="admin token rates"` checks Admin Resources' separate input/output rates,
agent/model totals, flat averaging intervals, hover values on either side of a boundary, and
the interval label when switching history ranges, including 30 days for both machine and token
history. `test_web_history.py` checks month retention, progressive compaction, weighted averages,
peaks and outages across both tiers. Unit tests in `test_web_insights.py` and
`test_web_admin.py` cover overlapping and sub-second calls, window clipping, and retained
campaigns across starts and records without counting shared transcripts twice.

`FLUX_E2E_STEPS="partial agent usage,admin token rates"` also checks that interrupted-agent
usage is visibly partial and wholly missing usage is shown as unavailable. `test_agent_usage.py`
covers Claude's cumulative message snapshots, final-result precedence, repeated blocks,
OpenCode/Codex interruptions, and a real timed-out subprocess that reports more usage during
SIGTERM shutdown. `test_web_partial_usage.py` checks the resulting API totals and completeness.

`unit/test_web_api_lifecycle.py` checks live permission changes on the same login session:
revoking/restoring shares across history and download endpoints, downgrading editors before
pending mutations (without changing files or starting processes), and disabling an account.

conformance/ is the load-bearing directory: any new evaluator or generation backend must pass
this suite proving it interprets the IR the same way as the reference, or fails loudly on the
parts it cannot express.

See [docs/architecture.md](../../docs/architecture.md).

`unit/` has real tests for `flux-ir`, `flux-evaluator-abi`, `flux-store`, `flux-calibration`,
`flux-cli`'s `import` command, `flux-frontend-onnx`, and both the ZigZag and Timeloop
workload/architecture translators (schema validation, canonicalisation/hashing, ABI type
invariants, store round-trips/idempotency/lineage queries, residual-statistics and
confidence-interval math (including a regression test for a real additive-CI-goes-negative bug),
escalation-policy triggers (domain, CI width, both, neither), translation edge cases — dynamic bounds, non-einsum ops, malformed einsums, 2D-vs-1D architecture
mismatches, non-MatMul/Gemm ONNX nodes, transposed Gemm, symbolic shapes, branching graphs — all
without touching any external tool). `integration/` runs `zigzag/` against the real, installed
`zigzag-dse` package and `timeloop/` against the real, Dockerized Timeloop+Accelergy (seconds,
not milliseconds each); `test_cross_evaluator_same_architecture_report.py` is the controlled
Phase 1 exit-criterion artifact — same workload *and* architecture through both, diagnosed, not
just reported; `test_store_live.py` round-trips
real results from both backends through the store; `test_cli_eval_replay.py` drives
`flux eval`/`flux replay` end to end, including a genuine re-run-and-diff replay;
`test_onnx_frontend_live.py` runs a synthetic MLP through real ZigZag and confirms zigzag-dse's
own bundled ResNet18 is correctly rejected; `test_calibration_live.py` calibrates on three real
architecture widths, checks the result against a real fourth, deliberately held-out width, and
confirms the escalation policy fires on both the held-out point and (correctly, on CI-width alone)
the in-domain calibration points. Run with
`nix develop --command python -m pytest -q` from `flux/` (needs a working `docker`
daemon for the Timeloop tests).

`conformance/` is implemented: one shared corpus (every workload example x every architecture
example) and one shared test function, run against every registered backend — not a separate ad
hoc fixture set per adapter. Its expected-outcome matrix was populated by actually running all 24
combinations and recording what happened, not by reading the translators and guessing (this
project's own history includes a test written from a plausible-sounding but empirically false
assumption — see the module's docstring). A dedicated test also checks that wherever two backends
both succeed on the same (workload, architecture) pair, their provenance confirms they saw the
exact same content hash. `golden/` holds pinned baselines and captured real tool output:
`calibration_baseline.json` (the drift-detection CI baseline), `timeloop_energy_baseline.json`
(pinned Timeloop energy numbers), and `booksim_congested_output.txt`/
`noxim_low_traffic_output.txt` (captured real simulator output the parser unit tests run
against). The directory's `.gitkeep` is obsolete now that real files live there.
