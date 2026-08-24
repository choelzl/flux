# Prefetcher

**Find the L2 prefetcher configuration that runs three 5G baseband workloads fastest, then
find the smallest one that keeps most of that speed.** Every speedup is measured by a real
ChampSim simulation, not modelled.

```bash
cd flux
nix develop --command flux task run applications/prefetcher/prefetcher.problem.yaml --tui
```

It needs the three 5G traces (~380 MB), which are **not in git**: without them every
measurement fails with `no *.gz or *.xz traces in ...`. They go in `applications/prefetcher/traces/` (`fdd_su_v1_0`, `tdd_dl_mu_v1_0`
and `tdd_ul_mu_v1_0`, each `.simout_champsim.gz`), or point the stages' `--traces` at another
directory in a copy of the document. The ChampSim simulator comes from the flake.

The application has no package of its own: a document and `bingo.py`, a script that renders a
point of the space as a ChampSim `.ini`, checks it (Bingo aborts on an illegal configuration)
and measures it through `flux_evaluator_champsim`, printing `geomean_speedup` and
`storage_bytes`. The design space is the Bingo spatial prefetcher (Bakshalipour et al., HPCA
2019) in the L2 of a simulated out-of-order core: ten knobs covering a spatial region size,
table sizes, field widths, associativity and a confidence threshold -- plus the stacks
(Bingo alone or beside partner prefetchers) and each partner's knobs, which move
only when their partner is in the stack (`when:`). Two objectives:

1. **Maximise** geomean IPC speedup over the no-prefetcher baseline.
2. **Minimise** storage, holding 90% of the best design's gain over 1.0
   (`{metric: geomean_speedup, keep: 0.9, above: 1.0}`).

The first is a constraint on the second, not a preference: a smaller design below it is
refused with a reason, never offered as a trade-off.

## The model writes the configuration

There is no `space:` in the document. The model reads `knobs.md` (the stack, every knob's
meaning, range and shipped value, legality, the storage model) and `bingo_default.ini`, and
writes a ChampSim knob file: which partners run beside Bingo and every knob it wants to change.
`bingo.py check` refuses an illegal file with its reason, and the model repairs it. The screen
measures 10M + 15M instructions and the finalists are re-measured at full length before
deciding. Between passes the loop sends the measured designs back with their numbers and asks
for better ones. Another ask is a copy of the document: add `--max-storage B` to the gate for
a hard storage budget, or `flow: {generate: {agent: opencode}}` to have a coding agent write the file.

## Inventing a prefetcher

`invent.problem.yaml` asks the model for a new L2 prefetcher in C++, one header against
ChampSim's `Prefetcher` class. The gate builds it into a private copy of the simulator
(`flux champsim build`, g++'s first diagnostic goes back to the model) and smoke-runs it
(`flux champsim check`); the stages measure it beside Bingo at its shipped configuration.

```bash
nix develop --command flux task run applications/prefetcher/invent.problem.yaml --tui
```

## What it costs, measured

One full-length simulation (100M warmup + 150M instructions) takes about 6 minutes; one
configuration is three simulations; rebuilding the simulator from source takes 7 seconds. That
last number shapes what is worth searching: recompiling is 0.6% of one evaluation, so the
prefetcher's *source code* is a reachable design space too, not just its knobs.

## Standings

Measured at full length, on the same binary, against the same no-prefetcher baseline. The table
is a frontier, ordered by storage: each row is faster than every row above, and the last column
is what its extra storage bought. "invented2" was a prefetcher a local model wrote in C++.

| configuration | geomean speedup | storage | that step buys |
|---|---|---|---|
| shipped `bingo.ini` (incumbent) | 1.0439 | 35 KB | |
| bingo + sms + stride, defaults | 1.0515 | 35 KB | +0.0076 for 0 B |
| **bingo + invented2, tuned (preferred)** | **1.0626** | **97 KB** | +0.0111 for +62 KB |
| bingo + sms + stride, tuned | 1.0640 | 124 KB | +0.0014 for +27 KB |
| bingo + sms + invented2, tuned | 1.0671 | 206 KB | +0.0031 for +82 KB |
| bingo + invented2, tuned | 1.0703 | 408 KB | +0.0032 for +201 KB |

Past 97 KB the curve is nearly flat: the last 109 KB buy 0.0045.
