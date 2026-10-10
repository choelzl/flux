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
| use the web interface | [web.md](web.md) |
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

Runs, in a temporary directory: a command-driven sweep, the tools on PATH, the model server,
a problem the model writes, and a coding agent on PATH; `--full`
adds the README's first run. Prints PASS, FAIL or SKIP per check; exits 1 when one fails.

## Start a problem

```bash
flux new NAME                                           # a blank loop to configure
flux ask "what you want" --file spec.pdf                 # an author writes the problem for you
flux ask --tui                                           # the same, from a setup screen
```

- `flux new NAME` writes a loop's baseline (D825): `NAME/problem.yaml` with every part present and what goes there,
  a README of what each part of the folder is for, an empty `library/` (`--dir D`: into `D/NAME/`) -- nothing of a case.
  Copy a folder from `flux/applications/` for an existing application, including its check and
  measurement scripts. The [cookbook](cookbook.md) explains the different loop shapes.
- `flux ask` has an author (the model by default, or `--author opencode|claude|codex`) write
  the document and its files into `./out/ask_<slug>/`. It checks the document, runs it, and
  gives the author the report to revise for the next pass. Options: `--no-run` (write and
  check only), `--passes N`, `--screen-only`, `--dir DIR`, `--skill DIR`. The setup screen
  (`--tui`, or no prompt) takes the prompt, the files, the author and the passes; with
  "review first" on, you read the checked problem and type `run`, `stop`, or a note.

Existing loops using the removed `flux rtl` or `flux champsim` commands need an app-local
script and its tool sources. Copy `rtl.py` and `tools/` from `flux/applications/mul8/`,
or `champsim.py` and `tools/` from `flux/applications/prefetcher/`, and replace the command prefix with
`{python} {home}/rtl.py` or `{python} {home}/champsim.py`. Declare each measurement stage's
`metrics` and `needs` explicitly, and set the design `language`. The bundled applications
already use this layout; copying them includes their scripts.

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
language: systemverilog
flow:
  orchestrate:                                   # the search, its space and where it starts
    policy: sweep
    space: {arch: [ripple, kogge_stone], block: [2, 4, 8]}
  generate: {command: "{python} {home}/gen.py {artifact} {arch} {block}"}
  test: "{python} {home}/rtl.py test {artifact} --golden {home}/golden.py" # the gate
  measure:                               # the stages, by name, cheapest first
    screen:
      command: "{python} {home}/rtl.py measure {artifact} --stage synth --clock-ps 300"
      metrics: [fmax_mhz, area_um2, power_w, cell_count]
      needs: [yosys, openroad]
    confirm:
      command: "{python} {home}/rtl.py measure {artifact} --stage place --clock-ps 300"
      metrics: [fmax_mhz, area_um2, power_w, cell_count]
      needs: [yosys, openroad]
      timeout_s: 1800
  knowledge: {files: [spec.md], agent: opencode}   # what is read; who digests the papers
  select: {finalists: 2}
