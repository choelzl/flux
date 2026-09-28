# Interconnect mapping

**The problem.** A 32-bank L1 memory serves 28 read and 24 write ports across three units (a
matrix unit, a vector unit, DMA). Data are tensors stored in 12 layouts, written with one tile
shape and read with another. Find the combinations of bank hash, placement, schedule and switching
fabric that give the best trade-offs between four costs: area, storage padding, average access
latency and throughput.

## Run it

```bash
flux task run applications/interconnect_mapping/interconnect_mapping.problem.yaml --screen-only   # no model, about two minutes
```

For model-proposed hashes, copy the document, set `params.llm_rounds: 6` and run it with a model.

## What the document says

| key | value |
|---|---|
| `params.seed`, `ops`, `vu_probability`, `dma_probability` | the traffic |
| `params.climb_rounds` | `40`: rounds of the XOR-hash hill-climb |
| `params.llm_rounds` | `0`: no model |
| `params.coordination_rounds` | `2`: rounds of tuning hash and fabric together |
| `objectives` | least `area_score`, `pad_fraction`, `holdout_latency`; most `holdout_throughput` (no goal: the balanced "knee" point wins) |
| `stages` | `analytic` (formulas), `phys` (Yosys and OpenROAD) |

## How it searches

- **Mapping loop:** for one fabric, climb the space of XOR hashes against that fabric's limits.
- **Interconnect loop:** for one mapping, shrink the fabric to the traffic that mapping leaves.
- **Joint loop:** alternate the two from the current best set until it stops changing.

Hashes are tuned on training traffic and judged only on separate holdout traffic. A hash that
could lose data (not one-to-one) is refused by the gate. Conflict-freedom claims are proved by
trying every tile position; failures come back as counterexamples.

## Results recorded

- All three jointly tuned pairs reached the best set, and the balanced pick is one of them.
- No single fixed hash works for every tiling: a 4x16 tile defeats both the swizzle and the skew.

## What it needs

No hardware tools for `--screen-only`; Yosys and OpenROAD for the `phys` stage; a model only
if `llm_rounds` is above 0.
