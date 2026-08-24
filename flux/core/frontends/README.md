# frontends/ — workload ingest

ONNX, MLIR (linalg/tosa/stablehlo), PyTorch export, handwritten YAML — all lower into the
Workload IR (ir/workload/). Frontends, not the internal representation.

See [docs/architecture.md (L1)](../../docs/architecture.md) and [docs/landscape.md](../../docs/landscape.md)'s "ONNX's role, honestly assessed" section.

`onnx/` is implemented — a real `flux-frontend-onnx` package translating pure MatMul/Gemm ONNX
graphs into Flux Workload IR (see its README for the exact, narrow v0.1 scope and why real CNN
models are correctly rejected rather than silently mishandled). `mlir/`, `pytorch/`, `yaml/` are
not started.
