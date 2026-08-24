# omni -- one prompt, the whole toolbox

The fifth Flux application is not another domain loop; it is the loop that picks loops.
Give it a task in prose and it plans against the *entire* Flux tool surface -- every
evaluator, search axis, generation flow, and application DSE loop the MCP interface
exposes -- executes the plan for real, and concludes only from what actually ran.

```
# without a model (the demo contract): replay a canned or recorded plan
python3 applications/omni/demo.py --plan applications/omni/plans/screen-and-compare.json

# with a model: the agent plans round by round
python3 applications/omni/demo.py --prompt "Find the array width in {4,8,16,32} that \
  minimizes latency for the bundled GEMM workload on zigzag; report width and latency."

# see what the agent sees
python3 applications/omni/demo.py --list-tools
```

## How it works

1. **Catalog by introspection, never by hand.** `flux_omni.catalog` replays
   `FluxTool.setup()` against a recorder, so the tool list, signatures, and docstrings
   are exactly the MCP surface (docs/agent-surface.md's "one definition" -- this is its
   fourth surface). A hand-list would rot; there isn't one. `--tools a,b,c` narrows the
   menu when a smaller model needs easier choices.
2. **Typed plans, validated before execution.** The model answers in JSON steps
   (`{tool, args, bind}`), later steps reference earlier results as `"$bind.field[0]"`,
   and every step is checked against the introspected signature first. A bad step is a
   `Refusal` with the exact reason, fed back verbatim for repair -- never a crash, never
   a silent skip.
3. **The model plans; tools measure.** Results reach the model as truncated summaries;
   the conclusion it writes is checked against nothing but its own executed evidence,
   and a budget stop (rounds, calls, wall clock) reports `done=False` honestly.
4. **Every run is a replayable artifact.** `omni_run.json` records the prompt, the
   offered catalog, each raw model reply, the executed plan, and full results. That
   file loads straight back into `--plan`, so any model-authored run re-executes
   deterministically with no model -- replay is the same executor, not a degraded mode.
5. **Three meta-tools** cover "generate files as needed": `write_file` (sandboxed to the
   run workdir), `load_ir` (bundled examples and workdir documents), `note`.

## Files

- `lib/src/flux_omni/catalog.py` -- introspected ToolSpecs from the MCP surface
- `lib/src/flux_omni/plan.py` -- Step/Refusal/Proposal, validation, reference resolution
- `lib/src/flux_omni/pilot.py` -- the round loop, the executor, provenance
- `plans/*.json` -- canned model-free plans (unit-tested against the live catalog, so a
  drifted tool signature fails in CI, not in a demo)
- `demo.py` -- `--prompt` (model), `--plan` (no model), `--list-tools`

Decision record: docs/decisions.md D377.
