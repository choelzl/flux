# Design: a loop driven by agents

Status: proposed (D630). Nothing here is built yet, except where a section says it exists.

## The idea

An orchestrator agent runs each pass. It hands each box of the drawing to a coding sub-agent
(`opencode`, `claude`, `codex` or a command of your own). The boxes that establish facts stay
real tools and are never delegated: the gate, the measurements and the record.

```yaml
flow:
  orchestrate: {agent: claude}          # picks the next work item, every pick on the ledger
  plan:        {agent: claude}          # writes the pass's plan, checked by check_plan
  dse:         {agent: codex}           # proposes points in `space`
  generate:    {agent: opencode}        # writes the artifact (exists: D575)
  critique:    {agent: claude}          # objects to a division, a part or a decision
  extract:     {agent: claude}          # mines lessons from the record, citing its rows
  select:      {agent: claude}          # chooses among designs that tie on the objective vector
  test: gate                            # never delegated (D460)
```

Every box keeps its rules half. An agent that is missing, times out, or returns something
invalid falls back to that half, and the fallback is noted on the ledger. A run with no agent
on PATH gives the same answer it gives today.

## What exists

| box | today | agent half |
|---|---|---|
| generate | model, command, catalog | `{agent: ...}` (D575, D585, D618) |
| orchestrate | rules, given, llm | `agent`: a *tool-calling model*, not a coding agent (D505) |
| plan | a plan file | `--agent plan`: a tool-calling model (D577) |
| validate, critique | rules, llm | none |
| dse | sweep, montecarlo, anneal, gradient, genetic, pareto, as phases | `llm` (the model proposes points) |
| extract, knowledge | mined, digest | none |
| select | the objective vector | none |

`flux_loop.agent` already runs an agent turn. `AgentSpec` holds the presets, the command, the
resume command and the output format. `converse` handles the brief, questions, resuming a
session, and the fresh session on context overflow. This design reuses it for every box.

## One contract for every box

A box turn is a file exchange in a work directory under the pass's trace directory:

| file | written by | content |
|---|---|---|
| `BRIEF.md` | loop | what the box decides, the rules it must follow, the output schema |
| `in/*.json`, `in/*.md` | loop | the box's inputs: standings, the menu, history, the frontier, record rows |
| `out.json` (or `artifact.*` for generate) | agent | the decision, with `why` |

The agent may read the repository and run `flux report`, `flux task check` and
`flux rtl proto`. It may not run the gate or a stage on its own behalf: the loop measures what
it chooses.

The loop:

1. Writes the brief and the inputs.
2. Runs the turn: `converse(spec, ...)`, with the box's time limit and the `questions:` policy.
3. Reads `out.json` and validates it against the box's schema and rules (see the table below).
4. On an invalid answer, resumes the session once with the reason. On a second failure, uses
   the rules half.
5. Records an `agent_turn` row: box, agent, brief digest, answer, verdict, seconds, and whether
   it fell back. `flux report` lists them.

This is the shape generate already has (brief, artifact, gate, repair), applied to decisions
instead of artifacts.

## The boxes

| box | the agent answers | the loop checks | falls back to |
|---|---|---|---|
| validate | objections to the document, each with the key it concerns | keys exist; advisory only, never refuses | rules |
| orchestrate | the next item, chosen from the menu, with a reason | the item is on the menu | rules |
| plan | a plan (parts, order, method, budgets) | `check_plan`, as today | the document's order |
| dse | points in `space`, with the reason for each | every knob and value is in `space`; not measured before; count ≤ the batch | the phase's policy |
| generate | the artifact | build, gate, repair (exists) | the model |
| critique | `{ok, why}` on a division, a part or a decision | an objection is not a veto: `critique_rounds` bounds it (D433) | no critique |
| extract | lessons, each citing record rows by id | every cited row exists and says what the lesson claims about its metric | mined |
| select | the pick among the designs that tie on the vector, with a reason | the pick passed the gate and meets every goal; otherwise the vector's pick stands | the vector |

