# tests/ — unit, integration, conformance, golden

conformance/ is the load-bearing directory: any new evaluator or generation backend must pass
this suite proving it interprets the IR the same way as the reference, or fails loudly on the
parts it cannot express.

See [docs/04.md §10](../docs/04.md#10-repository-layout).

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
just reported (see `../docs/phase1-exit-criterion-report.md`); `test_store_live.py` round-trips
real results from both backends through the store; `test_cli_eval_replay.py` drives
`flux eval`/`flux replay` end to end, including a genuine re-run-and-diff replay;
`test_onnx_frontend_live.py` runs a synthetic MLP through real ZigZag and confirms zigzag-dse's
own bundled ResNet18 is correctly rejected; `test_calibration_live.py` calibrates on three real
architecture widths, checks the result against a real fourth, deliberately held-out width, and
confirms the escalation policy fires on both the held-out point and (correctly, on CI-width alone)
the in-domain calibration points — see `../docs/calibration-report.md`. Run with
`nix develop .#python --command python -m pytest -q` from `flux/` (needs a working `docker`
daemon for the Timeloop tests).

`conformance/` is implemented: one shared corpus (every workload example x every architecture
example) and one shared test function, run against every registered backend — not a separate ad
hoc fixture set per adapter. Its expected-outcome matrix was populated by actually running all 24
combinations and recording what happened, not by reading the translators and guessing (this
project's own history includes a test written from a plausible-sounding but empirically false
assumption — see the module's docstring). A dedicated test also checks that wherever two backends
both succeed on the same (workload, architecture) pair, their provenance confirms they saw the
exact same content hash. `golden/` is still empty — reserved for byte-exact output regression
once outputs are stable enough to be worth pinning beyond the specific values already pinned in
`integration/`.
