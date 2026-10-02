# npu_gemm/ -- an accelerator sized for a workload

The smallest accelerator that runs a two-layer feed-forward block (`workload.yaml`, two GEMMs)
in at most 500 cycles. The designs are architectures, not RTL: a script writes each one as
Architecture IR from two knobs, and ZigZag, an analytical accelerator cost model, measures it.
No model is needed.

## The files

| file | what it is |
|---|---|
| `problem.yaml` | the document: the space (PE array width, global buffer size), the gate, the stage, the objectives |
| `workload.yaml` | what runs on the accelerator, as Workload IR |
| `render.py` | writes one architecture from `pe_x` and `gbuf_kb` |
| `check.py` | the gate: the architecture is valid Architecture IR |
| `measure.py` | the stage: ZigZag's cycles and energy through the evaluator registry, plus a first-order area estimate at 28 nm |

## Run it

```bash
nix develop --command flux task run applications/npu_gemm --passes 1
```

About 30 seconds for the 15 points. The front, on this workload:

| PEs | buffer | cycles | energy (pJ) | area (mm2) |
|---|---|---|---|---|
| 4 | 16 KB | 3,120 | 2.24e6 | 0.096 |
| 8 | 16 KB | 1,560 | 1.12e6 | 0.112 |
| 16 | 16 KB | 653 | 4.75e5 | 0.144 |
| **32** | **16 KB** | **341** | **2.51e5** | **0.208** |

The decision is 32 PEs, the least area that makes 500 cycles. 64 PEs are not faster, since this
workload cannot keep them busy, so they only add area; a larger buffer adds area without
changing ZigZag's numbers here.

## Change it

- **Another workload:** replace `workload.yaml` (see `core/ir/workload/examples/` and
  [docs/ir.md](../../../docs/ir.md)).
- **More of the architecture:** add knobs to `flow.orchestrate.space` and to `render.py` (a 2-D array, another
  memory level).
- **A bigger space:** `flow: {orchestrate: gradient}` or a list of phases, or `llm` to let a model
  propose points ([docs/cookbook.md](../../../docs/cookbook.md)).
- **Another cost model:** `make_evaluator("timeloop")` in `measure.py` (it needs Docker, or
  `nix develop .#timeloop`).