```

There is no `world:` or `hooks:` (D803): what a document cannot say is a command beside it -- a search (`orchestrate: {command: "... {history} {state} {params}"}`, D799), sub-loops in folders whose parent's `generate` composes them (`{parts}`, D801) -- a parent's `generate` that is a model or an agent (`generate: claude`) drafts for them instead, inherited like any box (D804). A search is the orchestrator's (D797): `orchestrate: {policy: sweep, space: {...}}` -- there is no `dse:`; the record's lessons are `knowledge: {lessons: mined}` and `brief` is gone (D796). Every box says who works it the same way (D795): a word (`rules`, `model`, `off`), an agent's name (`critique: claude`), or, only to give it options, `{by: claude, session: pass, ...the box's settings}` (D830: the name alone otherwise -- `plan: opencode`). Knowledge says who digests its papers as `digest:` and who writes lessons as `lessons:`, by name too: `knowledge: {files: [spec.md], digest: claude, lessons: mined}`. `flow.test` is a map by name like `flow.measure` (`lint: ...`, `golden: {run: ..., timeout_s: 300}`; a check named `build` refuses on any non-zero exit, D789). `parts` is a list of names or a map from each name to what it is (D792). The measurement cache is always on and keyed on the stage's command, the scripts it names and the params (D790); there is no `cache:`, `workbench:`, `joiner:` or `max_parts:`. `flow.knowledge: off` turns the library off; `flow.calibrate: off` the calibration. This is the
only layout (D783): a top-level `gate:`, `stages:`, `space:`, `seeds:` or `knowledge:` is a key a
document does not have, and `budget` takes no `finalists` or `calibrate`. A document of an earlier form
is brought to this one by `flux task migrate FOLDER [--write]`, or by an admin from Admin › Loops' "Migrate documents of an earlier form" (D811, D816):
each change said, a result written only when it loads, the original kept as `<file>.orig`; a `world:`,
`hooks:` or an `evaluator:` stage is said for a person to rewrite as commands, and a `workload:` key
or a mined `calibration:` source is dropped (D954).

## Check and run a problem

For a variable set of timing tests, declare a dictionary metric:

```yaml
flow:
  test: 'true'
  measure:
    bench:
      command: '{python} {home}/bench.py {artifact}'
      metrics:
        - {name: timings, type: dict, direction: minimize, unit: ms}
objectives:
  - {metric: timings.parse}  # inherits minimize and ms
```

The script can print `timings={"parse": 12.5, "compile": null}` or a JSON object such as
`{"timings": {"parse": 12.5, "compile": null}}`. JSON can span several lines, with diagnostic
output before or after it. Keys name individual tests; each inherits the parent's numeric type,
direction and unit. Omitted tests, null, empty and non-finite values stay unmeasured, while zero
remains a measurement. Records and caches use names such as `timings.parse`; objectives,
cutoffs and supplied baseline values can use the same names. The parent (`timings`) reports
the mean of its available finite tests by default. Set `aggregate` to `mean`, `median`, `min`,
`max`, `sum` or `none`; no finite tests means no aggregate measurement. Objectives can use
either the aggregate or a named test. Missing a required objective measurement still uses the
loop's normal eligibility rules. In the crafter, a custom measurement's **+ Dictionary** button
adds the parent name, direction, unit and aggregate choice.

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
```

**Papers.** A loop reads a library: the shared one (`flux/mentor/knowledge/library`, or `FLUX_LIBRARY`) and
its own (D791): `library/` beside its document (papers and references, in any subfolders; `flux ask`
puts its attachments there). Model prompts get the excerpts nearest the problem and a line per paper.
Coding agents get a **LIBRARY** section with the full stored digests of the five nearest papers,
ranked across the task's lookups, plus paths to nearby reference implementations. These digests replace
the lexical excerpts and duplicated digest catalog in their briefs. If a nearest paper has no digest
yet, the brief says so and provides its path to read (PDFs with `pdftotext`). `flux task check` says
how many documents, how many are the loop's own, and who reads them.
The sandbox mounts each library read-only. Every paper of the library is digested once -- the loop's own
first, then the shared ones -- and its key points become available to prompts; there is nothing to say for it, and
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

Coding-agent briefs put the current turn's task and search direction before the static knowledge,
including when writing a prototype. On exploration and variations passes, the incumbent is described by its leading
**INTENT** header and recorded measurements, without its implementation. If there is no INTENT header,
the loop uses its saved intent or explanation when available. Repair turns still include the refused
draft so the agent can fix it. These passes start independently of earlier best designs and cached
prototypes, while keeping those candidates on record. Variations develop distinct alternatives using
intent and measured trade-offs, rather than starting with the incumbent's implementation.

**Ideas notebook.** Each campaign retains hypotheses and alternative ideas alongside its trials.
On later passes, drafting and prototype prompts include a bounded notebook summary: untested
ideas, recent measurements, and failed attempts. These summaries contain no design source.
The full notebook is in **Results → Ideas**, with expandable evaluation histories and Raw JSON
and Fullscreen views. It survives stop/restart; clearing the loop's record also clears its notebook.

A model can add optional fields to its normal artifact, prototype or edits reply:

```json
{
  "idea": {"title": "Fewer stages", "hypothesis": "Removing redundant stages may reduce latency", "test": "Check correctness, measure cycles and area"},
  "ideas": [{"title": "Lookup table", "hypothesis": "Trade area for fewer cycles"}]
}
```

`idea` identifies the hypothesis this draft tests; `ideas` saves alternatives without claiming
they have been evaluated. Revisit an existing hypothesis with `"idea": {"id": "idea-..."}`.
Coding agents can write the same JSON to `<artifact filename>.ideas.json` beside their draft or
prototype; their brief names the file. Models with tools can use `ideas` to list, propose or select
a hypothesis. Leading INTENT headers on drafts are collected automatically when no idea is selected.
Notes are optional: malformed notes do not refuse the design.

Flux links ideas to the actual trial outcomes, including design, pass, stage, check failures and
measured values. **Proposed** means untested, **checked** means a check passed, and **measured**
means recorded measurements exist. Measured does not assert that an idea improved the objectives;
failed attempts remain visible when a later attempt succeeds. The read-only API is
`GET /api/apps/<name>/ideas`, with the same owner and historical run/campaign selectors as Results.

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

## The web interface

`flux serve` is a shared server with accounts where each user creates, configures, starts and
follows their loops live; [web.md](web.md) is its reference, page by page.

```bash
flux user add ada --admin && flux serve   # http://127.0.0.1:8765/
```

## RTL tools

The commands an RTL document names as its gate and stages:

```bash
python rtl.py test design.sv --golden golden.py        # Verilator against the golden model; prints `N failing of M`
python -m flux_loop.golden_proto prototype.py --golden golden.py   # a Python prototype against the golden model (D951)
python rtl.py measure design.sv --stage synth --clock-ps 1000   # or stat, place: ASAP7 metric=value lines
```

Run these from an application's folder: its `rtl.py` is one file, reading ASAP7 from
OpenROAD-flow-scripts' platform folder (D948, D950). Copy that script from a bundled RTL app
when creating your own. `python rtl.py test` exits 1 when the design fails and 3 when it does
not compile. The prototype check is core's, not RTL tooling (D951). A prototype that
passes is spelled as SystemVerilog by the loop (py2sv): integers, `if`/`elif`/`else` and early
returns, `for` over a constant range, helpers (inlined), module-level tables, and tuples -- a helper
returning several values, `s, m, k = unpack(x)`, `(a, b) if c else (d, e)`, `len(T)`, a tuple read
at a computed position (D804, D806). What it cannot spell is refused with the construct named.
A golden with `CLOCK = True` and `LATENCY = N` makes it a pipeline (D864): the spelled datapath is
cut into N register stages of about equal estimated depth, with `clk`, `rst_n`, `start` and `done`
(`start` N edges later); the prototype stays the plain algorithm. Changing N re-spells every
admitted design from its verified prototype at the next pass.

## From Python

```python
from flux_loop import PromptProblem, load_task, request_for, run_loop

task = load_task("applications/adder16")        # its problem.yaml; the id is `adder16`
result = run_loop(PromptProblem(task), request_for(task, db="adder16.db"), proposer=None)
```

What a document cannot say is a command beside it -- a check, a measurement, a search
(`orchestrate: {command}`), a composition -- ([extending.md](extending.md)).
