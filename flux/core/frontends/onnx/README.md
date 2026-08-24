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

**The reverse direction is real too now** (docs/decisions.md D81): `workload_ir_to_onnx_model`
translates a Flux Workload IR document into a real, `onnx.checker`-validated ONNX model — built
so a real external tool whose only workload input is ONNX (Stream, KU Leuven MICAS — docs/
decisions.md D80) can consume a Flux Workload IR document directly, without a second, parallel
workload description invented just for it. Deliberately symmetric with the forward direction's
own v0.1 scope (a chained sequence of 2D-GEMM `einsum` ops, fully static bounds) — each op
becomes one ONNX `Gemm` node with a deterministic, all-zero weight initializer (a real DSE
cost-model consumer reads only shapes/dtypes, never actual weight values). Verified two ways: a
real round trip back through `onnx_model_to_workload_ir` reproducing the exact original bounds,
and a real, decisive end-to-end run of `mlp-gemm0.yaml` through actual Stream
(`stream.api.optimize_allocation_co_generic`, Stream's own bundled single-core hardware config)
— confirmed via Stream's own log output parsing a real `Gemm node`, reporting
`total_latency=871.0`. See `tests/integration/test_stream_flux_export_live.py`.

Package: `flux-frontend-onnx` (on `PYTHONPATH` under `nix develop .#python`). Depends on
`flux-ir`, `onnx`, and `numpy` — nothing evaluator-specific, so it works standalone.

See [docs/architecture.md (L1)](../../../docs/architecture.md) and
[docs/roadmap.md Phase 1](../../../docs/roadmap.md#phase-1--spine-68-weeks) ("ONNX frontend").
