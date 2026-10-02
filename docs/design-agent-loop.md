# A loop driven by agents

Status: built (D640), on the design of D630. Every delegable box takes `{by: <agent>, ...}` (D795).

## The idea

An orchestrator agent runs each pass. It hands each box of the drawing to a coding sub-agent
(`opencode`, `claude`, `codex` or a command of your own). The boxes that establish facts stay
real tools and are never delegated: the gate, the measurements and the record.

```yaml
flow:
  orchestrate: {by: claude}          # picks the next work item, every pick on the ledger
  plan:        {by: claude}          # writes the pass's plan, checked by check_plan
  orchestrate:         {by: codex}           # proposes points in `flow.orchestrate.space`
  generate:    {by: opencode}        # writes the artifact (exists: D575)
  critique:    {by: claude}          # objects to a division, a part or a decision
  knowledge:   {lessons: {by: claude}}   # lessons mined from the record, citing its rows
  select:      {by: claude}          # chooses among designs that tie on the objective vector
  test: gate                            # never delegated (D460)
```

Every box keeps its rules half. An agent that is missing, times out, or returns something
invalid falls back to that half, and the fallback is noted on the ledger. A run with no agent
on PATH gives the same answer it gives today.

## What each box does with an agent

| box | the agent | where |
|---|---|---|
| generate | writes the artifact (D575) | `flux_loop.agent` |
| validate | objects to the document, advisory | `PromptProblem.objections` |
| orchestrate | picks the next part, kind of work or improve step from the menu | `AgentOrchestrator(coding=...)` |
| plan | writes the pass's plan, checked by `check_plan` | `plan._plan_by_agent` |
| dse | proposes new points of the space | `ModelSearch(agent=...)`, or `agent:` on an `llm` phase |
| critique | objects to a division, a part or the decision; never vetoes | `PromptProblem.critique` |
| extract | writes lessons from the record's rows, each citing its rows | `boxes.AgentLessons` (a knowledge source) |
| select | chooses among designs the objectives cannot separate | `loop._select` |

## One contract for every box

A box turn is a file exchange in a work directory under the pass's trace directory:

| file | written by | content |
|---|---|---|
| `BRIEF.md` | loop | what the box decides, the rules it must follow, the output schema |
| `in/*.json`, `in/*.md` | loop | the box's inputs: standings, the menu, history, the frontier, record rows |
| `out.json` (or `artifact.*` for generate) | agent | the decision, with `why` |

The agent reads, searches, computes and writes; it does not compile, simulate, synthesize or
test (D673, D674). The presets deny those commands (`DENIED` in `flux_loop/agent.py`). The loop
runs the gate and the stages on what the agent writes and brings the output back to its session.
Inside a turn it may check its file through the loop's own gate and stages with `flux probe`,
within a budget. Each probe is on the record as the agent's own check, not a measured candidate
(D678).

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
| dse | points in the space, with the reason for each | every knob and value is in `flow.orchestrate.space`; not measured before; count ≤ the batch | the phase's policy |
| generate | the artifact | build, gate, repair (exists) | the model |
| critique | `{ok, why}` on a division, a part or a decision | an objection is not a veto: `critique_rounds` bounds it (D433) | no critique |
| extract | lessons, each citing record rows by id | every cited row exists and says what the lesson claims about its metric | mined |
| select | the pick among the designs that tie on the vector, with a reason | the pick passed the gate and meets every goal; otherwise the vector's pick stands | the vector |

Delegable: validate, orchestrate, plan, dse, generate, critique, extract (with knowledge
reading its lessons), and select.

Never delegated, refused at load with the D460 reason: test and calibrate. The stages (and
their estimates, D665) and the record are not boxes at all. Feedback stays human.

`select` is the most sensitive box. The objective vector decides. The agent only breaks ties
and chooses along a Pareto front the document leaves open. Its reason goes on the decision
row, so a reader sees why this design and not its neighbour.

## The orchestrator

Today's model orchestrator is a tool-calling model with `standings()`, `history(part)`,
`decisions()` and `knowledge()`. The coding-agent orchestrator gets what `standings()` and
`decisions()` return in its brief, and answers each pick in a fresh turn.

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
- `flow.orchestrate.space` holds the knobs: unroll factor, tile sizes, loop order, `#pragma omp` schedule,
  branch hints, vector width

Two boxes do the work:

- `orchestrate: {by: ...}` proposes points in that space.
- `generate: {by: ...}` does the rewrites a knob cannot say: fusing loops, swapping
  instructions, making a branch branch-free.

The gate keeps every rewrite equal to the reference.

## How it was built, and where it differs from the proposal

- **A fresh turn each time.** Each box turn has its own directory (`agents/<box>/NNN/`) and a
  brief that carries what the tools would return (standings, earlier picks); there is no session
  kept across a pass. Simpler, and a turn can be replayed from its directory.
- **`select` breaks ties only.** The vector's pick stands unless designs sit within every
  objective's tie band of it (at the goal when it is), or, with no goal, on the non-dominated
  front. The agent's reason joins `decided_by`.
- **No separate agent time budget.** Each turn has the agent spec's `timeout_s` (default 1800 s).
- **The record.** Every turn is a `decided:agent_turn` event: box, agent, answered or fell back,
  seconds, why. `flux report` lists them.
