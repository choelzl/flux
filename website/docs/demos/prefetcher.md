# Prefetcher

**Find the L2 prefetcher configuration that runs three 5G baseband workloads fastest, then
find the smallest one that keeps most of that speed.** Every speedup is measured by a real
ChampSim simulation, not modelled.

```bash
nix develop --command python3 applications/prefetcher/demo.py --local --budget 12
```

The design space is the Bingo spatial prefetcher (Bakshalipour et al., HPCA 2019) in the L2 of
a simulated out-of-order core: eleven knobs covering a spatial region size, table sizes, field
widths, associativity and a confidence threshold. Two staged objectives:

1. **Maximise** geomean IPC speedup over the no-prefetcher baseline.
2. **Minimise** hardware storage while holding at least 90% of stage 1's speedup.

Stage 2's floor is a constraint, not a preference: a configuration that is smaller but drops
below it is refused with a reason, never offered as a trade-off.

## What it costs, measured

One full-length simulation (100M warmup + 150M instructions) takes about 6 minutes; one
configuration is three simulations; rebuilding the simulator from source takes 7 seconds. That
last number shapes what is worth searching: recompiling is 0.6% of one evaluation, so the
prefetcher's *source code* is a reachable design space too, not just its knobs.

## Confirmed standings

All numbers at full length, on the same binary, against the same no-prefetcher baseline.
Screened numbers order candidates and are never quoted. The table is a frontier, ordered by
storage: each row is faster than every row above, and the last column is what its extra
storage bought.

| configuration | geomean speedup | storage | that step buys |
|---|---|---|---|
| shipped `bingo.ini` (incumbent) | 1.0439 | 35 KB | |
| bingo + sms + stride, defaults | 1.0515 | 35 KB | +0.0076 for 0 B |
| **bingo + invented2, tuned (preferred)** | **1.0626** | **97 KB** | +0.0111 for +62 KB |
| bingo + sms + stride, tuned | 1.0640 | 124 KB | +0.0014 for +27 KB |
| bingo + sms + invented2, tuned | 1.0671 | 206 KB | +0.0031 for +82 KB |
| bingo + invented2, tuned | 1.0703 | 408 KB | +0.0032 for +201 KB |

The bold row is the one this project prefers: past 97 KB the curve is nearly flat, and the
last 109 KB buy 0.0045. "invented2" is a prefetcher a local model *wrote in C++* -- compiled
against the real simulator, measured beside the stack that vouches for it, and kept only
because it earned a confirmed gain.

## Invention and resumption

`--invent N` asks the model for N new prefetchers during a run, each challenged to beat the
tallest stack the records can vouch for, re-measured, never assumed. Compile failures are fed
back from the compiler's own first diagnostic -- affordable because a rebuild costs seconds
against minutes for one evaluation. A resumed campaign (same `--db`, same objective) leads its
seed pool with the best configurations already measured, and tells the first proposer call
what the record *taught*: every one-knob pair the campaign has measured is a controlled
experiment, and their aggregated directions reach the prompt as arithmetic computed fresh from
the store -- directions, not instructions.

`--strategy pareto-uct` replaces stage 1's hill-climb with a Pareto-UCT tree allocation
(following MicroEvo, arXiv:2608.06183): waves go to the branch that has been buying the most
speedup-storage hypervolume, with crowding pulling toward the frontier's gaps. Same budget,
same moves, same gates; only the allocation differs.
