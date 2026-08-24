# Omni

**One prompt, the whole toolbox.** Omni is not another domain loop; it is the loop that picks
loops. Give it a task in prose and it plans against the *entire* Flux tool surface -- every
evaluator, search axis, generation flow, and application loop the MCP interface exposes --
executes the plan for real, and concludes only from what actually ran.

Omni is the deliberate inversion of the other demos: the model decides the inner
generator-to-evaluator flow, and the [canonical shape](../guide/loop-shape.md)
constrains only the rim -- the gates every step passes, the record every measurement
lands in, and the report that quotes nothing it did not run.

```bash
# without a model (the demo contract): replay a canned or recorded plan
# a saved plan, no model: copy the document and set `params.plan_file`
nix develop --command flux task run applications/omni/omni.problem.yaml --db demo-omni.db

# with a model: the agent plans round by round
nix develop --command flux task run applications/omni/omni.problem.yaml --db demo-omni.db --tui   # params.prompt: "Find the array width \
  in {4,8,16,32} that minimizes latency for the bundled GEMM workload on zigzag."

# see what the agent sees
nix develop --command flux task check applications/omni/omni.problem.yaml          # the catalog it plans over
```

## How it works

1. **Catalog by introspection, never by hand.** The tool list, signatures and docstrings are
   replayed from the MCP server's own registrations, so what the agent plans over is exactly
   the real surface. A hand-list would rot; there isn't one. (The
   [tool catalog](../catalog/index.md) on this site is generated from the same introspection.)
2. **Typed plans, validated before execution.** The model answers in JSON steps
   (`{tool, args, bind}`), later steps reference earlier results, and every step is checked
   against the introspected signature first. A bad step is a refusal with the exact reason,
   fed back verbatim for repair -- never a crash, never a silent skip.
3. **The model plans; tools measure.** Results reach the model as truncated summaries; the
   conclusion it writes is checked against nothing but its own executed evidence, and a budget
   stop (rounds, calls, wall clock) reports itself honestly as not done.
4. **Every run is a replayable artifact.** The run record holds the prompt, the offered
   catalog, each raw model reply, the executed plan, and full results -- and loads straight
   back into `params.plan_file`, so any model-authored run re-executes deterministically with no model.
   Replay is the same executor, not a degraded mode.

Omni never offers *itself* in its own catalog: no recursive self-dispatch.
