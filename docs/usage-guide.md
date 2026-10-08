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
| extend it: a policy, a search command, a role | [extending.md](extending.md) |

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
flux example python|rtl|sweep|tune|rtl-sweep NAME     # a working problem to start from
flux ask "what you want" --file spec.pdf                 # an author writes the problem for you
flux ask --tui                                           # the same, from a setup screen
```

- `flux new NAME` writes a loop's baseline (D825): `NAME/problem.yaml` with every part present and what goes there,
  a README of what each part of the folder is for, an empty `library/` (`--dir D`: into `D/NAME/`) -- nothing of a case.
  `flux example KIND NAME` writes a worked example that runs (sweep, tune, python, rtl, rtl-sweep); the
  [cookbook](cookbook.md) says which fits which problem.
- `flux ask` has an author (the model by default, or `--author opencode|claude|codex`) write
  the document and its files into `./out/ask_<slug>/`. It checks the document, runs it, and
  gives the author the report to revise for the next pass. Options: `--no-run` (write and
  check only), `--passes N`, `--screen-only`, `--dir DIR`, `--skill DIR`. The setup screen
  (`--tui`, or no prompt) takes the prompt, the files, the author and the passes; with
  "review first" on, you read the checked problem and type `run`, `stop`, or a note.

## The problem document

A problem is a folder; its document is `problem.yaml` in it, and the folder's name is the
problem's id -- the name of its record (`out/<id>.db`) -- so the document has no `id:` (D786).
To ask something else, copy the folder -- or write `NAME.problem.yaml` beside `problem.yaml`:
another problem of the same loop, with the same id and its own record `out/<id>.NAME.db` (D787).
With several that load, `flux task run FOLDER` asks which (with no terminal it lists them; name
the file to run one), and the web's Start dialog has a picker, each choice checked before it starts. A document says what to make (`statement`, `contract`, `language`), what better means
(`objectives`), how much to spend (`budget`), and, under `flow`, each box of the loop -- who
works it and its own settings (D775):

```yaml
flow:
  orchestrate:                                   # the search, its space and where it starts
    policy: sweep
    space: {arch: [ripple, kogge_stone], block: [2, 4, 8]}
  generate: {command: "{python} {home}/gen.py {artifact} {arch} {block}"}
  test: flux rtl test {artifact} --golden {home}/golden.py            # the gate
  measure:                               # the stages, by name, cheapest first
    screen: flux rtl measure {artifact} --stage synth --clock-ps 300
    confirm: {command: "flux rtl measure {artifact} --stage place --clock-ps 300", timeout_s: 1800}
  knowledge: {files: [spec.md], agent: opencode}   # what is read; who digests the papers
  select: {finalists: 2}
