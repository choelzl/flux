# prefetcher/ — Bingo L2 prefetcher configuration DSE

Find the L2 prefetcher configuration that runs three 5G baseband workloads fastest, then find the
smallest one that keeps most of that speed. Every speedup here is measured by a real ChampSim
simulation, not modelled.

```bash
nix develop --command python applications/prefetcher/demo.py --local --budget 12
```

## The problem

The design space is the **Bingo** spatial prefetcher (Bakshalipour et al., HPCA 2019) sitting in
the L2 of a simulated out-of-order core. Eleven knobs: a spatial region size, four table sizes,
three field widths, the PHT's associativity, and a confidence threshold. Two stages, as the
project that arrived with this posed them:

1. **Maximise** geomean IPC speedup over the no-prefetcher baseline.
2. **Minimise** hardware storage while holding at least 90% of stage 1's speedup.

Stage 2's floor is a constraint, not a preference. A configuration that is smaller and drops below
it is refused with a reason, never offered as a trade-off.

## What it costs, measured

| | |
|---|---|
| one simulation (100M warmup + 150M simulated) | **~6 minutes** |
| one configuration (three traces) | three simulations |
| rebuilding the simulator from source | **7 seconds** |

That last row is the surprising one and it shapes what is worth searching. Recompiling is 0.6% of
one evaluation, so the prefetcher's *source* is a reachable design space too, not just its knobs.
This study searches the knobs; D349 records why, and what the code-space version would need.

Concurrency does not scale linearly: this is a cache simulator and it is memory-bandwidth bound,
so twelve concurrent runs each take roughly twice their solo time. More parallelism still helps,
just not proportionally.

## Confirmed standings

Every number here is at full length (100M + 150M instructions), on the same binary, against the
same no-prefetcher baseline (0.69602 / 0.99071 / 0.80232). Screened numbers are not listed: they
order candidates and must not be quoted (D351).

The table is a frontier, not a ranking: storage is the second axis (D362), and the rows are
ordered by it. Each row is faster than every row above; the last column is what its extra
storage bought.

| configuration | geomean | storage | that step buys | how it was found |
|---|---|---|---|---|
| shipped `bingo.ini` | 1.0439 | 35,096 B | | the incumbent |
| bingo + sms + stride, defaults | 1.0515 | 35,096 B | +0.0076 for 0 B | composition alone |
| **bingo + invented2, tuned** | **1.0626** | **97,208 B** | +0.0111 for +62 KB | 20 model proposals, an invented partner, stage 2 from 98 KB |
| bingo + sms + stride, tuned | 1.0640 | 124,048 B | +0.0014 for +27 KB | 10M+15M screen (D353) |
| bingo + sms + invented2, tuned | 1.0671 | 206,496 B | +0.0031 for +82 KB | 20 model proposals, an invented partner, stage 2 from 800 KB |
| bingo + invented2, tuned | 1.0703 | 407,808 B | +0.0032 for +201 KB | a climb run's confirmed frontier (D368's second sample) |

The bold row is the one this project prefers: past 97 KB the curve is nearly flat, and the last
109 KB buy 0.0045. Both runs are the study working as designed -- the model proposed the Bingo
configuration, compose picked an LLM-designed prefetcher as a partner, stage 2 shrank the
pattern table at no confirmed cost -- and both decisions are correct under the retention floor;
they differ because stage 1 climbed to different sizes. `--max-storage 96k` makes the preference
a constraint the search obeys from the first wave.

`--strategy pareto-uct` replaces stage 1's hill-climb with a Pareto-UCT tree (D368, after
MicroEvo, arXiv:2608.06183): the wave goes to the measured configuration whose branch has
been buying the most (speedup, storage) hypervolume, with crowding pulling toward the
frontier's gaps. Each wave is half-ordered by a free rollout estimate (the nearest measured
configurations' vote), so the simulations go to promising moves rather than merely near ones;
estimates order, only measurements are recorded. Same budget, same moves, same gates; only
the allocation differs, and the default stays the climb.

