# NPU GEMM: sizing an accelerator

**The problem.** The smallest accelerator that runs a two-layer feed-forward block (two matrix
multiplications, in `workload.yaml`) in at most 500 cycles. The designs are architectures, not
RTL: a script writes each one, and ZigZag, an analytical accelerator cost model, measures it. No
AI model is needed.

## Run it

```bash
flux task run applications/npu_gemm/npu_gemm.problem.yaml --passes 1
```

About 30 seconds for the 15 designs.

## What the document says

| key | value |
|---|---|
| `space` | `pe_x: [4, 8, 16, 32, 64]` (multipliers in the array), `gbuf_kb: [16, 64, 256]` (buffer) |
| `flow` | `dse: sweep`; `generate: {command: render.py ...}` writes each architecture |
| `gate` | `check.py`: the architecture is valid |
| `stages` | `model`: `measure.py` prints ZigZag's cycles and energy and an area estimate |
| `objectives` | `latency_cycles` at most 500, then least `area_mm2` |

## Result recorded

| PEs | buffer | cycles | area (mm2) |
|---|---|---|---|
| 4 | 16 KB | 3,120 | 0.096 |
| 8 | 16 KB | 1,560 | 0.112 |
| 16 | 16 KB | 653 | 0.144 |
| **32** | **16 KB** | **341** | **0.208** |

The decision is 32 PEs with 16 KB: the least area that makes 500 cycles. 64 PEs are not faster
on this workload, and a larger buffer only adds area.

## Changing it

- Another workload: replace `workload.yaml`.
- More knobs: add them to `space:` and to `render.py`.
- Another search: `flow: {dse: gradient}`, or `llm` to let a model propose points.

## What it needs

ZigZag (in the tool shell, or `pip install -e "./flux[zigzag]"`).
