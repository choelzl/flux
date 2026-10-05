# prefetcher: the L2 prefetcher for 5G baseband traces

Two problems on ChampSim (the `pythia` build in the dev shell), measured by `flux champsim` on
the three traces in `traces/` (not in git; see `traces/README.md`).

| file | what it is |
|---|---|
| `problem.yaml` | the configuration: the model writes a ChampSim `.ini` -- Bingo, the partners beside it and every knob |
| `knobs.md` | what an `.ini` may say: the stack, each knob's meaning, range and shipped value, legality, storage |
| `bingo_default.ini` | Bingo at its shipped configuration |
| `bingo.py` | `check` (legality, `--max-storage`) and `measure` (`flux champsim` + the storage model) |
| `invent.problem.yaml` | a new C++ prefetcher, written by the model, measured beside Bingo -- the loop's second problem, its record `prefetcher.invent` (D787) |

**The configuration.** The model reads `knobs.md` and `bingo_default.ini` and writes a knob
file. A knob it leaves out takes its shipped value. `bingo.py check` refuses an illegal file
with its reason (the model repairs it), the screen measures 10M+15M instructions, the finalists
100M+150M. The objectives: the most geomean speedup over no prefetcher, then the least storage
among the designs holding 90% of the best's gain. Between passes the loop sends the designs
back with their numbers and asks for better ones.

**Invention.** The model writes one header subclassing ChampSim's `Prefetcher`;
`flux champsim build` compiles it (a compile error is repaired), `flux champsim check` refuses
one that issues no prefetches, and the stages measure it beside Bingo's shipped configuration.

```bash
flux task check applications/prefetcher
flux task run applications/prefetcher --db demo-prefetcher.db --tui
flux task run applications/prefetcher/invent.problem.yaml --db demo-invent.db --tui
```

A coding agent can write either file instead of the model: `flow: {generate: opencode}`.
`bingo` beside `scooby`, `mlop` or `next_line` crashes; `knobs.md` says so.
