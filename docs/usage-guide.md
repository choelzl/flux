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

`flux serve` is a shared server with accounts. Each user has loops: a problem document and
its files. A loop is running or not; starting it again resumes it from its record. Each user
checks, starts, stops and follows their loops live (D683, D689).

```bash
flux user add ada --admin            # the first account, on the server's machine
flux serve                           # http://127.0.0.1:8765/ ; --host 0.0.0.0 behind a TLS proxy, with --secure-cookie
```

- **The configurator:** the loop crafter inside the app (D686). **New loop** builds a document and
  creates the application from it, with the files it runs. Beside the form, **Files that go with
  it** lists the loop's own files (scripts, golden models, specs): open one to edit it, write a new
  one, drop files or folders, delete one. A file the document names as `{home}/…` that the loop
  does not have is said to be missing, and a click writes it. **Configure**, on an application, reads its document back
  into the same form and saves it. What the form cannot say (a `world:`, its own stages or
  settings, `params`, a `{build, test}` gate, a failure pattern, objectives with a stage or tie
  of their own) is kept exactly as written and listed beside the file. Comments are not kept.
  A save first shows what it changes, line by line, and writes only when you confirm it.
- **Loops list:** search, filter by state (running, idle, failed), order by activity, name,
  accepted designs or decision. **Upload a loop** (beside New loop) takes a dropped folder, files
  or a `.zip`. Each loop shows its accepted and measured designs and the
  decision's number on the first objective (✓ or ✗ against its limit).
- **Applications:** upload files, a folder or a `.zip`, or write the YAML in the page; add
  files (or a `.zip`) to an existing one. Files and folders can also be dragged onto the page:
  a dropped folder keeps its paths, and names the new loop. Every file can be viewed, edited and downloaded. **Check the document** runs `flux task check` in the
  sandbox.
- **Start and stop:** a start takes passes (or "until I stop it"), screen only and a network
  allowlist, and is always sandboxed. The dialog offers the last start's choices, and runs the
  check when the inputs changed since it last ran; when it fails, the button says "Start
  anyway". It resumes the loop from its record. Stop after the pass
  or at once. A loop has one log (every start marked in it), one answer and one notes inbox;
  it has no run numbers.
- **The agents' workbench:** on the application's page, the tools and notes the agents keep,
  each with its first line.
- **Notifications:** the page tells you, and the bell keeps a list, when a run ends, fails, or
  its agent asks a question; desktop notifications when allowed.
- **Code:** files, a design's source and the code in agent prompts are highlighted (YAML,
  Python, SystemVerilog/Verilog, VHDL, C/C++, JSON, Markdown, shell, Tcl). **Theme:** system,
  light or dark, from the top bar, remembered in the browser.
