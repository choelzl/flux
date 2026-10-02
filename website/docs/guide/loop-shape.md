---
hide:
  - navigation
---

# The loop

Every document runs through one loop. A design is proposed, written, checked, measured stage by
stage, and the best one is chosen. Every step is written to the record.

This is the loop as it runs when the document says nothing about it. The
[loop crafter](loop-crafter.md) shows the same drawing: there, a click on a box changes who does
the step.

<div id="flux-loop-drawing" class="flux-crafter">
  <noscript>The drawing needs JavaScript. The table below lists the same boxes, top to bottom.</noscript>
</div>

<link rel="stylesheet" href="../../assets/crafter.css">
<script src="../../assets/crafter.js"></script>

- **Solid arrows** are the path of a design, top to bottom.
- **Dashed arrows** feed a step: your notes, the background reading, the critic, the lessons.
- **Red dotted arrows** are the ways a design is refused. A failed check sends it back to be
  *repaired*; a critic's objection *sends it back*; a measurement short of the objective asks to
  *improve* it; a design that fails a measurement's gate (or is estimated to) is *dropped*.
- **At rest, explore**: when a round finds nothing new, the next one sends the best designs back
  to be improved. A run ends only when you stop it, or at `--passes N`.

## Words used here

| word | meaning |
|---|---|
| **document** | the `*.problem.yaml` file that describes one problem |
| **check** | a command that refuses a wrong design: a lint, a test against the golden model, a script of yours. Together the checks are the **gate** (`flow.test`) |
| **measurement** | one **stage** (`flow.measure`, by name), from cheap (synthesis, a formula) to costly (placement, a simulation) |
| **objective** | what "better" means: a number, up or down, optionally with a limit to reach |
| **round** | one **pass** of the loop; a run is a series of them |
| **record** | the database of every design, number and refusal (`out/<id>.db`) |

## The boxes

Each box is one step. Most can be done by *rules* (plain code), a *model* (an AI language model)
or a *coding agent* (Claude Code, Codex, OpenCode). Say who in the document's `flow:` block, one
key per box; a box you do not name keeps its default, the first choice listed.

```yaml
flow:
  orchestrate: sweep                     # Search the settings: try every combination
  generate: {by: claude}      # Make a design: a coding agent writes it
  critique: model                  # Second opinion: a model critic
```

