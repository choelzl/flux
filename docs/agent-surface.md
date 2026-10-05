# Agents and scripts

Packages: `interfaces/cli/`, `core/loop/` (`flux_loop.author`, `flux_loop.agent`,
`flux_loop.skills`), `core/llm/`, `mentor/knowledge/`, `mentor/records/`. Part of
[architecture.md](architecture.md)'s layering.

There is one way in: the `flux` command. A person, a shell script, a CI job and a coding agent
all drive Flux the same way, through a problem document and the CLI. What differs is who
writes the document, who fills the loop's roles, and how the answer is read back.

## Scripts: run a document, read the answer as JSON

```bash
flux task check applications/mul8/mul8.problem.yaml          # answerable? which tools, which roles
flux task run   applications/mul8/mul8.problem.yaml --json answer.json
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
writes the Python prototype instead; the loop checks it with `flux rtl proto` and spells the
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

A coding agent can also answer any box but the gate and the stages: `flow: {critique: claude}`
(`{by: claude, timeout_s: 300}` with options), `{orchestrate: opencode}`, `{orchestrate: codex}`, and so on. The loop writes
the question, checks the agent's `out.json`, sends a refused answer back once, then falls back to
the rules half; every turn is on the record ([design-agent-loop.md](design-agent-loop.md), D640).

## Isolation and redaction

Two boundaries:

1. **Agent and evaluator.** A model or agent proposes designs and documents; it never writes the
   gate, the stages' tools, the calibration store or the benchmark holdout. The holdout
   partition of the benchmark corpus is not visible to search or to any agent
   (`CorpusStore.public_entries()`, [stores.md](stores.md)).
2. **Evaluator and model context.** A PDK confidentiality policy ([decisions.md D94](decisions.md),
   `evaluator/redaction/`): the raw ASAP7 synthesis entry point refuses outright
   (`ConfidentialPdkError`) if its PDK is registered confidential. Only ASAP7 is registered, and it
   is BSD-3-Clause, not confidential, so the refusal is a tested guard rather than a live filter;
   nothing yet turns absolute numbers into relative ones.

## Knowledge layer

`mentor/knowledge/`: `knowledge_lookup(query, standard_id=None)`, backed by a pure-Python BM25
index (`retrieval.py`, no embeddings or API key) over an ingested corpus whose provenance class
is kept on every chunk: five chapters of the RISC-V unprivileged ISA manual (CC BY 4.0, parsed
from the upstream AsciiDoc) and the curated `design-guidance` corpus (original prose,
[D244](decisions.md)/[D267](decisions.md): memory implementation, multi-port composition,
datapath PPA, interconnect fabric selection). A document's `flow.knowledge` and the
`knowledge` tool reach it. The sibling `mentor/records/` package (`flux_records.mining`)
computes typed facts from the campaign and calibration stores, never ingested into the BM25
index ([D243](decisions.md)), and renders them into prompts ([D245](decisions.md)). Not
implemented: any licensed standard beyond `riscv-unpriv`, an embedding backend, connectors for
formats other than AsciiDoc (AMBA/JEDEC/PCIe/I2C need a paid licence, [D31](decisions.md)).

## The CLI

Every command and option is in [usage-guide.md](usage-guide.md).
