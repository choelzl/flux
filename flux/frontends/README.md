# frontends/ — workload ingest

ONNX, MLIR (linalg/tosa/stablehlo), PyTorch export, handwritten YAML — all lower into the
Workload IR (ir/workload/). Frontends, not the internal representation.

See [docs/04.md §2 (L1)](../docs/04.md#2-layering) and [docs/02.md §6](../docs/02.md).

`onnx/` is implemented — a real `flux-frontend-onnx` package translating pure MatMul/Gemm ONNX
graphs into Flux Workload IR (see its README for the exact, narrow v0.1 scope and why real CNN
models are correctly rejected rather than silently mishandled). `mlir/`, `pytorch/`, `yaml/` are
not started.
