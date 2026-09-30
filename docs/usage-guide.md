# Usage guide: the commands

Every `flux` command and what it is for. Run them from `flux/` inside `nix develop` (or after
`pip install -e ./flux`; see the [repository README](../README.md)). The terms are in the
[glossary](glossary.md). Other pages cover the rest:

| to | read |
|---|---|
| choose and configure a model or a coding agent | [models.md](models.md) |
| pick a recipe for your problem | [cookbook.md](cookbook.md) |
| every key of a problem document | [author_reference.md](../flux/core/loop/src/flux_loop/author_reference.md) |
| drive Flux from a script or an agent | [agent-surface.md](agent-surface.md) |
| extend it: a policy, a world, a role | [extending.md](extending.md) |

## Environment

The model's variables are in [models.md](models.md). The others:

| variable | what it does | default |
|---|---|---|
| `FLUX_TMPDIR` | scratch files | the system's temp directory |
| `FLUX_TRACE_ROOT` | where each pass writes its prompts, replies and checked prototypes | `$TMPDIR/flux-traces` |
| `FLUX_OPENROAD_THREADS` | threads for one OpenROAD run | the cores / 4, at most 16 |

## Check the install

```bash
flux selftest [--full] [--no-model] [--model NAME]
```

Runs, in a temporary directory, what a newcomer would: the tools on PATH, a `sweep`, an
`rtl-sweep`, the model server, a problem the model writes, a coding agent on PATH; `--full`
adds the README's first run. Prints PASS, FAIL or SKIP per check; exits 1 when one fails.

## Start a problem

```bash
flux new NAME --kind python|rtl|sweep|tune|rtl-sweep     # a working problem to start from
flux ask "what you want" --file spec.pdf                 # an author writes the problem for you
flux ask --tui                                           # the same, from a setup screen
```

- `flux new` writes a document, its golden model or checker, and a README into `NAME/`. The
  [cookbook](cookbook.md) says which kind fits which problem.
- `flux ask` has an author (the model by default, or `--author opencode|claude|codex`) write
  the document and its files into `./out/ask-<slug>/`. It checks the document, runs it, and
  gives the author the report to revise for the next pass. Options: `--no-run` (write and
  check only), `--passes N`, `--screen-only`, `--dir DIR`, `--skill DIR`. The setup screen
  (`--tui`, or no prompt) takes the prompt, the files, the author and the passes; with
  "review first" on, you read the checked problem and type `run`, `stop`, or a note.

## Check and run a problem

```bash
flux task check DOC          # what it needs, what it will skip; runs nothing
flux task run DOC            # runs until stopped (Ctrl-C, flux stop, q in the TUI)
```

`task check` lists the parts, the roles each can be switched to, the stages and their
tools, and refuses a document that asks for what nothing measures.

`task run` options:

