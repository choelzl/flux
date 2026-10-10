# evaluator/abi — measurement records

What is left of the Evaluator ABI: the record a measurement is kept in, `run_tool` and the
toolchain fingerprints. The `Evaluator` protocol, `evaluate_batch`/`SequentialBatch` and every
backend adapter are gone (D958); every loop stage is a command printing `name=value` (D954).

`Result` carries `metrics` (each an `Estimate` with a confidence interval, never a bare scalar),
`validity`, `domain` (extrapolation flag), `bottleneck` (structured, not prose), `provenance`,
and `escalation`.

Every type here has both `to_dict()` and `from_dict()` ([decisions.md D19](
../../../docs/decisions.md)) — `from_dict()` is the exact inverse, for reconstructing a typed
`Result` from a plain dict (what the campaign store hands back, what a script reads from JSON). Checked with a real round trip, including the two optional nested shapes
(`Validity.violations`, `Bottleneck.roofline`) a bare "doesn't crash" test would miss —
`tests/unit/test_evaluator_abi.py`.

See [docs/evaluator-abi.md](../../../docs/evaluator-abi.md).
