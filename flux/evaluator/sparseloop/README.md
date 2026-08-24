# evaluator/sparseloop — sparse-tensor accelerator modelling (not built)

Sparseloop extends Timeloop to sparse workloads. `evaluator/timeloop/` covers the dense case
and this repo's workload IR has no sparsity annotation for an adapter to translate, so the rung
above it has nothing to stand on yet. `core/ir/workload/examples/mlp-gemm0-sparse-v1.yaml` is the
single sparse example, written to document the gap rather than to be evaluated.

## Why the directory exists at all

`tests/unit/test_backend_registry_parity.py` checks that every backend with code is registered,
and treats `cimloop`, `hammer` and `sparseloop` as deliberate placeholders rather than missing
registrations. That check needs somewhere to point, and a reader deserves to find the reason here
rather than in a test's docstring.

## If you build it

Follow `evaluator/README.md`: CHIA ships no sparseloop integration, so this would wrap the tool
directly, the way `evaluator/timeloop/` and `evaluator/booksim/` do — and implement the same
`flux_evaluator_abi` `Evaluator` protocol as every other rung, so it is interchangeable with them.