- **A loop's page** (tabs: Overview, Live, Log, Timeline, Agent turns, Results, Files, Workbench):
  - **Overview:** the loop's state, designs measured (accepted, failed), passes on record, the
    objective, and the decision's numbers against the limits. A best-so-far chart per objective
    shows each measurement in order, the best as a step line, the limit dashed and the passes
    marked. Also the agent's open question, the latest notes and the newest workbench entries.
    While the loop runs, it redraws once a minute. A figure gives the model and agent turns, their
    time and tokens. Under the decision, the best three designs: the decision, then accepted
    before failed, the deepest stage, then each objective without a limit.
  - **Live:** the task tree as the TUI shows it, from the run's journal `events.jsonl`. By
    default it follows the running task (an agent first) and collapses finished branches, and it
    can be searched. Select a task for its parameters, live fields (an agent's commands, output,
    thinking), output and the log's latest lines; following, it shows the running task, and at
    rest the one that ended last. Standings show as counts, the frontier and the parts. It shows the
    latest start's tree.
  - **Log:** the loop's output as it grows, numbered, problems highlighted, each start marked.
    Show one start or all; jump to the previous or next problem. It can follow (it pauses when
    you scroll up), wrap, filter by text or `/regex/`, show problems only, and download.
  - **Timeline:** where one start's time went, from its journal. Every phase that does the work
    (a tool, an agent, a model call) is a bar in the lane of its kind: agent, model, gate, a
    stage, generation, re-verify, knowledge, the loop's own work. Per kind: phases, busy time (work
    side by side counted once), share of the wall clock, summed time, and how many ran at once.
    Choose a start and a pass. It redraws every 10 s while the loop runs.
  - **Agent turns:** each prompt, reply and tool call, with its model and tool version, tokens,
    tool calls, session, exit and folder. Above them, what the
    turns cost: turns, time, tokens in (and from the cache), out, and USD where the agent prices
    it, in all and per agent or model. Tokens are counted from D694 on: an agent's own report
    (Claude Code's `result`, OpenCode's `step_finish`), every exchange of a model turn.
  - **Results:** the designs the loop measured successfully, across every start, each
    **accepted** or **failed** by the loop's limits (the stages' cutoffs, the objectives'
    limits), with its numbers at the deepest stage it reached and a mark on each limited one.
    The decision comes first; any column sorts (again to reverse, missing numbers last).
    Filter: all / accepted / failed. Two charts come first. **Pareto front:** any two metrics,
    at one stage or each design's deepest, accepted and failed designs coloured, the decision a
    diamond, the non-dominated designs joined; a click opens the design. **Improvement over
    time:** the best so far of each chosen metric. With more than 3000 measurements the charts
    draw every new best and an even share of the rest. The table grows by 200 rows. **Compare:**
    tick two designs for their numbers side by side at each stage, the change (green where it is
    better), and a diff of their sources. A design opens with the limits
    it misses, every stage's numbers and its source. A draft sent to repair or refused by the
    gate is not a result.
  - **Notes to the run:** each reaches the next prompt, as a note typed at the terminal would.
    When an agent asks (`questions: operator`), the page shows the question and its time left,
    and the answer goes back to the agent.
- **Usage:** the Account page gives your turns, time and tokens over all your loops; the Admin
  page, every user's.
- **Streams:** the Live and Log tabs say whether their stream is live. A dropped stream is opened
  again where it left off (no line twice, none lost), waiting up to 30 s between tries, and a
  banner says when the server cannot be reached. A stopped server waits at most 3 s for open
  streams; a restarted one finds its running loops again.
- **Models (Admin › Models, Account):** endpoint, model and key for Flux's own model calls and for
  each coding agent: OpenCode (its own, else Flux's model's), Claude Code and Codex (a `--model`,
  their endpoint and key). The admin sets them for the server; on their Account a user sees the
  server's values in grey and may set their own. A user who names their own endpoint in a group
  gets none of the server's values of that group. Keys are stored encrypted (`secret.key` beside
  the server's data) and never shown again. With nothing set, runs use the machine's own
  configuration (flux.env, OpenCode's and Claude Code's own).
- **Admin** (tabs: Loops, Resources, Users, Audit):
  - **Loops:** every user's loops, with controls over all of them. **Pause new starts** (with a
    reason users see; running loops go on), **stop every loop** after its pass or at once.
  - **Resources:** the machine (CPUs, load, memory, the disks of the server's data, the caches
    and the sandbox storage). The sandbox's containers with CPU, memory and PIDs, each with its
    loop (a `flux.app` label); a container no running loop owns is "left behind" and can be killed.
    Every loop's disk: inputs, record, log, workbench, sandbox cache. Clear a loop's tools' cache
    or its past passes' scratch (the journal, transcript and record stay); delete a cache no
    loop owns (a deleted loop's, or a `flux task run` of this machine's user).
  - **Users:** role, a running limit per user (empty: the server's `--max-running`), and usage.
- **Accounts:** passwords are hashed with scrypt; five failures lock a name for ten minutes;
  sessions live in an HttpOnly, SameSite=Strict cookie; every change needs the `X-Flux` header.
  A user sees only their own applications and runs. An admin manages users, sees every
  application (read only) and every run (and may stop it), and the audit trail.
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
