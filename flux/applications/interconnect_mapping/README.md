# interconnect_mapping -- interconnects evaluated WITH mapping functions

A 32-bank single-ported L1 (128-bit rows) serves 28 read + 24 write ports across three
units (MU 20R/16W with a 16/8/4 operand split, VU 4R/4W, DMA 4R/4W). Data are RxCxL
tensors in 12 storage modes (row/col/loop orders, 2x2 and 4x4 blocks, vectors), dims
known only at runtime, written with one tile shape and read with another. The study
asks: which (bank_id, line_id) hash, placement policy, schedule, and switching fabric
form the Pareto front over four costs -- area (A), storage padding (B), average access
latency (C), throughput (D)?

```bash
cd flux
nix develop --command flux task run applications/interconnect_mapping \
    --screen-only                                            # no model, about two minutes
# model-proposed hashes: copy the document, set `params.llm_rounds: 6`, run it with a model
```

The study is the document and the commands of `flux_imapping.steps` (D800) -- no world. A
candidate is a pair, written as JSON (a catalog policy by name, any other as its XOR taps; the
fabric's fields); `orchestrate: {command: steps search ...}` proposes them a round at a time --
the cross product of the policy field and the fabrics little loop A found, the model's taps
(`llm_rounds`), then the big loop's rounds -- and ends with the conclusion: the balanced pick,
the corners, the consensus fabric, the certificates by exhaustion over the front.

The record goes to `applications/interconnect_mapping/out/interconnect_mapping.db` unless
`--db` says otherwise. The knobs are the document's `params:` -- `seed`, `ops`,
`vu_probability`, `dma_probability` (the traffic), `climb_rounds` (the XOR-tap hill-climb),
`llm_rounds` (0 = no model), `coordination_rounds`, `certify_tiles`, `bank_bits`. Another
seed or regime is other traffic, so a copy that changes them is a copy of the folder: its name is its id.

## Two little loops, one big loop

The mapping can reduce conflicts, which changes what the interconnect experiences --
so neither half is searched alone:

- **The mapping loop** (bankmap-style): for one FIXED fabric, climb the injective XOR
  hash space against that fabric's own capacity tree. A hash tuned on the ideal
  crossbar has never felt a subtree capacity; one tuned here spreads traffic the way
  THIS topology needs. Results are named `S6-xor@<fabric>`.
- **The interconnect loop** (interconnect-app-style): for one FIXED mapping, rightsize
  a fabric's per-level capacities to the residual traffic that mapping leaves --
  measured peaks, so zero blocking is added on train traffic and the removed links
  shrink the area. Results are named `<fabric>-fit`. Under saturating traffic nothing
  is over-provisioned and the loop honestly returns nothing.
- **The big loop**: block-coordinate descent from the current Pareto front -- tune
  mappings for the front's fabrics, fit fabrics for the front's mappings, re-score
  everything identically, repeat until the front stops moving (`params.coordination_rounds`).

Measured effect (seed 0): all three coordinated pairs reached the front, and the
knee-point balanced pick IS one of them -- the per-fabric hash beats the ideal-tuned
hash on its own fabric.

## What the full study does

1. **A curated field of six solutions**, each honest about which of the three conflict
   categories it targets (intra-operand, intra-unit, system), what metadata a real
   system must carry for it (tensor descriptors, group ids, slot schedules), and what
   it assumes (compiler passes, padding, latency):
   S0 modulo baseline, S1 global XOR fold, S2 metadata swizzle (per-tensor pitch bits),
   S3 bank-group partition (space separation), S4 unit time-slots + fetch buffers
   (time separation), S5 pitch-pad skew (odd row pitch, Cost B paid and measured).
2. **Search extends the field**: a hill-climb over injective XOR tap sets, and
   optional LLM rounds -- every proposal passes the injectivity gate or is refused
   with the reason.
3. **Anti-overfitting is structural**: solutions tune on TRAIN workloads (seeded
   GEMM/VU/DMA traffic with operation info) and are judged ONLY on a disjoint HOLDOUT
   split; both numbers print, so a memorized hash exposes itself.
4. **Functional consistency is a gate, not a hope**: (bank, line) injectivity is
   decided exactly (GF(2) low-submatrix invertibility), and the hash may depend only
   on per-tensor metadata, never the tile -- so write-with-one-tiling,
   read-with-another cannot corrupt data by construction.
5. **Proofs, both directions**: conflict-freedom claims are certified by exhaustion
   over every tile origin in the bounded runtime domain (dims <= 64 -- finite, so
   exhaustion is a proof); failures come back as concrete counterexamples (the report
   shows a 4x16 tile defeating both the swizzle and the skew at difference 17 -- no
   universal static hash exists, which is the answer to the problem statement's
   universality question). Pigeonhole floors print beside measured latencies:
   64 rows through 16 ports is >= 4 cycles under ANY design.

## Files

- `lib/src/flux_imapping/model.py` -- geometry: 12 storage modes, linearization,
  tile -> bank-row sets (every downstream number is arithmetic over this)
- `lib/src/flux_imapping/conflict.py` -- the three conflict categories as cycle counts
- `lib/src/flux_imapping/workloads.py` -- seeded operation traffic, train/holdout split
- `lib/src/flux_imapping/solutions.py` -- the field, injectivity gate, fabric pricing
- `lib/src/flux_imapping/flow.py` -- study loop, hash search, certificates, Pareto
- `lib/src/flux_imapping/steps.py` -- the document's phases as commands (D800): `search` (the rounds below, a pass each), `check` (the hash is injective), `score` (the cycle law on train and holdout), `phys` (Yosys + OpenSTA on the pair's blocks)

Area is a structural gate-unit score (identical rules for every candidate: ranking,
not um2); the interconnect application's whole-fabric OpenROAD flow is the
upgrade path for frontier rows and the fmax>600 MHz check. Hash RTL comes from
`flux_bankmap.mapping.verilog()` -- a few XOR gates on each port's address path.
