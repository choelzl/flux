# Run a loop

Everything enters through `nix develop` from the `flux/` directory of a
[checkout](https://github.com/choelzl/flux). The first entry builds the toolchain; later
entries take seconds. Every loop is a problem document run by `flux task run`:

```bash
cd flux
nix develop --command flux task check applications/nlu/nlu.problem.yaml                          # answerable? tools present?
nix develop --command flux task run applications/nlu/nlu.problem.yaml --db demo-nlu.db --tui     # model-paced
nix develop --command flux task run applications/macarray/macarray.problem.yaml --db demo-macarray.db   # ~90 s
nix develop --command flux task run applications/bankmap/bankmap.problem.yaml --db demo-bankmap.db      # ~1 min
nix develop --command flux task run applications/prefetcher/prefetcher.problem.yaml --db demo-prefetcher.db   # ~40 min
nix develop --command flux task run applications/interconnect_mapping/interconnect_mapping.problem.yaml --db demo-imapping.db
nix develop --command flux task run applications/omni/omni.problem.yaml --db demo-omni.db       # params.prompt is the ask
```

Things worth knowing before running one:

- **The knobs are in the document.** Another ask -- other operators, a 1 GHz clock, 16
  lanes, other strides -- is a copy of the document with its `params:` changed and its own
  `campaign:`; there are no per-loop flags. `flux task run`'s own flags are the loop's:
  `--db`, `--steps`, `--passes`, `--tui`, `--think`, `--agent`, `--role`, `--replies`.
- **No model is required.** The roles a model would fill report themselves skipped and the
  rule-based halves run anyway.
- **A hosted model is opt-in.** `FLUX_LLM_REMOTE=1` with `FLUX_REMOTE_BASE_URL`,
  `FLUX_REMOTE_API_KEY` and `FLUX_REMOTE_MODEL` points every model role at an OpenAI-compatible
  server (LocalAI, llama.cpp, OpenRouter); a local Ollama is `FLUX_LLM_MODEL`. The first prompt
  that leaves the machine is announced, and a missing key falls back out loud. Thinking is
  decided per request.
- **NLU** and **macarray** measure real silicon (Yosys + OpenROAD on ASAP7, Verilator for
  correctness); a stage whose tool is absent is skipped and the report says so.
- **prefetcher** needs three 5G traces (~380 MB, not in git); the ChampSim simulator itself
  comes from the flake.
- **The record.** `--db PATH` accumulates measurements; a relaunch resumes from it and nothing
  measured is paid for twice. `flux report <db>` writes the campaign report (the fronts per
  pass, the best so far, the parts' ledger); `flux status`, `flux stop` and `flux attach`
  operate a long campaign; `flux migrate` upgrades an old record.
- **The TUI** (`--tui`) shows the roles, the current turn with its prompt, the timing tree and
  the results tab with the front; `f` types operator guidance into the next prompt.

## What a report looks like

A DECISION block first (the thing to build, every number from the measurement stage the report
names), then the front across the objectives, then WHAT THIS RUN ESTABLISHED / NOT
ESTABLISHED / REFUSED with reasons, then the cost. If a run proves the request impossible, the
proof and the nearest feasible answers *are* the report.
