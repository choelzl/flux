# Agents and scripts

Packages: `interfaces/cli/`, `core/loop/` (`flux_loop.author`, `flux_loop.agent`,
`flux_loop.skills`), `core/llm/`, `mentor/knowledge/`, `mentor/records/`. Part of
[architecture.md](architecture.md)'s layering.

There is one way in: the `flux` command. A person, a shell script, a CI job and a coding agent
all drive Flux the same way, through a problem document and the CLI. What differs is who
writes the document, who fills the loop's roles, and how the answer is read back.

## Scripts: run a document, read the answer as JSON

```bash
flux task check applications/mul8   # answerable? which tools, which roles
flux task run   applications/mul8 --json answer.json
```

`flux task run DOC --json FILE` writes what the pass decided ([D591](decisions.md)): the
decision with its metrics and artifact path, the frontier, what was refused and why, what is
not established, the lessons and the report's lines. The record (`--db`) keeps everything else, readable with
`flux report`. To ask a different question of the same problem, copy the document and change
its `params`, `objectives`, `budget` or `flow`; a changed ask opens its own record.

Long runs detach: `flux run -- flux task run DOC ...`, then `flux status`, `flux attach` and
`flux stop` on the record ([D513](decisions.md)).

## A prompt instead of a document: `flux ask`

An *author* (the model, or a coding agent with `--author opencode|claude|codex`) turns a prompt
and its files into a problem document and the files it names, checked before anything runs
([D586](decisions.md)); the loop runs it and the author revises it from the report. The author
writes the *problem*, never the design. Options are in [usage-guide.md](usage-guide.md).

## Coding agents as the generator

Any document can hand generation to a coding agent instead of the model
([D575](decisions.md)):

```yaml
flow:
  generate: opencode                  # a preset: opencode, claude, codex
  # generate: {by: claude, questions: model, max_questions: 2}}
  # generate: {by: {command: [my-agent, "{prompt_file}", "{artifact}"], timeout_s: 900}}
```

One session per part (D669): the first draft reads the whole brief; a repair or a critic's
send-back of a part not yet admitted resumes that session with a short message (what failed, the
file, fix it). Once the part is admitted the session ends; an improve is a new agent.

The loop gives the agent a work directory, a brief (`PROMPT.md`: the same design prompt the
model gets, plus the prior artifact and the failure on a repair) and a time limit. The agent
uses its own model, tools and skills; the loop then reads the artifact and runs its own build,
check and gate around it, exactly as around a model's reply. When a headless agent stops to ask
a question, `questions:` says who answers: nobody (`decide`, the default), the loop's `model`,
or the `operator` at the TUI ([D585](decisions.md)). With `budget.prototype: true` the agent
writes the Python prototype instead; the loop checks it with `python -m flux_loop.golden_proto` and spells the
RTL ([D618](decisions.md)). The agent writes and never runs: the loop compiles, tests and
measures, and brings a failure back to the agent's session ([D673](decisions.md)). [models.md](models.md) covers the agents' own configuration.

## An outside agent driving Flux

[`skills/flux/SKILL.md`](../skills/flux/SKILL.md) is the skill an outside coding agent installs to
drive Flux itself: the ways in, the document, running and reading, the rules for golden models
([D592](decisions.md)). `tests/unit/test_flux_skill.py` keeps its example document valid.

## Skills inside the loop

A skill is the folder coding agents already know: a `SKILL.md` (front matter with `name` and
`description`, then instructions) and whatever files it brings ([D588](decisions.md)). A
document lists them under `skills:`; `flux task run --skill DIR` and `flux ask --skill DIR` add
more. A coding agent finds them where it looks for skills (the loop copies them under
`.claude/skills/`, `.opencode/skills/` and `.agents/skills/` in its work directory). The loop's
model sees an index in every prompt and loads a skill's instructions or files with the `skill`
tool inside its turn.

## The workbench

The agents' own folder for tools and notes ([D677](decisions.md)): `workbench/` beside the
document, always (D790). It is made on the first
agent turn with `tools/` and `notes/` and is kept across runs. Every agent of the problem
(generate, prototype, every box) finds it as `workbench/` in its work directory, and its brief
lists what the folder holds, one line per file. The agents build tools there (scripts that fit,
tabulate or analyse) and keep notes (the method, what failed and why). This is knowledge built
inside the loop, beside the lessons that `knowledge.lessons` draws from measured results between passes.
The loop provides the folder and never reads it. Commit it with the application if it is worth
keeping.

## The model and the agentic halves

A model is an OpenAI-compatible server ([D508](decisions.md)): a hosted LocalAI
(`FLUX_REMOTE_BASE_URL`, `FLUX_REMOTE_MODEL`, `FLUX_REMOTE_API_KEY`, [D469](decisions.md)) or a
local Ollama (`FLUX_LLM_MODEL`). Every loop runs without one; the roles a model would fill say
they were skipped.

