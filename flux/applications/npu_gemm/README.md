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

One pass measures the 20 points side by side (`budget.batch`), about 30 seconds. The front, on
this workload:

| PEs | buffer | cycles | energy (pJ) | area (mm2) |
|---|---|---|---|---|
| 4 | 1 KB | 3,120 | 2.24e6 | 0.021 |
| 8 | 1 KB | 1,560 | 1.12e6 | 0.037 |
| 16 | 1 KB | 653 | 4.75e5 | 0.069 |
| **32** | **1 KB** | **341** | **2.50e5** | **0.133** |

The decision is 32 PEs and 1 KB, the least area that finishes within 500 cycles. 64 PEs are not
faster, since this workload cannot keep them busy, so they only add area.

The buffer is decided by area alone: in ZigZag's model it holds every operand of a layer behind
one 2048-bit port, so its size never changes the cycles (D877). What it must do is hold a layer:
`ffn.up` is 832 B (64 B of `H_act`, 512 B of `W2`, 256 B of `O` at 16 bits), so 0.75 KB has no
mapping at all and 1 KB is the floor. The space is 1, 2, 4 and 8 KB around it; the old 16, 64 and
256 KB were all past it, each point the same cycles at more area.

## Change it

- **Another workload:** replace `workload.yaml` (see `core/ir/workload/examples/` and
  [docs/ir.md](../../../docs/ir.md)).
- **More of the architecture:** add knobs to `flow.orchestrate.space` and to `render.py` (a 2-D array, another
  memory level).
- **A bigger space:** `flow: {orchestrate: gradient}` or a list of phases, or `llm` to let a model
  propose points ([docs/cookbook.md](../../../docs/cookbook.md)).
- **Another cost model:** `make_evaluator("timeloop")` in `measure.py` (it needs Docker, or
  `nix develop .#timeloop`).
