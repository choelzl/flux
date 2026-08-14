# evaluators/abi — the Evaluator ABI

The narrow contract: `evaluate(candidate, budget, metrics) -> Result`, plus mandatory
`evaluate_batch`. Any cost model that implements the `Evaluator` protocol here becomes swappable;
any search strategy that only depends on these types becomes portable across evaluators.

`Result` carries `metrics` (each an `Estimate` with a confidence interval, never a bare scalar),
`validity` (computed by an independent checker), `domain` (extrapolation flag), `bottleneck`
(structured, not prose), `provenance`, and `escalation`.

Every type here has both `to_dict()` and `from_dict()` ([decisions.md D19](
../../../docs/decisions.md)) — `from_dict()` is the exact inverse, for reconstructing a typed
`Result` from a plain dict (what `flux_store.ResultStore` hands back, what an MCP client receives
over the wire). Checked with a real round trip, including the two optional nested shapes
(`Validity.violations`, `Bottleneck.roofline`) a bare "doesn't crash" test would miss —
`tests/unit/test_evaluator_abi.py`.

See [docs/evaluator-abi.md](../../../docs/evaluator-abi.md).

Package: `flux-evaluator-abi` (on `PYTHONPATH` under `nix develop .#python`).
