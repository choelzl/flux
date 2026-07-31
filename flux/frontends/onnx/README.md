# frontends/onnx — the ONNX frontend

Translates an ONNX model into a Flux Workload IR document (`onnx_model_to_workload_ir`).

**v0.1 scope, same discipline as `evaluators/`:** only a **pure chain of MatMul/Gemm nodes** (an
MLP) is supported — one `einsum` op per node, chained. Anything else (`Conv`, activations,
reshape/flatten, transposed Gemm, a non-static or non-2D input shape, a weight that isn't a
constant initializer, more than one graph input) raises `NotExpressibleError` naming exactly
what wasn't supported. **Real CNN models are expected to be rejected**, immediately, on their
first `Conv` node — that's correct behaviour, not a gap to silently paper over. See
`tests/integration/test_onnx_frontend_live.py`, which checks both the happy path (a synthetic
MLP) and the rejection of a real bundled ResNet18 ONNX file from `zigzag-dse`.

The dimension names this frontend generates (`d0`, `d1`, `d2`, ...) are arbitrary — they only
work against a *translated* Architecture IR document (auto-generated spatial mapping), not
against `evaluators/zigzag`'s bundled `tpu_like` default, which needs loop dims literally named
`K`/`C`. Use `Candidate(workload=..., arch=<translated architecture>, mapping=None)`.

Package: `flux-frontend-onnx` (on `PYTHONPATH` under `nix develop .#python`). Depends on `flux-ir` and
`onnx` — nothing evaluator-specific, so it works standalone.

See [docs/04.md §2 (L1)](../../docs/04.md#2-layering) and
[docs/05.md Phase 1](../../docs/05.md#phase-1--spine-68-weeks) ("ONNX frontend").