With a model that makes tool calls, the loop offers three agentic halves
([D505](decisions.md)), each off by default and switchable per run
(`flux task run --agent tools|orchestrate|plan|all`, a document's `flow: {orchestrate: agent}`, a plan file):

| half | manual (the code's rules) | agentic |
|---|---|---|
| a model turn | one message, JSON back, no tools (`tools: false`) | `flux_llm.tools.Tool`s the model calls inside the turn: `compute`, `check` (the problem's own test with its report), `history`, `knowledge`, `timing`, `skill`, and the world's instruments (`error_map`, `compare`, `quantisation`, `family`, [D530](decisions.md), [D535](decisions.md)) |
| orchestration | `rules` / `given` / `llm` menus, the improve ladder's first due step | `agent`: reads `standings()`, `history(part)`, `decisions()`, `knowledge()`, picks from the same menus, every pick on the ledger |
| the loop's shape | a plan file (`--plan`): parts and order, budgets, stages, roles, tools | `--agent plan`: the agent writes the plan, validated by the same `flux_loop.plan.check_plan`, kept on the record |

What none of the halves may decide: admission. The gate stays the exhaustive test's.

## Coding agents in every box

Every delegable box takes `{by: <agent>, ...}` ([D640](decisions.md), D795): a preset
(`opencode`, `claude`, `codex`) or a command of your own. The boxes that establish facts stay real
tools and are never delegated: test and calibrate are refused at load (D460); the stages (and
their estimates, D665) and the record are not boxes; feedback stays human.

```yaml
flow:
  orchestrate: claude              # the next work item, or points in `flow.orchestrate.space`
  plan:        claude              # the pass's plan, checked by check_plan
  generate:    opencode            # the artifact (D575)
  critique:    {by: claude, timeout_s: 300}
  knowledge:   {lessons: claude}   # lessons mined from the record, citing its rows
  select:      claude              # among designs that tie on the objective vector
```

| box | the agent answers | the loop checks | falls back to |
|---|---|---|---|
| validate | objections to the document, each with its key | keys exist; advisory, never refuses | rules |
| orchestrate | the next item from the menu, or points in the space, with reasons | on the menu; knobs and values in the space, not measured before, ≤ the batch | rules / the phase's policy |
| plan | parts, order, method, budgets | `check_plan` | the document's order |
| generate | the artifact | build, gate, repair | the model |
| critique | `{ok, why}` on a division, a part or a decision | an objection is not a veto: `critique_rounds` bounds it (D433) | no critique |
| extract | lessons, each citing record rows by id | every cited row exists and says what the lesson claims | mined |
| select | the pick among ties, with a reason | the pick passed the gate and meets every goal | the vector's pick |

**One contract.** A box turn is a fresh work directory (`agents/<box>/NNN/` under the pass's trace
directory): the loop writes `BRIEF.md` (what the box decides, its rules, the output schema) and
`in/*` (standings, the menu, history, the frontier, record rows); the agent writes `out.json` (or
`artifact.*` for generate) with `why`. The loop validates it, resumes the session once with the
reason on an invalid answer, then falls back to the rules half. Each turn has the agent's
`timeout_s` (default 1800 s) and is a `decided:agent_turn` event (box, agent, answered or fell
back, seconds, why) that `flux report` lists. An agent missing, timing out or answering invalidly
gives the same run as no agent.

The agent reads, searches, computes and writes; it does not compile, simulate, synthesize or test
(`DENIED` in `flux_loop/agent.py`, D673, D674). Inside a turn it may check its file through the
loop's own gate and stages with `flux probe`, within a budget, on the record as its own check
(D678).

**The orchestrator** decides; the loop executes, measures and records, so the ledger has every
pick, `flux stop` works, a crash resumes from the record and the agent cannot claim a result the
tools did not measure. `select` only breaks ties: the vector's pick stands unless designs sit
within every objective's tie band of it (at the goal when it is), or, with no goal, on the
non-dominated front; its reason joins `decided_by`.

**Not only RTL.** A C/C++ kernel: `language: cpp`, the gate its tests, the stages a compile then a
timed benchmark (`workers: 1`), `flow.orchestrate.space` the knobs (unroll, tiles, loop order,
`#pragma omp` schedule, vector width). `orchestrate: {by: ...}` proposes points;
`generate: {by: ...}` does the rewrites a knob cannot say (fusing loops, branch-free code); the
gate keeps every rewrite equal to the reference.

## Isolation and redaction

Two boundaries:

1. **Agent and judges.** A model or agent proposes designs and documents; it never writes the
   gate, the stages' tools or the record ([records.md](records.md)).
2. **Tools and model context.** ASAP7, the only PDK in use, is BSD-3-Clause; the PDK
   confidentiality guard (D94) was removed with nothing to guard (D952). Nothing turns absolute
   numbers into relative ones.

## Knowledge layer

`mentor/knowledge/`: `knowledge_lookup(query, standard_id=None)`, backed by a pure-Python BM25
index (`retrieval.py`, no embeddings or API key) over an ingested corpus whose provenance class
is kept on every chunk: five chapters of the RISC-V unprivileged ISA manual (CC BY 4.0, parsed
from the upstream AsciiDoc) and the curated `design-guidance` corpus (original prose,
[D244](decisions.md)/[D267](decisions.md): memory implementation, multi-port composition,
datapath PPA, interconnect fabric selection). A document's `flow.knowledge` and the
`knowledge` tool reach it. The sibling `mentor/records/` package (`flux_records.mining`)
computes typed facts from the campaign store, never ingested into the BM25
index ([D243](decisions.md)), and renders them into prompts ([D245](decisions.md)). Not
implemented: any licensed standard beyond `riscv-unpriv`, an embedding backend, connectors for
formats other than AsciiDoc (AMBA/JEDEC/PCIe/I2C need a paid licence, [D31](decisions.md)).

## The CLI

Every command and option is in [usage-guide.md](usage-guide.md).
