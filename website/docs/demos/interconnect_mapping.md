# Interconnect mapping

**Which (bank, line) hash, placement policy, schedule and switching fabric form the Pareto
front over four costs -- area, storage padding, average access latency, throughput?**

A 32-bank single-ported L1 serves 28 read + 24 write ports across three units (a matrix unit,
a vector unit, DMA). Data are tensors in 12 storage modes -- row/column/loop orders, blocked
layouts, vectors -- with dimensions known only at runtime, written with one tile shape and
read with another.

```bash
nix develop --command python3 applications/interconnect_mapping/demo.py            # no model, ~1 min
nix develop --command python3 applications/interconnect_mapping/demo.py \
    --llm-round 6 --plot imapping-progress.svg                                     # + model hashes
```

## Two little loops, one big loop

The mapping can reduce conflicts, which changes what the interconnect experiences -- so neither
half is searched alone:

- **The mapping loop**: for one fixed fabric, climb the injective XOR hash space against that
  fabric's own capacity tree. A hash tuned on the ideal crossbar has never felt a subtree
  capacity; one tuned here spreads traffic the way *this* topology needs.
- **The interconnect loop**: for one fixed mapping, rightsize a fabric's per-level capacities
  to the residual traffic that mapping leaves -- measured peaks, so zero blocking is added on
  train traffic while removed links shrink the area.
- **The big loop**: block-coordinate descent from the current Pareto front -- tune mappings for
  the front's fabrics, fit fabrics for the front's mappings, re-score everything identically,
  repeat until the front stops moving (`--coord`).

Measured effect: all three coordinated pairs reached the front, and the knee-point balanced
pick *is* one of them -- the per-fabric hash beats the ideal-tuned hash on its own fabric.

## What keeps it honest

- **Injectivity is a gate, not a hope**: (bank, line) injectivity is decided exactly over
  GF(2), and the hash may depend only on per-tensor metadata, never the tile -- so
  write-with-one-tiling, read-with-another cannot corrupt data by construction.
- **Anti-overfitting is structural**: hashes tune on train workloads and are judged only on a
  disjoint holdout split; both numbers print, so a memorised hash exposes itself.
- **Proofs, both directions**: conflict-freedom claims are certified by exhaustion over every
  tile origin in the bounded runtime domain; failures come back as concrete counterexamples.
  One such counterexample -- a 4x16 tile defeating both a metadata swizzle and a pitch-pad
  skew -- answers the universality question: no universal static hash exists. Pigeonhole
  floors print beside measured latencies: 64 rows through 16 ports is at least 4 cycles under
  *any* design.

The curated field of six solutions (modulo baseline, global XOR fold, metadata swizzle,
bank-group partition, unit time-slots, pitch-pad skew) is each honest about which conflict
category it targets and what metadata a real system must carry for it; search then extends the
field with an XOR-tap hill-climb and optional model rounds, every proposal passing the
injectivity gate or refused with the reason.