```

There is no `world:` or `hooks:` (D803): what a document cannot say is a command beside it -- a search (`orchestrate: {command: "... {history} {state} {params}"}`, D799), sub-loops in folders whose parent's `generate` composes them (`{parts}`, D801) -- a parent's `generate` that is a model or an agent (`generate: claude`) drafts for them instead, inherited like any box (D804). A search is the orchestrator's (D797): `orchestrate: {policy: sweep, space: {...}}` -- there is no `dse:`; the record's lessons are `knowledge: {lessons: mined}` and `brief` is gone (D796). Every box says who works it the same way (D795): a word (`rules`, `model`, `off`), an agent's name (`critique: claude`), or, only to give it options, `{by: claude, session: pass, ...the box's settings}` (D830: the name alone otherwise -- `plan: opencode`). Knowledge says who digests its papers as `digest:` and who writes lessons as `lessons:`, by name too: `knowledge: {files: [spec.md], digest: claude, lessons: mined}`. `flow.test` is a map by name like `flow.measure` (`lint: ...`, `golden: {run: ..., timeout_s: 300}`; a check named `build` refuses on any non-zero exit, D789). `parts` is a list of names or a map from each name to what it is (D792). The measurement cache is always on and keyed on the stage's command, the scripts it names and the params (D790); there is no `cache:`, `workbench:`, `joiner:` or `max_parts:`. `flow.knowledge: off` turns the library off; `flow.calibrate: off` the calibration. This is the
only layout (D783): a top-level `gate:`, `stages:`, `space:`, `seeds:` or `knowledge:` is a key a
document does not have, and `budget` takes no `finalists` or `calibrate`. A document of an earlier form
is brought to this one by `flux task migrate FOLDER [--write]`, or by an admin from Admin › Loops' "Migrate documents of an earlier form" (D811, D816):
each change said, a result written only when it loads, the original kept as `<file>.orig`; a `world:`
or `hooks:` is said for a person to rewrite as commands.

## Check and run a problem

```bash
flux task check DOC          # DOC: the folder, or its problem.yaml; what it needs, what it will skip; runs nothing
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

**A search works between passes** (D738). A design is named by its settings (D743): a value that
is a word stands alone, any other carries its knob (`list_sieve-wheel=1`). With `flow.orchestrate` and its `space`, a pass tries one design, the
search's next pick from what the passes before it measured: a 6-point sweep is 6 passes
(`--passes 6`), an anneal or a genetic population carries on from pass to pass. `budget.batch: N`
lets one pass make, check and measure N of the search's picks side by side (worth it when the tools
take many designs at once). A new run starts the search again from its record.
`budget.parallel: N` (D747) runs N passes at once, each with its own design and its own branch in the tree;
the next N start once all of them ended, and the run ends with one decision over every pass. On a server
an admin allows it per loop (Advanced › Allow parallel work); otherwise passes run one at a time.

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

**Papers.** A loop reads a library: the shared one (`flux/mentor/knowledge/library`, or `FLUX_LIBRARY`) and
its own (D791): `library/` beside its document (papers and references, in any subfolders; `flux ask`
puts its attachments there). Every prompt gets the excerpts nearest the problem and a line per paper,
each coding agent a LIBRARY section with the papers and the files nearest its question (PDFs read with
`pdftotext`); `flux task check` says how many documents, how many are the loop's own, and who reads them.
The sandbox mounts each library read-only. Every paper of the library is digested once -- the loop's own
first, then the shared ones -- and the key points join every prompt; there is nothing to say for it, and
`flow.knowledge: off` turns the library and its digest off (D791). The digest runs first in each pass's
Setup (the tree's **Digest** leaf, before Reading: how many new, by whom, how many in all). A digest is kept in the run's home too (`~/.cache/flux/digests`, `FLUX_DIGESTS` elsewhere; on the web, each user's Flux home), keyed by the document's content: another loop or a later run takes it from there with no call, and only a changed document is digested again (D794), so no prompt waits on it (D771). An agent can digest instead of the model -- it reads each file
itself (a PDF's tables and figures too) and is gated by its Test like any agent the loop uses:

```yaml
flow:
  knowledge: {digest: opencode}  # who sums up library/'s papers: model (default), an agent, {by: agent, ...options} (D773, D830)
```

In the configurator's drawing it is the **Digest the papers** box (the model, or a coding agent; D784, D791). A Setup digests at most 8 new papers (`FLUX_DIGEST_PER_PASS`), the loop's own first;
the rest follow in later passes, and a digester that fails three times in a row waits for the next pass (D782).
An agent reads each paper from a `paper.txt` beside it; it needs a model served with room for its own
prompt (OpenCode's alone is about 33,000 tokens) and working tool calls -- else digest with the model (D785).

## The sandbox

`flux task run` and `flux ask` run in a container (D680), so neither an agent nor a
document's code (its commands, `golden.py`, scripts) can touch the rest of the
machine.

- **It sees:** the host read-only, meaning the system, `/nix/store`, the flux source, the
  executables on PATH and the problem folder. The same tools run, OpenCode and Claude Code
  included.
- **Paths inside:** new runs use `/sandbox/<loop-name>/` for the loop, including its `out/`,
  `workbench/` and feedback files; traces use `/sandbox-cache/tmp/flux-traces/`. Logs and agent
  working directories show these container paths. Flux's own checkout uses `/flux`, including
  `/flux/.nix-bin` on `PATH` and its source folders on `PYTHONPATH`, so imports and tracebacks
  use that location. The original host paths remain mounted for
  saved scripts and older records that use absolute paths. A host-looking path in agent output
  therefore names a permitted mount; it does not by itself indicate access outside the sandbox.
  Persisted run pointers still name the host files so history and stop controls work outside it.
- **It writes:** the record's folder, the problem's `out/` and `workbench/` (a sub-loop's in a folder: its
  parent's, whose folder it reads through -- D805), and the
  application's cache `~/.cache/flux/apps/<id>/`. That cache is shared by the application's
  runs: `tmp/` holds its traces, `cache/` its caches. Tool scratch lives in the container's own
  `/tmp`, in memory and gone after the run (`FLUX_SANDBOX_TMP_SIZE` caps it).
  Only `out/` and `workbench/` are written and kept; everything else of the application (its folder,
  its document) is read-only to the run. Scratch goes to `/tmp`, gone after the run (D764). An application
  that writes beside itself (a `history/` with its `.lock`) is to write into one of these instead.
- **HOME** is the application's (`apps/<id>/home`), holding the agents' sessions, their
  configuration and a copy of their login. Another application's cache is not there. `~/.ssh`, other repositories, the Docker socket and `~/.config/flux` are
  not there. The model settings and key come in through the environment.
- **Network:** the host's by default. `FLUX_SANDBOX_ALLOW=host,domain,10.0.0.0/8` restricts
  HTTP(S) to those destinations through the host's proxy, with no extra network container.
  Add `FLUX_SANDBOX_RAW_NETWORK=1` when the task needs native TCP/UDP as well. Only then
  does a helper install the allowlist firewall before the task starts; it requires host `nft` (nftables).
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

- **New loop**, four ways: **Create or upload** starts an empty loop when no files are selected,
  or imports a folder, files, or a single `.zip`. The empty loop opens in the configurator with
  a starter `problem.yaml`, README, and empty `library/`. A ZIP can have its files at the root
  or inside one containing folder (as Windows commonly creates); that containing folder is
  removed, while nested scripts, resources, and library folders are kept. **Clear files** returns
  to empty creation. **Configurator** (below; the form's **Loop name** is the loop's
  name and its problem's id; the checklist shows what is left to do; who does each step is folded),
  or **Agent**: name the loop, say what it should do, attach what it should read (a spec,
  a reference model, tests, papers), pick the agent (OpenCode, Claude Code, Codex or Flux's own
  model; one not installed says so). The agent writes the problem document and the files it names
  -- `flux ask --no-run` in the sandbox, with your model settings -- and the document is checked;
  nothing runs. The loop's Overview follows it (its log, Stop), and you review it before starting.
  **Clone a loop** copies an existing loop's problem without its runs.
- **Configure**, three ways: **Configurator**; **Direct edit** (the document's YAML as written,
  saved with its diff shown, its files beside); **Agent**: say what should change, and the agent
  revises the document and its files in place, keeping its name; its diff is shown when done. It
  works on a copy of the loop's own files: the record, the log and the workbench stay out of its
  reach. Not while the loop runs, and a loop does not start while an agent writes its problem.
- **The configurator:** the loop crafter inside the app (D686). **New loop** builds a document and
  creates the application from it, with the files it runs. Beside the form, **Files that go with
  it** lists the loop's own files (scripts, golden models, specs): open one to edit it, write a new
  one, drop files or folders, delete one. A file the document names as `{home}/…` that the loop
  does not have is said to be missing, and a click writes it. **Configure**, on an application, reads its document back
  into the same form and saves it. What the form cannot say (its own stages or
  settings, `params`, a failure pattern, objectives with a stage or tie
  of their own) is kept exactly as written and listed beside the file. Comments are not kept.
  A save first shows what it changes, line by line, and writes only when you confirm it.
- **Extra sandbox packages:** in a loop's **Settings › Loop › Advanced**, an admin can list
  **Nixpkgs packages** (for example `jq`, `ripgrep`, `python3Packages.numpy`) and **Nixchip
  packages** (for example `verilator`, `systemc`), one attribute name per line. These resolve
  from Flux's locked `nixpkgs` and `nixchip` inputs, not a separate channel. Names can refer
  to nested attributes; arbitrary Nix expressions and flake URLs are not accepted.
  Packages are fetched or built on the host before the next sandbox launch, then made available
  to that loop's runs, Check, and agents. The first launch can take longer while tools build;
  failures appear in its log. Cached profiles keep their store paths alive across Nix garbage
  collection and refresh when Flux's lock file or the selection changes. Executables, headers,
  library and pkg-config search paths are added; matching-version Python modules are also
  available. Nixchip tools get their `NAME_HOME`, `NAME_BIN`, `NAME_LIB`, and `NAME_INCLUDE`
  variables; Verilator's conflicting `VERILATOR_BIN` is unset, as in Flux's dev shell.
  No extra runtime `LD_LIBRARY_PATH` is added, to avoid mixing Nix and host libc.
  Clearing the lists restores the default tools on the next start. Packages apply only while
  sandboxing is enabled; the host needs Nix and a Flux checkout with `flake.nix` and `flake.lock`.
- **Loops list:** search, filter by state (running, idle, failed), order by activity, name,
  accepted designs or decision. **New loop › Create or upload** takes a dropped folder, files or a `.zip`, of any size (a progress dialog Escape does not close; Cancel stops it, and a file
  cut midway is discarded): the page sends it in batches (300 files, 40 MB) and a file over 40 MB in
  parts of 32 MB, with a progress bar. A single ZIP is sent intact for the server to unpack.
  A loop holds up to 100,000 files and 8 GB of its own; one
  request from elsewhere, 900 files and 256 MB (a zip, up to the loop's limits). A failure is said
  above the dialog, as is any error the page did not expect. Each loop shows its accepted and measured designs and the
  decision's number on the first objective (✓ or ✗ against its limit).
- **Applications:** upload files, a folder or a `.zip`, or write the YAML in the page; add
  files (or a `.zip`) to an existing one. Files and folders can also be dragged onto the page:
  a dropped folder keeps its paths, and names the new loop. Every file can be viewed, edited and downloaded. **Check the document** runs `flux task check` in the
  sandbox.
- **Start and stop:** a start takes passes (or "until I stop it") and screen only, and is
  always sandboxed; its network is the admin's and the loop's Settings' (D884). The dialog offers the last start's choices, and runs the
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
- **A loop's page** (D713: six tabs -- Overview, Live, Results, Agents, Files, Settings -- some with views
  under them: Live › Tasks, Log, Timeline, History; Files › Loop files, Workbench; Settings › Problem (the
  configurator, Direct edit, an agent), Variables and sharing (with Advanced and, for the owner, Delete
  at the end). The header has Start/Stop and Check. **Ask** is a button at the bottom right that opens a
  panel over any tab: the questions and answers, and a new question. Escape closes it. An address
  names the view (`#/app/x/live/log`, `#/app/x/settings/problem/edit`, `#/app/x/ask` opens the panel), and
  the old ones (`/log`, `/timeline`, `/agent-turns`, `/workbench`, `/configure/...`) lead to their new places.
  What each view shows:
  - **Overview:** the loop's state, designs measured (accepted, failed), passes on record, the
    objective, and the decision's numbers against the limits -- the decision is the record's latest
    pass's (D809), so a loop that runs for days has one from its first pass on, with why it was chosen (D815: a limit
    is a floor to meet; among the designs that meet every limit the next objective without one decides) -- and the best 3, ranked
    by the objectives' own rule over the deepest stage (also before any decision). A best-so-far chart per objective
    shows each measurement in order, the best as a step line, the limit dashed and the passes
    marked. Also the agent's open question, the latest notes and the newest workbench entries (under
    the decision), and the last pass: when, its measurements, its conclusion.
    While the loop runs, it redraws once a minute. A figure gives the model and agent turns, their
    time and tokens. Under the decision, the best three designs: the decision, then accepted
    before failed, the deepest stage, then each objective without a limit.
  - **Long runs** (D759): Live › Tasks opens on the last 30 passes ("Earlier" loads the rest), the log on its
    last 2 MB ("Load all" for the rest). A run's journal keeps only its tasks' starts and ends; what runs now and the standings
    are `live.json` beside it, rewritten each second (D761), so an hour-long pass does not grow it.
    Timeline and the Agents tab read their files as they grow (D779). A resumed loop keeps a design its
    judge already admitted -- the gate, the files it names, the tools and Flux unchanged -- instead of
    re-verifying it every pass (D778); any of them changed, it is re-verified once.
    The first look is also never more than 24 MB (D762): a start of a few passes of hours each opens on the
    oldest whole pass that fits, or on the newest pass's tail ("Earlier" says what was left out). `marks.jsonl`
    beside the journal says where each pass begins; a journal from before it is read back at most 88 MB.
  - **Talk** (D758): the button on every loop page opens the drawer -- a note to the running loop (it joins the
    next prompt, or answers its agent's open question; the notes so far under it) and questions to an agent about it.
    A note or a question is removed by its bin, top right, once confirmed (D808); a note the loop has not read yet
    never reaches it, one it read stays in its record. In Files, a path's folders are links (D808).
  - **When something is wrong** (D757): a failed start's Overview says why (its log's own lines, with the log a
    click away); Direct edit says before saving that a document does not load; a tool that broke fails its leaf;
    an ended session is said on the way to the login.
  - **Live:** the loop as it ran (D739, D742): this start's **Setup**, a branch per **Pass** (named by
    the design it made) whose leaves are the configurator's boxes in a word each, in the order they
    ran -- Search, Design, Check, Measure, Choose, Critic … (the box's full name on hover; ×N when it
    ran several times in a row) -- each with what it produced, and an **End** (Reason, Decision,
    Lessons, Established, Design file, Answer file); a loop in parts has a branch per **Part** (its
    passes inside) and **Whole**. A resumed pass's re-checks of what the record holds are its Setup leaf (D755). A leaf opens its work in tabs -- Live or Output, Input, Log, Every
    field -- and lists its tasks when it has several. From the run's journal `events.jsonl`, or as a
    graph (**Tree | Graph**, remembered per browser; D726: the graph is the loop's own drawing, as
    the configurator draws its document, unused boxes dimmed, a running one pulsing; D727: a step
    bar -- ⏮ ◀ slider ▶ ⏭ -- goes through the selected box's runs (D729; every visit when none is),
    and a box or a run opens what worked in it -- the agent or model at work, else a tool; D730: the
    selected run's tasks show under the bar as an indented tree, a click opening one). An agent's task
    puts its reply first, then what it did (D731). By
    default it follows the running task (an agent first) and collapses finished branches, and it
    can be searched. Select a task for its parameters, live fields (an agent's commands, output,
    thinking) and output; following, it shows the running task, and at rest the one that ended
    last. A tool's task (`tool:<program>`, named by its step and candidate, e.g. `stage bench
    list_sieve-0`) shows its exit, time, folder and command, and the ends of its stdout and
    stderr, live while it runs; a non-zero exit is marked in the tree. A coding agent's task shows its conversation in order (D712): its words as text, its
    thinking and each tool call folded (a call opens on its input, field by field, and its output;
    a failed one is red; what you open stays open as it updates), the latest at the bottom.
    Thinking and tool input/output boxes keep their individual scroll positions when other
    logs update, including in fullscreen. A box at its end follows new output; scrolling upward
    keeps your place. Detail tabs remember their positions separately for each task. The
    Agent turns tab shows a finished turn the same way, the prompt folded below it. Before D712, it showed: its model and version, status, output and
    exit; its thinking, the commands it ran, the last command's output and its words, each a stream
    that follows its end and keeps its place when read upward. Under it, the log as it grows, coloured as the Log tab, problems only on demand, and each line's
    time with **times** (D732: a run writes every line with its time; the setting is one for both logs,
    kept per browser, on unless turned off; lines from before have none). A time carries its day, `Oct 05 14:03:22`
    (D816), so a run of several days reads. A line
    docked at the bottom sends notes to the loop (Enter sends, Shift+Enter breaks the line); when
    the agent asks, it shows the question and answers it. Standings show as counts, the frontier and the parts. It shows the
    latest start's tree.
  - **Log:** the loop's output as it grows, numbered, problems highlighted, each start marked.
    Only the lines in view are drawn, so a log of a hundred thousand lines scrolls as a short one
    (with wrap on, the last 3000).
    Show one start or all. It can follow (it pauses when
    you scroll up), wrap, filter by text or `/regex/`, show problems only, and download.
  - **History** (under Live): choose an older start to replay every retained pass, tool and agent task,
    read its text output, or open its agent conversations with prompts and tool calls. Results and reports
    can also be selected by recorded campaign, through the chosen start's end; a resumed campaign includes earlier measurements. Removed data
    is shown as unavailable. Links to a selected start can be bookmarked (`#/app/x/live/history/<start-id>`).
    **Raw** opens complete logs, journals and transcripts; the text log preview is limited to 1 MiB.
  - **Raw and Fullscreen:** file, log, result, task and agent viewers have these controls. Raw files stream
    their complete content as plain text in a new tab, including files too large for the editor.
    Raw results include every retained measurement and design, without the chart and table limits.
    Fullscreen keeps the current view and unsaved edits; Close or Escape returns to it.
  - **Results tables:** Results and Overview's Decision table always use a compact layout.
    Shortened design names and ✓/✗/… status badges keep rows small; measurements use green/red
    text for limits, without extra checkmarks. Full names, stages, times, units and limit status
    remain on hover. Measurement headers use short diagonal labels that preserve the start and end
    of long names, with edge space to prevent clipping; Results headers still sort. Both tables keep
    every measurement column visible.
  - **Absolute / Relative (%):** Results and Overview's Decision table share a remembered toggle.
    Relative shows percent change from the latest baseline for the same metric, stage and design
    group (baseline = 0%; a value 30% lower = −30%). Without a matching baseline, it uses that
    group's median at that stage, across all measured designs. Hover for the absolute value and
    reference. Missing values or a zero reference show — because percent change is undefined.
    Graphs show baseline measurements as gray reference lines; search designs remain points.
  - **Timeline:** where one start's time went, from its journal. Every phase that does the work
    (a tool, an agent, a model call) is a bar in a broad work category, using the tree's vocabulary:
    Setup, Plan, Search, Design, Check, Measure, Choose or Critic. Builds and tests belong to Check;
    measurement stages share Measure. Task names remain in the tooltips. One reserved colour
    highlights agent activity within each category, including tools run under an agent.
    Per category (D772): calls, the average
    and the longest call, the total (the wall clock its calls held, side by side counted once) and its
    share of the wall clock; the share bar stacks work without an agent and work with an agent,
    labelled **total% (agent%)**. Both percentages use the start's wall clock, and agent time is
    included in the total. Summed time and how many ran at once appear only where calls ran side by side.
    Choose a start and a pass. It redraws once a minute while the loop runs.
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
  - **Sharing** (on Settings, the owner's): share the loop with another user to **watch** (its
    runs, log, results, turns, files and settings) or to **edit** (also change its files, document
    and variables, start, stop and send it notes). An editor's runs are the owner's loop: its record,
    the owner's model settings, keys and limits; the log line of each start says who started it.
    Deleting and sharing stay the owner's. Loops shared with you are listed under **Shared with
    me**, with what you may do; you are told in the bell when a loop is shared with you or no longer
    is, and **Leave** takes one off your list (its owner is told). The bell watches shared loops too,
    and is each user's own.
  - **Ask:** a question about the loop -- why it stalls, which design is best and by how much, what
    to try next -- answered by an agent (or Flux's own model) that reads it: its document and
    files, a copy of its record (to query as it likes), its log. It runs in the sandbox with the
    loop's folder read-only and the run's network; it changes nothing. Answers are kept with the
    loop, newest first, in Markdown; one is answered at a time. Anyone who may edit the loop asks;
    a watcher reads. From the command line: `flux consult "<question>" --loop <folder> --out
    <folder> --author opencode`.
  - **Settings:** the loop's environment variables (over the user's and the server's, which are
    listed under them), and its advanced settings: run in the sandbox or on the host, memory,
    CPUs, processes, scratch size, and **Allow parallel work** (D740, D741): off, a loop runs one tool
    and drafts one part at a time whatever its document asks (the log says so); on, the document
    says how many (`budget.workers`, `parallel_parts`). Only an admin changes the advanced
    settings (also when creating a loop); everyone sees them. **Allow raw TCP/UDP** is off by
    default and takes effect on the next start. Under an allowlist, enabling it adds a firewall
    helper for direct TCP/UDP to the allowed destinations. Off, HTTP(S) uses the proxy alone.
    The choice also applies to the app's Check and agent connection Tests. Open networks keep their existing access.
    Owners also have **Reset** beside **Delete** in the same box. Reset requires the loop and its
    agents to be stopped. Its confirmation lists the full paths it clears: the loop's `out/`
    (all results, measurements, decisions and pass records), `runs/` (full and archived logs,
    agent output, answers and notes), `workbench/`, `.author-work/`, and its own sandbox cache
    under `$XDG_CACHE_HOME/flux/apps/` (or `~/.cache/flux/apps/`). Saved start history and the last
    check/start status also go. This cannot be undone. The problem document, source files,
    library, settings and sharing stay; the next start begins fresh. Delete removes the whole loop.
- **Agents and models (Admin › Agents and models, D756, D807, D814):** one tab per tool -- Flux's own model and the agent by default; each agent, its program and login above the model it uses and the variables only it gets, one Save; the other providers; **Every agent** (the server's variables, which every run and agent gets); **+ Add an agent**. A user's Account has the same tabs (**My agents and models**): each agent's login and Test, its model, its own variables; their variables for every agent. In detail: OpenCode, Claude Code and Codex, and any the admin adds -- **Add an
  agent**: a name (lower case: what a document says, `generate: nga`), a kind (opencode, claude or codex: how it
  runs) and its program (a path) -- e.g. a company's own OpenCode beside the plain one. An agent is offered to users
  (pickers, Agent logins, its Models tab) only where its program is found and runnable; here every agent is listed,
  found or not. Per agent: its name shown, program, login command, extra arguments, where a build of its own keeps its login (D760: e.g. `.cache/nga/auth.json`;
  `~/.cache` is the user's own); the files every home starts with for it; the hosts it needs on the
  allowlist; whom it is ready for (each user's Test); an added agent can be removed (its settings and variables,
  the server's and every user's, go with it).
  **Seconds per turn** (D893): each agent's time limit, set here for everyone and on a user's Account for
  their own runs (theirs wins, whatever endpoint they use); a document's `timeout_s` wins over both
  (`generate: {by: codex, timeout_s: 3600}`); unset everywhere, 1800. A document names an added agent as it
  names a built-in one, in any box: `generate: nga`, `orchestrate: nga`, `generate: {by: nga, timeout_s: 7200}`,
  `knowledge: {digest: nga}`. The server tells its runs which agents it added (`FLUX_AGENTS`), so such a
  document loads in the web; `flux task run` on the command line needs `FLUX_AGENTS='{"nga": "opencode"}'`
  and `FLUX_NGA_BIN` set.
  **Endpoint recovery:** if an agent reports a transient API error (502/503/504/529 or a reset/refused
  connection), Flux waits for its configured endpoint and sends **continue** to the same session.
  An agent stuck after the error is stopped after 15 seconds without progress; only its subprocess
  is replaced, keeping the loop, files, pass and session. Each readiness wait lasts at most five
  minutes, with at most three continuations within the original turn's time limit. Recovery is
  visible in the task tree and transcript. If the endpoint cannot be probed, the continuation
  tries the pending work after a pause. Agents without resumable sessions keep their usual failure
  handling. Tool failures, ordinary debug output, authentication errors and allowlist refusals
  do not trigger recovery. Direct model calls retry the pending request with backoff for up to
  five minutes; an incomplete stream is discarded before any tool calls are executed.
- **Maintenance (Admin › Maintenance, D885):** clean-up on a schedule, as Gitea's cron tasks -- each
  task on or off, every so many minutes, hours or days, its settings, its last result and Run now.
  Clean scratch (FLUX_TMPDIR's old, unused entries; off until turned on, as the folder is the
  machine's), Reap containers (every minute), Clean idle caches, Disk alert (the admins are told
  once when a disk runs low), Compact databases (checkpoint, optimise, vacuum), Condense run logs
  (a log over a size keeps its recent part, the rest gzipped beside it), Prune server tables, and
  Prune stale records (rows measured under other inputs, D853; reports, deletes when told; off).
  Additional tasks (schedules off until enabled): **Check database integrity** runs read-only
  SQLite checks on the server database and stopped loops' `out/*.db` records; **Validate loop
  documents** loads saved problem documents and their referenced parts without running tools
  or agents. **Back up server database** saves `flux-web.db` and `secret.key` (if present) in
  `<server-data>/backups/server-<timestamp>/`, accessible only by the server's OS user. It keeps
  the latest seven backups by default; **Edit** changes the retention count and schedule.
  These snapshots cover accounts, settings and run metadata. Back up loop folders and sandbox
  caches separately to preserve source files, results and full agent history.
  Tasks that act on loops run over every loop or the one picked, and a loop's owner can run
  them on it from its Settings. A running loop is never touched; every run is in the audit trail.
  **Admin › Loops** automatically shows **Old documents** when migration or manual rewriting
  is needed, with individual migration controls and **Migrate all**. The box disappears once
  every document is current; originals are kept as `<file>.orig`.
- **Sandbox (Admin › Sandbox):** what every container gets.
  - **Network:** open, or an allowlist (hosts and their subdomains, `*.domain`, IPs, CIDRs). With
    an allowlist HTTP(S) uses the host's proxy by default, without a helper container. An admin
    can enable **Allow raw TCP/UDP** for an individual app under **Settings › Advanced**.
    When enabled, IPs and CIDRs permit both protocols at all ports. Flux resolves allowed domains on the
    host and admits their addresses before answering DNS; subdomains work the same way.
    When a name passes through an IP/CIDR rule, only its matching addresses are admitted.
    Native traffic is filtered by destination address, so names sharing an admitted IP share
    that permission. HTTP(S) retains the host's proxy, including its corporate upstream.
    Native UDP needs a route from the host; an HTTP upstream cannot carry it.
    The separate helper needs host `nft` (nftables); the task keeps all capabilities dropped
    and cannot alter the firewall. If the helper fails to initialize, the pass continues
    through the existing isolated HTTP relay; native TCP/UDP remains unavailable until a
    later run can start the helper.
    Optionally the model endpoints' hosts join the list. A loop's Settings (admins) may add
    hosts for that loop; a start adds none (D884). An empty
    allowlist reaches nothing. Users never see the admin's hosts (D716): a run's log says the network is
    limited and by how many entries, and the container's environment does not carry the list. Admins
    see it in the Sandbox tab.
    A program that ignores the proxy settings still looks its host up: under an allowlist the
    container resolves through Flux (D717), and a name the list does not allow is refused and
    shows in the admin's audit as "a name lookup". With raw networking enabled, a bare IP is reachable only when an IP/CIDR
    rule permits it or an allowed name has resolved to it. Other bare-IP traffic is dropped
    by the firewall and does not appear in the name-lookup audit. Loopback stays inside.
    A blocked destination fails that request; it does not stop the current pass or the
    campaign. The loop records failed attempts and continues with another candidate or
    an allowed service, within the pass's ordinary step and time budgets.
  - **PATH:** each directory on the runs' PATH is mounted read-only. The server user's login PATH
    (their own shell's, interactive and login) can be added, and further directories.
  - **Slow answers** (D774): a request over half a second is said in the server's log (`flux serve: slow: GET /api/… 1.23 s`;
    `FLUX_SLOW_S` sets the threshold) -- where to look when pages are slow.
  - **Leftovers** (D768): a login's or Test's container ends itself (Podman's `--timeout`); each minute the server removes
    sandbox containers no process runs any more, including stopped leftovers and their network helpers (in the audit).
    A network helper stays while its task or client still exists; an agent's turn ends with everything it started.
    A login that ends well is tested at once (Account shows "testing…", then the result).
  - **Homes** (D744): every user has a home of their own, `<data>/users/<name>/home` (0700, no system
    account): their runs' HOME, writable at `/home/flux`, kept -- their agents' settings, logins and
    sessions. A run uses its loop's owner's home -- a shared loop runs on the owner's agent logins, whoever starts it (D769). Every home starts with the admin's list of
    paths from the server account's home (default `.config/opencode` and a corporate OpenCode's
    plugin parts and state under `.local/share/opencode/` and `.local/state/opencode/`, D765), copied where it lacks them,
    never over what is there; no one's login is among them. The server account's own home path is
    scratch inside (a tmpfs, with its PATH folders and the loops' caches mounted on it).
  - From the command line: `FLUX_SANDBOX_HOME` names the home; without it, a run's home is
    `~/.local/share/flux/home`, started from your own agents' configuration and logins;
    `FLUX_SANDBOX_NET=allowlist` and `FLUX_SANDBOX_ALLOW` set the network;
    `FLUX_SANDBOX_RAW_NETWORK=1` enables native TCP/UDP through the firewall helper.
  - A run's variables reach its container in a file of its own (0600), never on the command line, where
    any user of the machine could read them (D745).
- **Environment variables:** the server's (Admin › Models and variables), a user's (Account), a
  loop's (its Settings), applied in that order, a secret stored encrypted and never shown again.
  They reach the run inside the sandbox whatever their names (`FLUX_SANDBOX_PASS`). The
  sandbox's and the loop's own variables, the process's basics (`PATH`, `HOME`, `LD_*`, …) and
  the model settings cannot be set this way.
- **Usage:** the Account page gives your turns, time and tokens over all your loops; the Admin
  page, every user's.
- **Streams:** the Live and Log tabs say whether their stream is live. A dropped stream is opened
  again where it left off (no line twice, none lost), waiting up to 30 s between tries, and a
  banner says when the server cannot be reached. A stopped server waits at most 3 s for open
  streams; a restarted one finds its running loops again. The installed user service stops only
  the server on updates (`KillMode=process`); its loop sessions and containers continue running.
  For an older service installation, re-run `scripts/install-service.py` from `flux/` with the
  same server arguments, then `systemctl --user daemon-reload` before restarting it.
- **The agent by default** (Admin › Agents and models, Account): who writes problems and answers
  questions unless chosen otherwise (an agent's name, or model); a user's own over the admin's. An agent's
  program is Admin › Agents' (D807), for every run; its folder goes on the run's PATH, so the sandbox mounts
  it (without one, a built-in agent's own name on PATH, or the machine's `FLUX_<AGENT>_BIN`). On the
  command line (`flux.env`) the sandbox mounts the program itself, the file alone with its link followed,
  and names it so inside (D804): a link in `~/.config/flux` works without the key beside it going in.
- **Models (Admin › Models, Account):** a tab per tool (D721) -- Flux (its own model, and the agent by
  default; its endpoint is any OpenAI-compatible one -- a hosted one, OpenRouter's, a local Ollama's /v1, D817), one per
  agent offered (D807); `OLLAMA_BASE_URL`, `FLUX_LLM_MODEL` and `OPENROUTER_API_KEY` are no settings, but variables when
  one wants them (the command line reads them as before); a tab with
  settings of its own is marked •, one Save covers them all. Each agent's tab is its own: an endpoint, a
  model and a key as its kind reads them (`FLUX_<AGENT>_BASE_URL`, `_MODEL`, `_API_KEY`) -- an OpenCode gets
  them as a provider (the built-in OpenCode, with none of its own, Flux's model's), Claude Code and Codex as
  their `ANTHROPIC_*` / `OPENAI_*` variables and a `--model` -- and **variables for that agent alone**
  (an `ANTHROPIC_API_KEY` for an OpenCode, a whole `OPENCODE_CONFIG_CONTENT`): a run hands them to that
  agent only (`FLUX_<AGENT>_ENV`), never to another nor to Flux itself. A variable for every agent at once
  is an ordinary environment variable (the server's, a user's, a loop's), which each agent gets whatever its
  name. The admin sets them for the server; on their Account a user sees the
  server's values in grey and may set their own. A user who names their own endpoint in a group
  gets none of the server's values of that group. Keys are stored encrypted (`secret.key` beside
  the server's data) and never shown again. With nothing set, runs use the machine's own
  configuration (flux.env, OpenCode's and Claude Code's own).
- **`language:` is optional** (D832): unsaid, the tools the checks and stages name tell it (`flux rtl ...`: SystemVerilog, a ChampSim build: C++); say it when they do not -- a script of your own, `flux prog` -- or the design is a `.txt` file.
- **On a phone** (D856): the account items are in the ☰ menu; a loop's Overview opens on its decision; Results opens on its designs, the charts below.
- **Evidence follows its inputs** (D853): editing a file beside the document (a checker's helper, a data file, a bench script) or the params re-checks what was admitted and measures again on resume; the document's objectives do not. After this update a resumed search re-measures once.
- **Files stay in their loop** (D852): the server never follows a link out of a loop's folders -- a raw log, a record, a run pointer, an inbox that leads elsewhere is refused; a replaced file is replaced, never written through a link.
- **One pass, one design** (D845): a loop of one design builds a design every pass, refining the standing one or exploring a new one -- the orchestrator chooses, else the rules (explore after `budget.explore_after: 2` passes without a new decision); it never rests. The best design measured so far stands and is the decision, whichever pass made it (D861).
- **Novel designs** (D839): once a loop is at rest, each exploring pass asks for a new design that beats the standing one, not an edit of it; every fresh draft is shown what was tried (best first) and a design already measured is refused before it is built.
- **Token prices** (D835): Admin › Agents and models, Flux tab (Flux's own model) and each agent's model fold: price in and out, USD per million tokens; Insights and a loop's Agents tab then show the cost. A user's own prices count only with their own endpoint. On the command line: `FLUX_REMOTE_PRICE_IN=0.5` and `FLUX_<AGENT>_PRICE_OUT=...` in flux.env.
- **Settings save as you change them** (D833): Account, Admin (agents, sandbox, a user's running limit) and a loop's Advanced have no Save button; a mark beside the field says saved or why not. A key saves when you leave its field. The document (Direct edit, the configurator) keeps Save.
- **The configurator** (D826) is seven steps -- the problem, checks, measurements, objectives, who does each step, more (budget, search, parts), review and save -- one at a time, Back and Next, Save on every step when editing a loop.
- **Cloning** (D824, D828): "Clone…" on a loop (or New loop › Clone a loop) makes a new loop of yours with its problem and none of its runs, its workbench when asked.
- **Invitations** (D818): an admin adds a user without a password (Users, or `flux user add NAME --invite --url https://flux.example`) and gets a link to send them -- it lets them choose their password (10 or more characters) and logs them in; until then the account cannot be used. **Password reset link** (Users, or `flux user link NAME`) is the same for an existing user: their password works until the link is used, and their sessions end then. A link works once, for a week; a new one replaces it. There is no mail: the admin sends it.
- **Kinds of user** (D734; the Users tab, or `flux user add NAME --role internal|external|admin`, `flux user role NAME --role ...`):
  - **internal** (the default): their runs use the server's model, agent and environment settings, under their own.
  - **external**: their runs get none of the server's or the machine's model and agent settings, nor the server's
    environment variables -- only their own (Account) and the admin's agent programs; the network rules apply to
    everyone. Every user (D744) has a home of their own (see Homes); on Account, **Agent logins** runs each
    agent's login command (the admin's, in Admin › Agents; by kind, `opencode auth login`, `claude setup-token`,
    `codex login`) in the sandbox, with that home writable, in a small terminal: its output with links, a line to
    type, ↑ ↓ Enter Esc Tab Ctrl-C. What it writes stays in their home, where their runs use it. Claude Code's
    `claude setup-token` prints a year-long token instead of keeping it: the login saves it to the user's settings
    (the agent's `FLUX_<AGENT>_OAUTH_TOKEN`, encrypted; its runs get it as `CLAUDE_CODE_OAUTH_TOKEN`) and never
    shows it (D748). For Codex set its login command to `codex login --device-auth` (a code to enter on the site; the default waits for a redirect to localhost). Inside the
    sandbox Codex runs without its own (bubblewrap cannot start there): the container is its sandbox (D750).
    **Test** (D751) checks an agent for you -- its program, your login or key, one short answer
    (`flux agent test <agent> --live` on the command line): a loop, a new loop's author or a question uses an
    agent only once its test passed for whoever starts it; `task check` says which agents a document needs.
    **Each day** (D807) an agent a user tested is tested again, one at a time: an answer keeps its login fresh;
    a failure is told to the user (the bell) and their loops that need it wait until a Test passes again.
  - **admin**: internal, and the admin pages.
- **Admin** (tabs: Loops, Insights and audit, Applications, Resources, Sandbox, Agents and models, Users):
  - **Loops' "Migrate documents of an earlier form…"** (D811, D816): every loop's documents of an earlier form -- what each would change (the steps, the
    result), where it goes (`<id>.problem.yaml` with an `id:` becomes `problem.yaml`; the record follows a
    renamed id) -- migrated one loop at a time or all at once; a running loop is left until stopped; a loop's
    Start says when its document needs it.
  - **Insights and audit** (D766, D816, D819; a sub-tab each -- Failures, Usage and disk, Endpoints and network, Audit trail): over the last day, 7 or 30 days -- the starts that failed with why (their log's
    words) and the agents' Tests that failed; turns, tokens and cost by user and by agent or model, a bar a
    day, and the loops that used most; each model endpoint and agent with its turns, failures, median and
    slow (95%) time and its last failure; the hosts the sandboxes refused, by which loops; the disk by user
    (home, loops, the largest). An agent reaching its local time limit counts toward turns, timing and usage,
    but not endpoint failures or the last failure. All from what the server keeps already: nothing new is recorded for it.
  - **Applications:** the `applications/` folder of this Flux (or `FLUX_APPLICATIONS`), each with
    what it asks and its size. **Use** makes one a loop of the admin's: its files hard linked
    (copied across disks), its record, log and workbench its own; an edit replaces a file rather
    than writing through the link, so the folder never changes. **Refresh** takes the folder's
    files again and keeps the record.
  - **Loops:** every user's loops, with controls over all of them. **Pause new starts** (with a
    reason users see; running loops go on), **stop every loop** after its pass or at once.
    **Restart all active loops** interrupts current passes, waits for their processes to exit,
    then resumes from the records with each loop's screen-only mode and selected problem document.
    Finite runs use the remaining budget: 10 requested passes with 5 completed restart with 5;
    run forever stays unlimited. Counts are read after stopping, exclude baseline pass 0 and
    the interrupted pass, and reset for the replacement run. Repeated restarts keep subtracting
    completed passes; exhausted budgets stay stopped. Files, results, logs and records stay;
    idle loops stay idle.
    Restart requires starts to be resumed and respects owners' running limits and agent checks.
    Loops that cannot restart are listed individually; a process still stopping after 30 seconds
    gets no replacement, so two runs cannot overlap.
  - **Resources:** the machine (CPUs, load, memory, the disks of the server's data, the caches
    and the sandbox storage), and over time: `flux serve` samples it once a minute (load,
    memory, disks, the containers' CPU and memory, loops running), kept a week, charted over the
    last hour, 6 hours, day or week; hovering a chart shows the sample under the pointer, its time and values. The sandbox's containers with CPU, memory and PIDs, each with its
    loop (a `flux.app` label); a container no running loop owns is "left behind" and can be killed.
    Every loop's disk: inputs, record, log, workbench, sandbox cache. Clear a loop's tools' cache
    or its past passes' scratch (the journal, transcript and record stay); delete a cache no
    loop owns (a deleted loop's, or a `flux task run` of this machine's user).
  - **Users:** role, a running limit per user (empty: the server's `--max-running`), and usage.
- **Files and the configurator follow `.gitignore`:** the loop's `.gitignore` files, read as git
  reads them, hide what they ignore; **show ignored files** on the Files tab lists it greyed. `.git`
  is never listed nor read.
- **Pages** carry breadcrumbs (Loops › owner › loop › tab) and grey placeholders while they load.
- **Accounts:** a name is the same whatever its case and the spaces around it (as a phone types
  it); a password is exactly as typed. `flux user add` says which data folder it wrote; `flux
  serve` says its data folder and its accounts: the two must be the same folder. Passwords are hashed with scrypt; five failures from one address lock the name there for ten minutes, fifty
  from all addresses lock it everywhere;
  sessions live in an HttpOnly, SameSite=Strict cookie; every change needs the `X-Flux` header.
  A user sees only their own loops and those shared with them. An admin manages users, sees and edits
  every loop -- its files, problem and variables, starting and stopping it, as an editor of it would; it runs on
  its owner's agents and settings (D812) -- and the audit trail. The audit also
  lists each host a loop's sandbox refused (network allowlist), under the loop's owner, once per
  host and port per run. The audit narrows by what happened and by whom (D723), and searches
  the details. What happened comes in groups (D724): users and sign-in, runs, loops and their
  files, sharing and loop settings, server, network, and other; the filter offers the groups only (D733),
  each row keeping its exact kind.
- **Every check at once** (D822): `python3 tests/check.py` (from `flux/`, in the dev shell) runs ruff, the unit suite, the heavy tests and the browser test side by side, a line each as it ends (about 2.5 minutes); `python3 tests/check.py unit e2e` some of them; `FLUX_E2E_STEPS="login refused,login,invitation,insights"` a few steps of the browser test (D821; the first two log the browser in).
- **Browser test:** `python3 tests/e2e/web_ui.py` (from `flux/`, in the dev shell) starts its own
  `flux serve` with three users and walks the pages in headless Firefox. It covers login, New
  loop, upload, every tab, Files and `.gitignore`, Direct edit, variables, sharing, start and
  stop, a watcher, the admin tabs and the dark theme. Each page is checked for script errors and
  red notices. It prints a report and exits 1 on a failure, with screenshots in
  `~/snap/firefox/common/flux-e2e/shots/` (`FLUX_E2E_HOME` moves it). `FLUX_E2E_SANDBOX=1` runs
  the loops in the sandbox.
  It also runs passes at once, an agent's Test with a stand-in Codex and the main pages at a phone's width
  (D752) -- where nothing may be wider than the screen: lists stack, tabs and logs wrap (D754). The task tree's building (`static/looptree.js`) is tested apart under node on recorded journals:
  `tests/unit/test_looptree.py`.
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
proto` prints where a prototype fails, grouped by the input's sign and exponent. A prototype that
passes is spelled as SystemVerilog by the loop (py2sv): integers, `if`/`elif`/`else` and early
returns, `for` over a constant range, helpers (inlined), module-level tables, and tuples -- a helper
returning several values, `s, m, k = unpack(x)`, `(a, b) if c else (d, e)`, `len(T)`, a tuple read
at a computed position (D804, D806). What it cannot spell is refused with the construct named.
A golden with `CLOCK = True` and `LATENCY = N` makes it a pipeline (D864): the spelled datapath is
cut into N register stages of about equal estimated depth, with `clk`, `rst_n`, `start` and `done`
(`start` N edges later); the prototype stays the plain algorithm. Changing N re-spells every
admitted design from its verified prototype at the next pass.

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

task = load_task("applications/adder16")        # its problem.yaml; the id is `adder16`
result = run_loop(PromptProblem(task), request_for(task, db="adder16.db"), proposer=None)
```

What a document cannot say is a command beside it -- a check, a measurement, a search
(`orchestrate: {command}`), a composition -- ([extending.md](extending.md)).