A resumed campaign (same `--db`, same objective) leads its seed pool with the best
configurations it has already measured and shows them to the first proposer call, so it starts
where it left off rather than where it started (D367). It also tells that call what the record
TAUGHT: every one-knob pair the campaign has measured is a controlled experiment, and their
aggregated directions ("pht_size up: +0.0095 over 2 pairs") reach the prompt as arithmetic
computed fresh from the store -- directions, not instructions (D369).

`invented/` holds every prefetcher the model has written that compiled, winner or not, with the
stack it was measured beside. Designs that earned a place (a gain beside their reference stack)
go on the compose menu; `--invent N` writes N more during a run, each asked to beat the tallest
stack those records can vouch for, re-measured (D361). Measurements are cached across rebuilds:
a design's number is keyed on the stock simulator and the digest of each invention its stack
enables, so adding or dropping a design from the library does not discard what is known.

## Layout

| | |
|---|---|
| `demo.py` | the command line; calls the CHIA loop |
| `lib/src/flux_prefetcher/` | the domain: `config` (space + storage model), `space` (legal moves), `objective` (what a measurement means), `flow` (the study), `propose` (asking a model) |
| `traces/` | the three workloads, ~380 MB, not in git |
| `baseline/reference_ipc.json` | the no-prefetcher IPC the project recorded, used as a drift check |
| `experiments/` | studies that measure this study, e.g. whether a short run ranks like a full one |

The evaluator lives at `evaluator/champsim_bingo/` with every other evaluator, and the loop at
`interfaces/chia_nodes/prefetcher_dse_loop.py` with every other CHIA node.

## Two things that will bite

**The simulator comes from nix.** `nixchip.packages.pythia` builds CMU-SAFARI/Pythia at the
MICRO'21 fork and installs the whole tree under `$out/share/pythia`, with the binary symlinked to
`$out/bin/pythia`, so `nix develop .#python` puts a working simulator on PATH and the study needs
nothing checked in. It is the `multi multi no 1` build, which reaches the L1D prefetcher axis as
well as the L2 one.

That package was adopted only after it reproduced the recorded no-prefetcher baselines EXACTLY —
0.69602 / 0.99071 / 0.80232, zero delta on all three at full length. A simulator that shifts the
denominator would invalidate every speedup this study has recorded, so "it builds and runs" was
not the acceptance test.

The evaluator still looks in four places (`--champsim-bin`, `$FLUX_CHAMPSIM_BIN`, `PATH`, then an
in-tree `proj/` build) and names all four when it finds nothing; the last exists only for a
checkout that predates the pin.

**An illegal configuration aborts the simulator, it does not score badly.** `bingo.cc` opens with
an `assert` that `region_size >> 6 == pattern_len`. So `flux_prefetcher.config.validate` is a
correctness gate, not an optimisation, and it runs before anything reaches ChampSim. The same
rules are stated in the proposer's prompt, because a model told the goal but not the rules
produces mostly-illegal candidates.

## Why the L2 slot says `multi`

The binary is built as `no multi no 1`: no L1D prefetcher, `multi` in the L2C slot, no LLC
prefetcher, one core. `multi.l2c_pref` reads `--l2c_prefetcher_types` at run time and iterates a
**vector**, so sixteen prefetchers are selectable without recompiling — `ampm bingo bop dspatch
ipcp mlop next_line power7 sandbox scooby sms spp_dev2 spp_ppf_dev streamer stride`. `scooby` is
the Pythia RL prefetcher itself.

**One flag per prefetcher, never a comma list.** `knobs.cc` does
`l2c_prefetcher_types.push_back(string(value))` on the whole value and never splits on commas, so
`--l2c_prefetcher_types=bingo,sms` registers one prefetcher named `bingo,sms` and ChampSim exits
with "unsupported prefetcher type". Repeat the flag instead. **And not every pair works**: some
combinations crash the simulator — `bingo+scooby` aborts, `next_line+sms` segfaults — while
`bingo+sms` and `bingo+ampm` run fine. Composition has to be searched with failures treated as
data, not assumed to be legal.

That axis is worth searching. `experiments/prefetcher_family.py` measures all sixteen alone:
Bingo wins at 1.0607, ahead of `next_line` 1.0378 and `scooby` 1.0366, so the study tunes the
right prefetcher. But `bingo+sms+ampm` reaches **1.0684** — better than any tuning of Bingo's
knobs found — and this study does not search composition at all.
