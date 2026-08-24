# Run a demo

Everything enters through `nix develop` from the `flux/` directory of a
[checkout](https://github.com/choelzl/flux). The first entry builds the toolchain; later
entries take seconds.

```bash
cd flux
nix develop .#physical --command python3 applications/macarray/demo.py            # ~90 s
nix develop --command python3 applications/bankmap/demo.py \
    --strides 1 8 16 --concurrent 4 --banks 8                                     # ~1 min
nix develop --command python3 applications/interconnect_mapping/demo.py           # ~15 s
nix develop --command python3 applications/omni/demo.py \
    --plan applications/omni/plans/screen-and-compare.json                        # ~1 min
nix develop .#physical --command python3 applications/nlu/demo.py --llm-round 4  # model-paced
nix develop .#physical --command python3 applications/interconnect/demo.py        # ~15 min
nix develop .#physical --command python3 applications/prefetcher/demo.py          # ~40 min
```

Things worth knowing before running one:

- **No model is required.** Model roles use a local Ollama (`FLUX_LLM_MODEL` picks the tag);
  without one, proposer and invention phases report themselves skipped and the deterministic
  search runs anyway.
- **macarray** and **interconnect** measure real silicon (Yosys + OpenROAD on ASAP7, Verilator
  for correctness and throughput) and refuse to start outside the `.#physical` shell rather
  than fail one tool call at a time.
- **prefetcher** needs three 5G traces (~380 MB, not in git); the ChampSim simulator itself
  comes from the flake.
- Every demo takes `--db PATH`: measurements accumulate there and a re-run with the same
  objective resumes -- nothing already measured is paid for twice. Campaign-backed demos also
  write `<db>.progress.svg` beside the database: explored points in measurement order with the
  running best, and the objective space with the frontier stepped and the decision starred.
- `--help` on any demo explains its flags in full sentences.

## What a report looks like

A DECISION block first (the thing to build, every number from the measurement rung the report
names), then the frontier across both objectives, then WHAT THIS RUN ESTABLISHED / NOT
ESTABLISHED / REFUSED with reasons, then the cost. If a run proves the request impossible, the
proof and the nearest feasible answers *are* the report.