| option | what it does |
|---|---|
| `--passes N` | stop after N passes (default: the document's `budget.passes`, else until stopped) |
| `--steps N`, `--repair N` | work items per pass; repair attempts per draft |
| `--screen-only` | stop the chain at the first stage |
| `--db FILE`, `--out FILE` | the record (default `out/<id>.db` beside the document); the decided artifact |
| `--json FILE` | also write the answer as JSON: the decision, the front, the refusals |
| `--tui` | the terminal screen: tasks, timing, results, log, feedback (`f` types a note) |
| `--model NAME`, `--think`, `--num-predict N`, `--no-structured` | the model's name, reasoning on every turn, output tokens per turn, plain decoding |
| `--role ROLE=NAME` | switch who fills one role (`--role orchestrator=rules`) |
| `--agent tools\|orchestrate\|plan\|all` | let the model use tools in its turns, pick the next step, or plan the pass |
| `--plan FILE` | follow a loop plan document |
| `--tool-hops N`, `--hop-share F`, `--patience N` | tool rounds per turn; a tool round's share of the window; prototype turns granted per new best |
| `--regenerate PART...` | draft these parts again instead of resuming them |
| `--no-prototype`, `--no-patching` | no prototype stage; repair by rewriting, not by edits |
| `--skill DIR` | add a skill folder |
| `--replies FILE` | scripted model replies (what the tests use) |

A run resumes from its record: what was measured is never paid for twice.

## Long runs and records

```bash
flux run -- flux task run DOC     # start detached; the log goes under the trace root
flux status DB                    # is it running, since when, how many passes
flux attach DB                    # follow its log
flux stop DB                      # stop at the end of the pass (--now interrupts)
flux report DB                    # an HTML report beside the record: the front per pass, the best so far
flux log DB                       # every model and agent turn: prompt, reply, tool calls, errors
flux gc --db DB --keep-days 7 --apply   # remove trace directories no record names
flux knowledge digest --db DB     # the library's key points, digested once by the model
```

## The sandbox

`flux task run` and `flux ask` run in a container (D680), so neither an agent nor a
document's code (its commands, `golden.py`, scripts, `world:` hooks) can touch the rest of the
machine.

- **It sees:** the host read-only, meaning the system, `/nix/store`, the flux source, the
  executables on PATH and the problem folder. The same tools run, OpenCode and Claude Code
  included.
- **It writes:** the record's folder, the problem's `out/` and `workbench/`, and the
  application's cache `~/.cache/flux/apps/<id>/`. That cache is shared by the application's
  runs: `tmp/` holds its traces, `cache/` its caches. Tool scratch lives in the container's own
  `/tmp`, in memory and gone after the run (`FLUX_SANDBOX_TMP_SIZE` caps it).
- **HOME** is the application's (`apps/<id>/home`), holding the agents' sessions, their
  configuration and a copy of their login. Another application's cache is not there. `~/.ssh`, other repositories, the Docker socket and `~/.config/flux` are
  not there. The model settings and key come in through the environment.
- **Network:** the host's by default. `FLUX_SANDBOX_ALLOW=host,domain,10.0.0.0/8` gives no
  network except those hosts, through a proxy on the host. A refused host is said once.
- **Limits:** `FLUX_SANDBOX_MEMORY=16g`, `FLUX_SANDBOX_CPUS=8`, `FLUX_SANDBOX_PIDS` (4096).
- **Off:** `--no-sandbox` or `FLUX_SANDBOX=0`.
- **Engine:** rootless Podman when installed, else Docker (`FLUX_SANDBOX_ENGINE=podman|docker`)
  (D682).
  - **Podman** has no root daemon: a container is one of your own processes, and root inside is
    you outside. It needs no image, since it starts from a bare local root directory, and it
    keeps its state on a local disk (`/var/tmp/flux-podman-<uid>`, or `FLUX_SANDBOX_STORAGE`).
  - **Docker's** daemon is root, and the `docker` group is root-equivalent on the machine.
- **Stopping:** `flux status` and `flux stop --now` find a sandboxed run by its container.
- **Not inside:** Timeloop through Docker (no Docker socket inside). Run those with
  `--no-sandbox`.

## The web interface

`flux serve` is a shared server with accounts. Each user uploads applications (a problem
document and its files), checks them, starts runs, and follows each run live (D683).

```bash
flux user add ada --admin            # the first account, on the server's machine
flux serve                           # http://127.0.0.1:8765/ ; --host 0.0.0.0 behind a TLS proxy, with --secure-cookie
```

- **Applications:** upload files, a folder or a `.zip`, or write the YAML in the page. Every
  file can be viewed, edited and downloaded. **Check the document** runs `flux task check` in the
  sandbox.
- **Runs:** passes, screen only, and a network allowlist, always sandboxed. Stop one after its
  pass or at once.
- **A run's page:**
  - **Live:** the task tree as the TUI shows it, from the run's journal `events.jsonl`; select a
    task for its parameters, live fields (an agent's commands, output, thinking) and output.
  - **Log:** the run's output as it grows.
  - **Agent turns:** each prompt, reply and tool call.
  - **Results:** every measured design, the answer, and the report.
- **Accounts:** passwords are hashed with scrypt; five failures lock a name for ten minutes;
  sessions live in an HttpOnly, SameSite=Strict cookie; every change needs the `X-Flux` header.
  A user sees only their own applications and runs. An admin manages users and reads every run
  and the audit trail.
- **Data:** `$XDG_DATA_HOME/flux/web` (`--data`), holding `flux-web.db` and
  `users/<name>/apps/<app>/`. A run's sandbox cache is `~/.cache/flux/apps/<user>-<app>/`.
- **Limits:** `--max-running` runs at once per user (4).

## RTL tools

The commands an RTL document names as its gate and stages:

```bash
flux rtl test design.sv --golden golden.py        # Verilator against the golden model; prints `N failing of M`
flux rtl proto prototype.py --golden golden.py    # a Python prototype, checked on every input up to 20 bits
flux rtl measure design.sv --stage synth --clock-ps 1000   # or place, route: ASAP7 metric=value lines
```

`flux rtl test` exits 1 when the design fails and 3 when it does not compile. `flux rtl
proto` prints where a prototype fails, grouped by the input's sign and exponent.

## The IR and the evaluators

The accelerator-era path, kept for the evaluator backends: a workload, an architecture and a
mapping as documents ([ir.md](ir.md)), evaluated through the evaluator ABI
([evaluator-abi.md](evaluator-abi.md)). These commands are left out of `flux --help`.

```bash
flux import core/ir/workload/examples/mlp-gemm0.yaml            # validate and hash (--store DB keeps it)
flux eval --workload core/ir/workload/examples/mlp-gemm0.yaml \
          --arch core/ir/architecture/examples/simple-npu-1d-v1.yaml --backend zigzag
flux replay RESULT_ID --store DB                                # re-run a stored result and compare
```

`flux eval` prints a `Result`: an estimate per metric (value, interval, method), a validity
check, the bottleneck and the provenance. The backends are `zigzag`, `timeloop`, `rtl`,
`openroad` and `champsim`. In Python:

```python
import yaml
from flux_evaluator_abi import Budget, Candidate, make_evaluator

workload = yaml.safe_load(open("core/ir/workload/examples/mlp-gemm0.yaml"))
arch = yaml.safe_load(open("core/ir/architecture/examples/simple-npu-1d-v1.yaml"))
result = make_evaluator("zigzag").evaluate(Candidate(workload=workload, arch=arch), Budget(),
                                           frozenset({"latency_cycles", "energy_pj"}))
```

`flux_store.CachingEvaluator(inner, ResultStore("results.db"), evaluator_prefix="zigzag")`
serves a repeated call from the store. [calibration.md](calibration.md) covers the intervals
and conformance; [stores.md](stores.md) covers the record and the result store.

## From Python

```python
from flux_loop import PromptProblem, load_task, request_for, run_loop

task = load_task("applications/adder16/adder16.problem.yaml")
result = run_loop(PromptProblem(task), request_for(task, db="adder16.db"), proposer=None)
```

A world is a class taking the problem, with the hooks it chooses to implement
([extending.md](extending.md)).
