# Run and results

Use this page after [creating](tutorial.md) and [configuring](build-your-own.md) a loop.
Replace `sums` with your loop's folder. Commands use the active Flux environment.

## Check before running

```bash
flux task check sums
flux task run sums --passes 1 --json sums/out/answer.json
```

`task check` validates the document, lists the stages and required tools, and reports problems
before a run. It does not execute the candidate checks and measurements. A bounded first run
checks the actual scripts and their output.

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
| `--screen-only` | Omit the costliest stage in a multi-stage chain |
| `--db FILE` | Use a different campaign record |
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

## Stop, resume, and keep experiments separate

An unlimited run continues until you stop it. Limits and timeouts can end individual attempts
or passes; a satisfied objective or a stalled search does not automatically end the campaign.
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
In the browser, a stopped loop's Settings offers Reset with a warning listing affected folders.
Reset removes generated data while retaining the source document and scripts.

For a process that outlives the terminal:

```bash
flux run -- flux task run sums
flux status sums/out/sums.db
flux attach sums/out/sums.db
```

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

In the web UI, Live shows passes, tools, agent activity, and their output. Use the historical
start picker for older retained runs; their tasks and conversations belong to that start.
Files, logs, and results provide raw or fullscreen views. History is available while its files
and records are retained; resetting a loop clears the generated history.

## Browser setup

Install the web extra and create your account using the same user that should own the files:

```bash
pip install -e "./flux[web]"
flux user add me --admin
flux serve
```

These install commands assume the repository root. The server reports its address; open it
and sign in. Create a loop from an example, an empty baseline, an uploaded folder, a clone,
an agent, or the configurator. The configurator edits the same document as the CLI.
App Settings controls the document and user settings; administrators control sandbox and
parallel-work permissions. Under a loop's **Settings › Loop › Advanced**, an admin can also
list extra nixpkgs or nixchip package attributes, such as `jq` or `verilator`. They use Flux's
locked versions, are cached, and become available on the next sandbox launch.
The [Loop crafter](loop-crafter.md) is also available on this site.

## Hardware tools

Install [Nix](https://nixos.org/download/) and enable
`experimental-features = nix-command flakes` in `~/.config/nix/nix.conf`. From the repository:

```bash
cd flux
nix develop --accept-flake-config
flux selftest --no-model
```

Use this shell for loops requiring Verilator, Yosys, OpenROAD, ChampSim, or the other packaged
tools. The first shell entry may build or download tools. Keep your loop folder in a convenient
location and pass its path to Flux.

## Models and agent environments

Script-generated searches do not require an AI model. The direct model defaults to local
Ollama and `FLUX_LLM_MODEL` (`qwen3.8:latest`). A remote OpenAI-compatible endpoint uses:

```bash
export FLUX_REMOTE_BASE_URL=https://your-model-server.example/v1
export FLUX_REMOTE_MODEL=your-model-name
export FLUX_REMOTE_API_KEY=your-key
flux selftest
```

Supply a key only if the endpoint needs it. Store persistent machine settings in
`~/.config/flux/flux.env`; shell variables take precedence. `FLUX_CONFIG` selects another
config file. `FLUX_REMOTE_API_KEY_FILE` keeps the key in a separate file.

Coding agents must be installed and authenticated where Flux runs. Their executable overrides
are `FLUX_CLAUDE_BIN`, `FLUX_CODEX_BIN`, and `FLUX_OPENCODE_BIN`. Flux's per-agent aliases are
`FLUX_<NAME>_BASE_URL`, `FLUX_<NAME>_API_KEY`, `FLUX_<NAME>_MODEL`, and, for agents supporting it,
`FLUX_<NAME>_OAUTH_TOKEN`. The web settings can configure agents without changing the document.
Keep credentials in your environment or account settings, not the loop's YAML.

For detailed environment recipes, use the repository's
[model and agent setup](https://github.com/choelzl/flux/blob/main/docs/models.md).