Delegable: validate, orchestrate, plan, dse, generate, critique, extract (with knowledge
reading its lessons), and select.

Never delegated, refused at load with the D460 reason: test, analytical, simulation,
calibrate, records. Feedback stays human.

`select` is the most sensitive box. The objective vector decides. The agent only breaks ties
and chooses along a Pareto front the document leaves open. Its reason goes on the decision
row, so a reader sees why this design and not its neighbour.

## The orchestrator as a session

Today's agent orchestrator is a tool-calling model with `standings()`, `history(part)`,
`decisions()` and `knowledge()`. The coding-agent orchestrator gets the same four as files in
`in/` and one session per pass. Each pick resumes the session with what changed since the last
pick, so the agent keeps its reasoning across the pass without re-reading everything.

A pass stays loop-driven: `run_loop` calls the orchestrator for the next item, as it calls any
orchestrator. The agent decides; the loop executes, measures and records. That keeps these
properties:

- The ledger has every pick.
- `flux stop` works.
- A crash resumes from the record.
- The agent cannot claim a result the tools did not measure.

The other shape, an agent that runs `flux task run --passes 1` and edits the document between
passes, already exists as `flux ask --author` (D586). It works at the level of the problem,
not the pass.

## Software tuning (C/C++)

Nothing here is specific to RTL. For a C/C++ kernel:

- `language: cpp`
- the gate is the kernel's tests
- the stages are the compile, then a timed benchmark (`workers: 1`)
- `space` holds the knobs: unroll factor, tile sizes, loop order, `#pragma omp` schedule,
  branch hints, vector width

Two boxes do the work:

- `dse: {agent: ...}` proposes points in that space.
- `generate: {agent: ...}` does the rewrites a knob cannot say: fusing loops, swapping
  instructions, making a branch branch-free.

The gate keeps every rewrite equal to the reference.

## How to build it

1. **Loader.** `flow.<box>: {agent: <spec>}` is accepted for the delegable boxes and refused
   for the others. `agent_spec` validates the spec. Unit tests.
2. **The box turn.** Add `flux_loop.agent.box_turn(box, spec, brief, inputs, schema, check)`,
   which returns the answer or a fallback with its reason. It does steps 1-5 of the contract.
   Test it with the fake agent script `test_coding_agent` already uses: valid, invalid then
   repaired, invalid twice, timeout, missing binary.
3. **Critique and validate.** Both are advisory and small, so they are the first two boxes. An
   `AgentCritic` sits behind `problem.critique`, and `problem.objections` has an agent half.
4. **Orchestrate.** A coding-agent `AgentOrchestrator`: the session per pass, picks checked
   against the menu.
5. **DSE.** An `agent` phase in `flux_loop.dse`, beside `llm`, with the same point checks.
6. **Extract and select.** Lessons with row citations; the tie-break with its reason on the
   decision row.
7. **Plan.** `--agent plan` accepts a coding agent; `check_plan` is unchanged.
8. **Report and docs.** The `agent_turn` rows appear in `flux report`. Update the author
   reference, agent-surface and the website guide. One D-entry per step.
9. **Live check.** mul8 with `orchestrate`, `critique` and `select` on opencode against the
   hosted model: the decision must stand on the numbers. Then a small C kernel with
   `dse: {agent: ...}`.

Steps 1-3 make a vertical slice: one advisory box end to end, with the record and the
fallback. Each later step is one box.

## Questions for Cedric

- **Session lifetime.** One session per box per pass (proposed), or one per campaign? A campaign
  session remembers more, but it grows until it overflows and gets harder to reproduce.
- **`select`.** Should the agent only break ties (proposed), or may it overrule the vector with
  a reason the report shows?
- **Agent budget.** Should there be a cap on agent seconds per pass, beside `steps`? Agent turns
  take minutes; a model turn takes seconds.
