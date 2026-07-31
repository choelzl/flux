# evaluators/abi — the Evaluator ABI

The narrow contract: `evaluate(candidate, budget, metrics) -> Result`, plus mandatory
`evaluate_batch`. Any cost model that implements the `Evaluator` protocol here becomes swappable;
any search strategy that only depends on these types becomes portable across evaluators.

`Result` carries `metrics` (each an `Estimate` with a confidence interval, never a bare scalar),
`validity` (computed by an independent checker), `domain` (extrapolation flag), `bottleneck`
(structured, not prose), `provenance`, and `escalation`.

See [docs/04.md §4](../../docs/04.md#4-l4--the-evaluator-abi).

Package: `flux-evaluator-abi` (on `PYTHONPATH` under `nix develop .#python`).
