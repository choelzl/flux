# Prefetcher: ChampSim L2 studies

**The problem.** A cache prefetcher guesses which memory a program will need next. This
application finds the L2 prefetcher setup that runs three 5G baseband workloads fastest, then the
smallest setup that keeps most of that speed. Every number comes from a ChampSim simulation.

There are two documents:

| document | what the model writes |
|---|---|
| `prefetcher.problem.yaml` | a ChampSim `.ini` file: Bingo, the partner prefetchers beside it, and every knob |
| `invent.problem.yaml` | a new prefetcher in C++, one header, measured beside Bingo |

## Run it

```bash
flux task check applications/prefetcher/prefetcher.problem.yaml
flux task run applications/prefetcher/prefetcher.problem.yaml --tui
flux task run applications/prefetcher/invent.problem.yaml --tui
```

## What the document says (`prefetcher.problem.yaml`)

| key | value |
|---|---|
| `knowledge` | `knobs.md` (every knob: meaning, range, shipped value, legality, storage) and `bingo_default.ini` |
| `gate` | `bingo.py check`: refuses an illegal file with the reason; the model repairs it |
| `stages` | `bingo.py measure`: `screen` at 10M+15M instructions, `confirm` at 100M+150M |
| `objectives` | most `geomean_speedup`, keeping 90% of the best gain (`keep: 0.9, above: 1.0`); then least `storage_bytes` |

Between passes the loop sends the measured setups back with their numbers and asks for better
ones. For a hard storage limit, add `--max-storage B` to the gate command in a copy of the
document. To have a coding agent write the file, set `flow: {generate: {agent: opencode}}`.

## Inventing a prefetcher (`invent.problem.yaml`)

The model writes one C++ header subclassing ChampSim's `Prefetcher`.

- `flux champsim build` compiles it; the first compiler error goes back to the model.
- `flux champsim check` runs it on one trace and refuses one that issues no prefetches.
- `flux champsim run` measures it beside Bingo's shipped configuration.

## What it needs

- The tool shell (it brings ChampSim).
- An AI model or a coding agent.
- Three 5G traces, about 380 MB, **not in git**. Put `fdd_su_v1_0`, `tdd_dl_mu_v1_0` and
  `tdd_ul_mu_v1_0` (each `.simout_champsim.gz`) in `applications/prefetcher/traces/`. Without them
  every measurement fails with `no *.gz or *.xz traces in ...`.
- Time: one full-length simulation takes about 6 minutes; one setup is three simulations.
