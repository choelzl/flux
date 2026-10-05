---
hide:
  - navigation
---

# Run a problem

A problem is one document, `myproblem/problem.yaml` here (from
[`flux new`](build-your-own.md) or the [loop crafter](loop-crafter.md)). Hardware problems run
inside the tool shell (`nix develop`, see the [full install](../index.md#get-started)).

1. See what the document needs and which tools are missing. This runs nothing:

    ```bash
    flux task check myproblem
    ```

2. Run it:

    ```bash
    flux task run myproblem --passes 1
    ```

3. Read the report printed at the end, or write an HTML one from the record:

    ```bash
    flux report myproblem/out/myproblem.db
    ```

## Options you will use

| option | what it does |
|---|---|
| `--passes N` | stop after N passes; without it a run goes on until you stop it |
| `--screen-only` | stop at synthesis (fast, an estimate only) |
| `--tui` | a live screen; `f` types a note into the next prompt, `q` quits |
| `--db FILE` | where the record goes (default `out/<id>.db` beside the document) |
| `--json FILE` | also write the answer as JSON, for scripts |
| `--model NAME` | the AI model to use, for this run |

## Stop and resume

Stop a run with Ctrl-C, `q` in the live screen, or from another terminal:

```bash
flux stop myproblem/out/myproblem.db        # at the end of the pass; --now stops at once
```

Run the same command again and it resumes: nothing already measured is measured twice. A run
without `--passes` never stops by itself; when a pass finds nothing new, the next one explores.

For a run that outlives your terminal:

```bash
flux run flux task run myproblem     # starts it detached, with a log
flux status myproblem/out/myproblem.db                      # is it running, how many passes
flux attach myproblem/out/myproblem.db                      # follow its log
```

## Read the results

| what | where |
|---|---|
| the decision, the trade-offs, every refused design and why | the report printed at the end; the `--json` file |
| the chosen design | `myproblem/out/` |
| how the search moved, pass by pass | `flux report myproblem/out/myproblem.db` (an HTML page) |
| every prompt and reply | `flux log myproblem/out/myproblem.db` |

The report says which numbers were measured by real tools and which were only estimated, and
names the deepest stage that ran.

## Choosing an AI model

Problems whose designs come from a script (`flux example sweep`, `rtl-sweep`, `tune`) need no
model. For the others, Flux uses a local [Ollama](https://ollama.com) by default and the model
named in `FLUX_LLM_MODEL` (default `qwen3.8:latest`). For any OpenAI-compatible server:

```bash
export FLUX_REMOTE_BASE_URL=http://my-server:8080
export FLUX_REMOTE_MODEL=<model name on that server>
export FLUX_REMOTE_API_KEY=<key>        # only if the server wants one
```

`flux selftest` says whether the model answers.

## Using a coding agent

Claude Code, Codex or OpenCode can write the designs, or do any step of [the loop](loop-shape.md)
that does not establish facts. Name it in the document:

```yaml
flow:
  generate: claude        # or codex, opencode
```

The agent must be installed and signed in on the machine that runs Flux. If its program is not
on the `PATH`, give the path in `FLUX_CLAUDE_BIN` (or `FLUX_CODEX_BIN`, `FLUX_OPENCODE_BIN`).

## Asking something else

Every setting is in the document. To ask a different question, copy the folder, change its
`objectives:` (or `flow`, `budget:`); the copy's folder name is its id, so it keeps its own
record.
