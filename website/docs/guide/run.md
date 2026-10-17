# Run, stop, resume

Use this page after [creating](tutorial.md) and [configuring](build-your-own.md) a loop. In
the web app, the same actions are a loop's **Start** and **Stop** ([Tour](../web/index.md#a-loops-page)).
Replace `sums` with your loop's folder. Commands use the active Flux environment.

## Check before running

```bash
flux task check sums
flux task run sums --passes 1 --json sums/out/answer.json
```

`task check` validates the document, lists the stages and required tools, and reports problems
before a run: every file the document names must be in the folder (a missing one is listed with the
key that names it and what the folder holds), and the golden model must run. It exits 2 when the
document does not load (`not a task:`) and 1 when something is missing or refused (`MISSING:`,
`REFUSED:`). Like a run, it runs in the [sandbox](#sandbox); `--no-sandbox` checks on this
machine. It is the same check an agent writing the problem runs before it ends its turn. It
does not execute the candidate checks and measurements. A bounded first run checks the actual
scripts and their output.

A folder normally contains `problem.yaml`. Additional `NAME.problem.yaml` files describe
alternate problems in the same folder and use separate records. When a folder contains several
valid documents, specify the file explicitly for unattended runs:

```bash
flux task run sums/small.problem.yaml --passes 1
```

## Runtime overrides

Values in `budget` are persistent settings; command-line options override them for one run.
Use `flux task run --help` for the complete command syntax.

| Option | Effect |
|---|---|
| `--passes N` | Cap passes; default is `budget.passes`, or unlimited when omitted/zero |
| `--steps N`, `--repair N` | Override work per pass and repair attempts per draft |
| `--screen-only` | Stop every design at the first measurement stage |
| `--db FILE` | Use a different record |
| `--out FILE` | Choose where the feasible decision artifact is written |
| `--json FILE` | Write the answer and results as JSON |
| `--tui` | Open the terminal task, log, and results view |
| `--model NAME`, `--think`, `--num-predict N` | Choose a model, request reasoning, and bound output tokens |
| `--no-structured` | Disable schema-constrained model output |
| `--role ROLE=NAME` | Override a role; `task check` lists available choices |
| `--agent tools orchestrate plan` | Give the direct model those tool, orchestration, and planning capabilities; `all` enables all three |
| `--plan FILE` | Follow a saved loop plan |
| `--tool-hops N`, `--hop-share F`, `--patience N` | Override model tool rounds, context share, and prototype patience |
| `--regenerate PART...` | Draft named parts afresh; `all` starts every part afresh |
| `--no-prototype`, `--no-patching` | Disable prototype work or edit-based repairs |
| `--skill DIR` | Add a skill directory; repeatable |
| `--replies FILE` | Supply scripted model replies, useful for repeatable experiments |
| `--rerun-baseline` | Measure the [baseline](parameters.md#baseline-pass-0) (pass 0) again, though the document's `baseline` is unchanged |
| `--reevaluate` | Check and measure every design on the record again, after a check or measurement changed |
| `--reevaluate-of ID` | Check and measure again only the designs one invalidation took out (its id is in Results) |
| `--remeasure` | Measure again the designs a rejection asked for, then rest |
| `--no-sandbox` | Run on this machine, not in the [sandbox](#sandbox) (also `FLUX_SANDBOX=0`) |

## Stop, resume, and keep experiments separate

An unlimited run continues until you stop it. Limits and timeouts can end individual attempts
or passes; a satisfied objective or a stalled search does not automatically end the run.
Stop with Ctrl-C, `q` in the TUI, the browser's Stop action, or another terminal:

```bash
flux stop sums/out/sums.db        # finish the current pass
flux stop sums/out/sums.db --now  # interrupt immediately
```

Run the document again to reuse its record. Cached measurements can be reused; stage commands,
the scripts they name, and params contribute to the cache identity. Changing the objective
can reuse observations; changing the measurement changes what can be reused.

For a separate experiment, use a fresh `--db` or a new folder name. Copying a folder's generated
`out/` and `workbench/` also copies its history and agent files; leave them out for a fresh loop.
In the browser, a stopped loop's **Settings › Preferences › Reset or delete** offers Reset, which
asks what to keep.
Reset removes generated data while retaining the source document and scripts.

For a process that outlives the terminal:

```bash
flux run -- flux task run sums
flux status sums/out/sums.db
flux attach sums/out/sums.db
```

### Upgrading older loops

Flux never upgrades a document silently. `flux task migrate FOLDER` shows what would change in a
loop's documents and `--write` saves them (the original kept as `<file>.orig`). A loop's task
journal from an earlier version, the file Live reads, is converted once with
`flux task migrate FOLDER --journal EVENTS` (the original kept as `events.jsonl.orig.gz`); on a
server, **Admin › Maintenance › Bring task journals up to date** does it for every loop.

## Read the result and full output

```bash
flux report sums/out/sums.db
flux log sums/out/sums.db
```

The report shows measured designs, trade-offs, refusals, and the standing design. The chosen
artifact is written under `out/` only when a design meets the requirements. An estimated metric
or a shallower screening result should not be read as a confirmed measurement.

| `task run` exit | Meaning |
|---|---|
| `0` | A measured design meets every requirement; the decision artifact is written |
| `3` | Correct designs were measured, but none meets every requirement; the report shows the shortfall |
| `1` | No design was measured, or the run could not start |
| `130` | Interrupted |

In the web app, a loop's [Live](../web/live.md) tab shows passes, tools, agent activity, and
their output as a tree, a graph, a timeline, and a log. Its **Start** picker opens an older start; its tasks and
conversations belong to that start.
Files, logs, and results provide raw or fullscreen views. History is available while its files
and records are retained; resetting a loop clears the generated history.

## Sandbox

`flux task check`, `flux task run` and `flux ask` run inside a container, so neither an agent nor
the document's own code (its commands, scripts, `golden.py`) can touch the rest of the machine.
The engine is rootless Podman when installed, else Docker (`FLUX_SANDBOX_ENGINE=podman|docker`).
Without either, these commands stop with `the sandbox needs Podman or Docker`.

- **It sees** the host read-only: the system, `/nix/store`, Flux itself, the programs on PATH,
  and the loop's folder (inside, at `/sandbox/<loop>/`).
- **It writes** only the loop's `out/` and `workbench/`, the record's folder, and the loop's own
  cache under `~/.cache/flux/apps/`. Scratch in `/tmp` is gone after the run.
- **Its home** holds the agents' configuration and logins for Flux, not your own home: `~/.ssh`,
  other repositories and the Docker socket are not there.
- **Network** is the host's by default. `FLUX_SANDBOX_ALLOW=host,domain,10.0.0.0/8` limits HTTP(S)
  to those destinations; add `FLUX_SANDBOX_RAW_NETWORK=1` for direct TCP/UDP to them (needs `nft`
  on the host).
- **Limits:** `FLUX_SANDBOX_MEMORY` (e.g. `16g`), `FLUX_SANDBOX_CPUS`, `FLUX_SANDBOX_PIDS` (default
  4096).
- **Off:** `--no-sandbox` or `FLUX_SANDBOX=0` runs on this machine as you. Use it only for code
  you trust, such as the [first loop](tutorial.md).

## Setup elsewhere

- Installing Flux, the web app and the hardware tools: [Install](../start/install.md).
- Flux's model and the coding agents: [Models and agents](models.md).
- Following runs in the browser: [Web app](../web/index.md).
