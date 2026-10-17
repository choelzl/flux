# Usage guide: notes for contributors

The user documentation is on the site: [Install](https://choelzl.github.io/flux/start/install/),
[First loop](https://choelzl.github.io/flux/guide/tutorial/), [Run, stop, resume](https://choelzl.github.io/flux/guide/run/),
[Write a loop with an agent](https://choelzl.github.io/flux/start/ask/), the [Document reference](https://choelzl.github.io/flux/guide/parameters/)
and every command's options in the [CLI reference](https://choelzl.github.io/flux/reference/cli/). This page keeps how
some of it works inside: what a contributor, or someone debugging a run, needs beyond them.

| to | read |
|---|---|
| drive Flux from a script or an agent | [agent-surface.md](agent-surface.md) |
| extend it: a policy, a search command, a role | [extending.md](extending.md) |
| the structure | [architecture.md](architecture.md) |

## Environment

The model's variables are on the site ([Models and agents](https://choelzl.github.io/flux/guide/models/)). The others:

| variable | what it does | default |
|---|---|---|
| `FLUX_TMPDIR` | scratch files | the system's temp directory |
| `FLUX_TRACE_ROOT` | where each pass writes its prompts, replies and checked prototypes | `$TMPDIR/flux-traces` |
| `FLUX_OPENROAD_THREADS` | threads for one OpenROAD run | the cores / 4, at most 16 |

## How a search works between passes

**A search works between passes** (D738). A design is named by its settings (D743): a value that
is a word stands alone, any other carries its knob (`list_sieve-wheel=1`). With `flow.orchestrate` and its `space`, a pass tries one design, the
search's next pick from what the passes before it measured: a 6-point sweep is 6 passes
(`--passes 6`), an anneal or a genetic population carries on from pass to pass. `budget.batch: N`
lets one pass make, check and measure N of the search's picks side by side (worth it when the tools
take many designs at once). A new run starts the search again from its record.
`budget.parallel: N` (D747) runs N passes at once, each with its own design and its own branch in the tree;
the next N start once all of them ended, and the run ends with one decision over every pass. On a server
an admin allows it per loop (Advanced › Allow parallel work); otherwise passes run one at a time.

## Papers and their digests

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

## The ideas notebook

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

## RTL tools

The commands an RTL document names as its gate and stages:

```bash
fluxpreset golden design.sv --golden golden.py        # Verilator against the golden model; prints `N failing of M`
python -m flux_loop.golden_proto prototype.py --golden golden.py   # a Python prototype against the golden model (D951)
fluxpreset rtl measure design.sv --stage synth --clock-ps 1000   # or stat, place, route: ASAP7 metric=value lines
```

The golden test, the RTL lint and measurement and the prototype check are Flux's own (D951,
D968); `fluxpreset rtl measure` runs OpenROAD-flow-scripts on ASAP7 to the stage asked. `fluxpreset golden` exits 1 when the
design fails and 3 when it does not compile. A prototype that
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