| box | `flow:` key | what it does | choices, the default first |
|---|---|---|---|
| Check the document | `validate` | Before anything runs, the document is read for mistakes. | `rules`: the built-in checks · `model`: then a model reads it and objects · a coding agent |
| Plan the round | `plan` | Optionally writes a plan for the round before any work starts. | `off`: step by step · `model`: a model writes the plan · a coding agent |
| Pick the next job | `orchestrate` | Decides what to work on next. | unsaid: the model picks the next part, rules pick the kind of work · `rules`: no model · `model`: a model picks · `tools`: a model with tools picks · a coding agent. Left out when a search is on: the search picks. |
| Your notes | `feedback` | Notes you type while it runs steer the next round. Typed in the live screen (`--tui`, then `f`). | `human` · `off` |
| Search the settings | `orchestrate` | Walks the list of settings (the space) to choose which to try. With a space, the orchestrator picks points, not parts (D797). The settings are its `space` (`flow.orchestrate: {policy: sweep, space: {...}}`). | `none` · `sweep`: every combination · `montecarlo`: random samples · `anneal` · `gradient`: step towards better · `genetic`: breed the best · `pareto`: the trade-off front · `model`: a model proposes settings · a coding agent |
| Make a design | `generate` | Writes each candidate design. | `model`: a model writes it · `{command: "..."}`: your script writes it · a coding agent |
| Background reading | `knowledge` | What the model reads with every request. | unsaid: the library (your papers and notes, see [build your own](build-your-own.md#3-say-what-is-correct)) and the files the document lists · `none`: no library |
| Digest the papers | | Each paper of the library (library/ beside the document, and the shared one) is summed up once, in the Setup, and the summaries reach every prompt. Always, while the library is on. | `model` (unsaid): the model sums them up · a coding agent, written `knowledge: {by: opencode}` |
| Check it works | `test` | Runs your checks in order; a design that fails goes back to be repaired. Always yours, never a model's. | **fixed**: always your checks, said as `flow.test` |
| Second opinion | `critique` | Optionally, a critic questions the division into parts, each admitted part (sending it back) and the final choice. The three *Critic* boxes of the drawing. | `off` · `model`: a model critic · a coding agent |
| Measure | | Runs your measurements, cheapest first; a design that fails a gate is dropped. | **fixed**: always your measurements, said as `flow.measure`. Each may `estimate:` first and skip a design that cannot pass. |
| Compare measures | `calibrate` | Checks how well the cheap measurement predicts the costly one. | `on` · `off`; never a model's or an agent's |
| Choose the best | `select` | Picks the winner by your goals. | `objectives` · a coding agent breaks the ties they leave open |
| Keep a record | | Every design, measurement and refusal is kept, and read back when you resume. | **fixed**: always on |
| Learn from results | | Optionally turns past results into lessons for the next round, read with the library. | `off` · `mined`: lessons mined from the record, written `knowledge: {lessons: mined}` · a coding agent, `knowledge: {lessons: claude}` |

A coding agent is written `{by: claude}` (or `codex`, `opencode`). The loop checks its answer
and falls back to the rules when the answer is unusable. An agent that writes designs keeps one
session per part until the part is admitted; an agent on any other box starts fresh every turn,
or keeps one session for the whole round with `{by: claude, session: pass}`. Every box says it
the same way (D795): a word, an agent's name, or `{by: <who>, ...}` with its settings beside.

`flux task check <document>` prints these boxes for a given document, each with the choice in
force.

### In a document only

The loop crafter offers the choices above. A document written by hand can also say:

| key | value | meaning |
|---|---|---|
| `orchestrate` | `given` | take the parts in the order the document lists them, no model |
| `orchestrate` | a list, e.g. `[sweep, gradient]` | several searches, one after the other |
| `generate` | `{catalog: [...]}` | a fixed list of designs, no model |
| `knowledge` | `{by: opencode}` | that coding agent sums up the library's papers instead of the model |

## Parts

A large design can be made as several **parts**, each written and checked on its own, then
composed and measured as one. List them (`parts: [decoder, datapath]`), say what each is
(`parts: {decoder: "the opcode to control lines", datapath: "the ALU and the registers"}`) or let
the loop divide the statement (`parts: decompose`). The drawing then shows a *parts* stack beside the checks.

## Measurements that estimate first

A costly measurement can be predicted before its tool runs, with `estimate:` on the stage. A
design estimated to fail a gate or a limit by more than the margin is skipped at that stage.

| `kind` | the estimate comes from |
|---|---|
| `surrogate` | a fit over the designs this stage has already measured |
| `command` | a script of yours that prints the same `name=value` numbers |
| `model` | the AI model, from the design and the stage's past results |

```yaml
flow:
  measure:
    place:
      command: flux rtl measure {artifact} --stage place --clock-ps 1000
      estimate: {kind: surrogate, margin: 0.05}
```

## Rules that never change

- **The checks are never delegated.** No model or agent decides whether a design is correct; a
  design that fails a check is refused, never ranked.
- **A cheap measurement orders, it never concludes.** Quick estimates only choose which designs
  go on to the costly stage. The decision quotes the deepest stage that ran, and the report
  names it.
- **Measured and modelled are kept apart.** The report says which numbers come from real tools
  and which from estimates.
- **A run ends only when you stop it** (Ctrl-C, `q`, `flux stop`) or at `--passes N`.
- **Nothing is measured twice.** The record keys every measurement by the tools and the exact
  source.

## Code a document cannot hold

When a problem needs code (a solver, a simulator, a special search), it is a command the box
names -- `orchestrate: {command: ...}` for a search of its own, the parent's `generate` for a
composition of sub-loops. See [build your own](build-your-own.md#when-you-need-code-commands-beside-the-document).
