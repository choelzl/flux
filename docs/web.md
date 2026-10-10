# The web interface

`flux serve` is a shared server with accounts. Each user has loops: a problem document and its
files. A loop is running or not; starting it again resumes it from its record. Users check,
start, stop and follow their loops live (D683).

## Running the server

```bash
flux user add ada --admin            # the first account, on the server's machine
flux serve                           # http://127.0.0.1:8765/ ; --host 0.0.0.0 behind a TLS proxy, with --secure-cookie
```

- **Data:** `$XDG_DATA_HOME/flux/web` (`--data`), holding `flux-web.db`, `secret.key` (encrypts
  keys and secrets) and `users/<name>/apps/<app>/`. A run's sandbox cache is
  `~/.cache/flux/apps/<user>-<app>/`. `flux user add` says which data folder it wrote; `flux serve`
  says its data folder and its accounts: the two must be the same folder.
- **Limits:** `--max-running` runs at once per user (4); per-user override in Admin › Users. A loop
  holds up to 100,000 files and 8 GB; one request from elsewhere, 900 files and 256 MB.
- **Restarts:** a stopped server waits at most 3 s for open streams; a restarted one finds its
  running loops again. The installed user service stops only the server on updates
  (`KillMode=process`); loop sessions and containers keep running. For an older installation,
  re-run `scripts/install-service.py` from `flux/` with the same arguments, then
  `systemctl --user daemon-reload` before restarting.
- **Slow answers:** a request over 0.5 s is logged (`flux serve: slow: GET /api/… 1.23 s`);
  `FLUX_SLOW_S` sets the threshold.

## Everywhere

- **Top bar:** breadcrumbs (Loops › owner › loop › tab); **Theme** (system, light, dark;
  remembered in the browser); the **bell**.
- **Bell / notifications:** a run ends, fails, or its agent asks a question; a loop is shared
  with you or no longer is; a daily agent Test fails. Desktop notifications when allowed. Each
  user's own; it watches shared loops too.
- **Streams:** Live and Log say whether their stream is live. A dropped stream reopens where it
  left off (no line lost or repeated), retrying up to every 30 s; a banner says when the server
  cannot be reached. An ended session is said on the way to the login.
- **Raw / Fullscreen** on file, log, result, task and agent viewers. Raw streams the complete
  content as plain text in a new tab (also files too large for the editor; every retained
  measurement and design). Fullscreen keeps the view and unsaved edits; Close or Escape returns.
- **Code** is highlighted: YAML, Python, SystemVerilog/Verilog, VHDL, C/C++, JSON, Markdown,
  shell, Tcl.
- **Settings save as you change them** (Account, Admin, a loop's Advanced): a mark beside the
  field says saved or why not; a key saves when you leave its field. Documents (Direct edit, the
  configurator) keep **Save**. (D833)
- **Addresses** name the view: `#/app/x/live/log`, `#/app/x/settings/problem/edit`,
  `#/app/x/ask` (opens Talk), `#/app/x/live/history/<start-id>`.
- **On a phone:** account items are in the ☰ menu; Overview opens on the decision; Results on its
  designs, charts below. Lists stack, tabs and logs wrap.

## Loops list and New loop

- **Loops list:** search; filter by state (running, idle, failed); order by activity, name,
  accepted designs or decision. Each loop shows accepted and measured designs and the decision's
  number on the first objective (✓/✗ against its limit). **Shared with me** lists loops shared
  with you and what you may do; **Leave** removes one (its owner is told). **Group loops** lists
  group members' loops you may view.
- **New loop › Create or upload:** with no files selected, an empty loop opens in the
  configurator with a starter `problem.yaml`, README and empty `library/`. Otherwise drop or pick
  a folder (keeps its paths, names the loop), files, or a single `.zip` (files at the root or in
  one containing folder, which is removed). Any size: sent in batches (300 files, 40 MB), files
  over 40 MB in 32 MB parts, a ZIP intact for the server to unpack. The progress dialog ignores
  Escape; **Cancel** stops it and discards a half-sent file. **Clear files** returns to empty
  creation. Errors are shown above the dialog.
- **New loop › Configurator:** the form's **Loop name** is the loop's name and its problem's id.
- **New loop › Agent:** name the loop, say what it should do, attach what it should read (spec,
  reference model, tests, papers), pick the agent (OpenCode, Claude Code, Codex, Flux's model;
  one not installed says so). It runs `flux ask --no-run` in the sandbox with your model
  settings, writes the document and its files, and the document is checked; nothing runs. The
  Overview follows it: elapsed time, phase (writing/repair/check), live thinking, replies, tool
  calls, endpoint retries, creation log; process liveness and time since last output shown
  separately (a quiet agent is not called failed). **Stop** ends the author. Review the
  document before starting. Needs both Create and Run permissions.
- **New loop › Clone a loop** (or **Clone…** on a loop): a new loop of yours with its problem,
  none of its runs, its workbench when asked. Variables, sharing and admin overrides are not
  copied; it uses your account and server defaults. An admin cloning a loop with special
  permissions may tick **Keep special permissions** (off by default): keeps mounts, sandbox
  override, extra allowlist and raw TCP/UDP; resource limits, packages and parallelism reset.

## The configurator

The loop crafter inside the app (D826). Seven steps, one at a time with **Back** / **Next**: the
problem, checks, measurements, objectives, who does each step, more (budget, search, parts),
review and save. **Save** is on every step when editing a loop. A checklist shows what is left.

- **Files that go with it** (beside the form): the loop's own files; open, edit, write new, drop
  files or folders, delete. A file named as `{home}/…` that the loop lacks is marked missing; a
  click writes it.
- What the form cannot express (own stages or settings, `params`, a failure pattern, objectives
  with their own stage or tie) is kept as written and listed beside the file. Comments are not
  kept.
- A save shows its line-by-line diff and writes only on confirm.
- **Baseline / pass 0** (Extra step): **Provided metrics and values** adds metric/value rows and
  an optional stage (default: deepest), e.g. `baseline: {metrics: [{metric: time_ms, value: 12}]}`.
  Pass 0 imports them without running tools; unchanged values are reused on restart, edited ones
  make a new reference.
- `language:` is optional: `systemverilog` for RTL, `cpp` for C++. Without one the design is a
  `.txt` file.

## A loop's page

Six tabs: **Overview**, **Live**, **Results**, **Agents**, **Files**, **Settings** (D713). The
header has **Start**, or **Stop** / **Restart** while active.

- **Start:** passes (or "until I stop it") and screen only; always sandboxed; network from the
  admin's and the loop's Settings. Offers the last start's choices; runs the check when inputs
  changed since it last ran; if it fails the button reads **Start anyway**. Resumes from the
  record. Says when the document needs migrating.
- **Stop / Restart:** one press schedules it after the pass; the button turns red (**Stop NOW** /
  **Restart NOW**); pressing again asks to confirm abandoning the pass (cancel keeps the
  scheduled action). Stop replaces a pending restart. Restart keeps problem and run settings and
  subtracts completed passes from a finite budget. Requests persist on the server.
- A loop has one log (each start marked), one answer and one notes inbox; no run numbers.
- **Talk** (any tab) opens a panel with conversations about the loop. **Reply** follows up with
  that branch as context; **New chat** starts another; **Collapse** / **Expand** (survives
  refreshes). Ctrl+Enter (⌘+Enter) sends, Enter adds a line, Escape closes keeping the draft.
  While running, **Send a note to the running loop** joins the next prompt or answers the
  agent's open question (`questions: operator` shows its time left). A bin removes a note or
  conversation after confirming; an unread note never reaches the loop, a read one stays in the
  record.
- **Ask** (in Talk): a question (why it stalls, which design is best, what next) answered by an
  agent or Flux's model reading the document, files, a copy of the record and the log; sandboxed,
  loop folder read-only, the run's network; changes nothing. Answers kept newest first, in
  Markdown, one at a time. Editors with Run permission ask; watchers read. CLI:
  `flux consult "<question>" --loop <folder> --out <folder> --author opencode`.
- **When something is wrong:** a failed start's Overview quotes its log's lines (log a click
  away); Direct edit warns before saving a document that does not load; a broken tool fails its
  leaf.

### Overview

- State, designs measured (accepted, failed), passes on record, the objective, the decision's
  numbers against the limits and why it was chosen. The decision is the record's latest pass's
  (D809). A limit is a floor; among designs meeting every limit, the next objective without one
  decides.
- Best 3 designs: the decision, then accepted before failed, deepest stage, then each objective
  without a limit (also before any decision).
- Best-so-far chart per objective: each measurement, the best as a step line, limit dashed,
  passes marked.
- The agent's open question, latest notes, newest workbench entries, the last pass (when, its
  measurements, its conclusion); model/agent turns, time and tokens. Redraws each minute while
  running. Empty cards hide; failures and unanswered questions always show.
- **Account › Overview layout › Customize** (whole account): 3–5 small top cards (also main
  metric, change vs baseline, acceptance rate, active run time, model/agent time, goals met) and
  large cards in two columns (also Pareto front, reference comparisons, goal status, recent
  designs, recent passes, usage by model/agent, **Ideas**). ↑/↓ reorder, ←/→ change column; live
  preview on **Mock Data**; **Save**, **Cancel**, **Defaults**. Pareto cards follow the project's
  saved axes, stage, scope and focus. **Ideas** shows the five most recently active proposals and
  their evidence (measured means evidence exists, not improvement); **All ideas** opens Results ›
  Ideas.

### Live

Views: **Tasks**, **Log**, **Timeline**, **History**.

- **Tasks** (D739): this start's **Setup**, a branch per **Pass** (named by its design) whose
  leaves are the configurator's boxes in a word (Search, Design, Check, Measure, Choose, Critic…;
  full name on hover; ×N when repeated), and an **End** (Reason, Decision, Lessons, Established,
  Design file, Answer file). A loop in parts has a branch per **Part** and **Whole**. A resumed
  pass's re-checks are its Setup leaf.
- A leaf opens tabs (Live or Output, Input, Log, Every field) and lists its tasks. **Tree |
  Graph** (remembered per browser): the graph is the loop's drawing, unused boxes dimmed, running
  one pulsing; a step bar (⏮ ◀ slider ▶ ⏭) walks the selected box's runs, its tasks listed below.
- It follows the running task (agents first), collapses finished branches, and is searchable;
  at rest it shows the task that ended last.
- **Tool task** (`tool:<program>`, e.g. `stage bench list_sieve-0`): exit, time, folder,
  command, stdout/stderr tails live; non-zero exit marked.
- **Agent task:** reply first, then its conversation: text, folded thinking and tool calls
  (input field by field, output; failed in red; open ones stay open). Scroll boxes follow their
  end unless you scroll up. **Prompt** shows its input while it runs.
- Below the tree: the log as it grows, problems on demand, **times** per line (`Oct 05
  14:03:22`; one setting for both logs, per browser, on by default). A docked line sends notes
  (Enter sends, Shift+Enter breaks) and answers the agent's question. Standings: counts, frontier,
  parts.
- **LiveAlt:** an alternative layout: **Tree**, **Graph**, **Timeline** of the same journal;
  pick **Start** and **Current** / **All passes** / a **Pass** (including pass 0). Selecting a
  task pins its inspector; **Current** resumes following. Inputs (prompts, commands, stdin up to
  48,000 characters), output and conversations together, with Raw and Fullscreen.
- **Long runs** (D759): Tasks opens on the last 30 passes (**Earlier** loads the rest), at most
  24 MB (the oldest whole pass that fits, or the newest pass's tail); **Show more** expands large
  lists. A resumed loop keeps a design its judge already admitted unless the gate, its files, the
  tools or Flux changed.
- **Log:** numbered, problems highlighted, starts marked; opens on the last 2 MB (**Load all**).
  One start or all; follow (pauses on scroll up), wrap (last 3000 lines), filter by text or
  `/regex/`, problems only, download.
- **Timeline:** one start's time by category (Setup, Plan, Search, Design, Check, Measure, Choose,
  Critic; builds/tests are Check). Agent activity has one reserved colour. Per category: calls,
  average, longest, total (overlap counted once) and **total% (agent%)** of wall clock; summed
  time and concurrency where calls overlapped. Choose start and pass; redraws each minute.
- **History:** pick an older start to replay its passes, tool and agent tasks, text output and
  conversations; Results and reports by recorded campaign up to that start's end. Removed data
  shows unavailable. Text log preview up to 1 MiB (**Raw** for all).

### Results

- Designs measured successfully across all starts, **accepted** or **failed** by the limits, at
  their deepest stage; the decision first. Columns sort (again reverses, missing last). Filter
  all / accepted / failed. Rows grow by 200. A draft sent to repair or refused by the gate is not
  a result.
- **Pareto front:** any two metrics at one stage or each design's deepest; accepted/failed
  coloured, decision a diamond, front joined; click opens the design. **Focus** fits both axes
  to the feasible front (saved; full range if none). **Improvement over time:** best so far per
  metric. Over 3000 measurements, charts draw every new best and a share of the rest. Baselines
  are gray reference lines.
- **Compare:** tick two designs: numbers per stage, the change (green where better), a source
  diff. A design opens with the limits it misses, every stage's numbers and its source.
- **Table layout** (also Overview's Decision table): compact; names `#ID` or `part#ID` (reused IDs
  get a content-key suffix); ✓/✗/… badges; limit status as green/red text; details on hover;
  diagonal headers.
- **Absolute / Relative** (shared toggle): Relative is percent change from the latest baseline
  for the same metric, stage and group; without one, from accepted designs' P90 (higher-better) or
  P10 (lower-better). Hover for absolute value, reference, sample count. Undefined shows —.
- **Dictionary metrics:** one test at a time from a dropdown, or **All** to unroll them. Tests
  missing or null/empty/NaN stay unmeasured, not zero.
- **Baselines** are references, never the decision or a search start. Pass 0 keeps the best
  eligible search design if its measurements are fresh; unchanged successful baselines are reused.
- **Ideas:** the full ideas notebook.

### Agents

Each turn: prompt, reply and tool calls, model and tool version, tokens, session, exit, folder;
**Prompt** opens its input fullscreen. Above: turns, time, tokens in (and cached), out, USD
where priced (see token prices), in all and per agent/model. Interrupted turns' usage is marked
incomplete; no estimates.

### Files

- **Loop files** and **Workbench** (the tools and notes the agents keep, each with its first
  line). View, edit, download any file; path folders are links.
- The loop's `.gitignore` files hide what they ignore (here and in the configurator); **show
  ignored files** lists them greyed. `.git` is never listed or read.

### Settings

- **Preferences** (default view):
  - **Name & ownership** (owners, admins; loop stopped, agents too): **Rename** (1–60 of
    `A–Z a–z 0–9 - _`) keeps files, results, history, caches, variables, sharing and admin
    settings. **Transfer** to an enabled user (optionally renaming) moves files, history, caches
    and variables (with secrets), clears sharing and, by default, admin overrides; the former owner
    loses access. Links follow. Custom scripts with absolute paths may need updating.
  - **Measurements:** per metric, **Visible** (hidden columns reappear dimmed with **Hidden N**),
    **Main** (shown in lists and summaries, seeds charts; default the first objective without a
    goal), **%** (show as change from baseline in summaries and lists). Saved with the project
    on the server; graphs and ranking always use all measurements.
  - Graph choices (metrics, Pareto axes, stages, scope) are saved with the project by owners,
    editors and admins; viewers change them temporarily. They survive rename, transfer, clone and
    reset.
  - **Variables:** the loop's environment variables over the user's and the server's (listed
    below them).
  - **Sharing** (owners, admins, group members allowed): **watch** (runs, log, results, turns,
    files, settings) or **edit** (also files, document, variables, start, stop, notes). An
    editor's runs use the owner's record, model settings, keys and limits; each start's log line
    names who started it.
  - **Maintenance:** run maintenance tasks on this loop.
- **Problem:** **Configurator**, **Direct edit** (YAML as written, saved with its diff, files
  beside), or **Agent** (say what should change; it revises a copy of the loop's files, keeping
  its name, and shows its diff; not while the loop runs, and the loop does not start meanwhile).
- **Advanced** (admins change, everyone sees): sandbox or host, memory, CPUs, processes, scratch
  size; **Allow parallel work** (D740: off, one tool and one part at a time, said in the log; on,
  `budget.workers` / `parallel_parts` decide); **Allow raw TCP/UDP** (off; next start; see
  Sandbox); extra allowlist hosts; **Nixpkgs packages** (`jq`, `python3Packages.numpy`) and
  **Nixchip packages** (`verilator`, `systemc`), one attribute per line from Flux's locked inputs
  (no expressions or flake URLs). Packages build on the host before the next sandbox launch (the
  first can be slow; failures in its log), reach runs, Check and agents, add PATH, headers,
  library and pkg-config paths and Python modules; Nixchip tools get `NAME_HOME/_BIN/_LIB/_INCLUDE`.
  Need sandboxing, Nix and a checkout with `flake.nix` and `flake.lock`; clearing restores the
  defaults.
- **Reset** (owner; loop and agents stopped) clears `out/`, `runs/`, `workbench/`,
  `.author-work/`, its cache under `$XDG_CACHE_HOME/flux/apps/` (or `~/.cache/flux/apps/`), saved
  start history and last check status. **Keep** boxes preserve workbench, results/logs/history
  (together), author scratch, or tool caches and agent traces. The document, sources, library,
  settings and sharing stay. Cannot be undone. **Delete** removes the whole loop.

## How a loop behaves

- **One pass, one design** (D845): each pass refines the standing design or explores (orchestrator's
  choice, else after `budget.explore_after: 2` passes without a new decision); it never rests. The
  best design measured so far is the decision. Exploring passes ask for a new design, shown what
  was tried; a design already measured is refused before it is built.
- **Evidence follows its inputs:** editing a file beside the document or the params re-checks and
  re-measures on resume; editing objectives does not.
- **Files stay in their loop:** the server never follows a link out of a loop's folders, and
  replaces a file rather than writing through a link.
- A blocked network destination fails that request only; the loop tries another candidate within
  its budgets.

## Applications

- **Applications** page: upload files, a folder or a `.zip`, or write the YAML in the page; add
  files to an existing one; drag onto the page. **Check the document** runs `flux task check` in
  the sandbox.
- **Admin › Applications:** the `applications/` folder (or `FLUX_APPLICATIONS`), each with what
  it asks and its size. **Use** makes it the admin's loop (files hard-linked or copied, edits
  replace rather than write through); **Refresh** re-takes the files, keeping the record.

## Account

- **My agents and models:** per agent its login, **Test**, model, own variables, **Seconds per
  turn** (overrides the server's); variables for every agent; the agent by default (overrides the
  admin's). Server values shown in grey. Naming your own endpoint in a group drops the server's
  values of that group; your token prices count only with your own endpoint.
- **Agent logins:** runs the agent's login command (`opencode auth login`, `claude setup-token`,
  `codex login`) in the sandbox with your home writable, in a small terminal (↑ ↓ Enter Esc Tab
  Ctrl-C). Claude's year-long token is saved encrypted as `FLUX_<AGENT>_OAUTH_TOKEN` (given to
  runs as `CLAUDE_CODE_OAUTH_TOKEN`) and never shown. For Codex use `codex login --device-auth`.
  A successful login is tested at once.
- **Test** (D751): program, login or key, one short answer (`flux agent test <agent> --live`).
  Loops, authors and questions use an agent only once its Test passed for whoever starts it;
  `task check` says which agents a document needs. Tested agents are retested daily; a failure
  goes to the bell and dependent loops wait for a passing Test.
- **Variables**, **Usage** (your turns, time, tokens over all loops), **Overview layout**.

## Admin

Tabs: Loops, Insights and audit, Applications, Resources, Sandbox, Agents and models, Users and
groups, Maintenance. Admins see and edit every loop as an editor would; it runs on its owner's
agents and settings.

### Loops

- Every user's loops. **Pause new starts** (with a reason users see; running loops go on),
  **Stop** every active loop after its pass.
- **Restart** all: waits for passes to finish and processes to exit, resumes each with its
  screen-only mode and document; finite budgets continue with what remains (10 requested, 5 done
  → 5; pass 0 and unfinished passes excluded); exhausted budgets stay stopped; idle loops stay
  idle. Respects running limits and agent checks; loops that cannot restart are listed; failures
  go to the bell.
- **Old documents** (D811) appears when documents need migrating: what each would change, where
  it goes (`<id>.problem.yaml` with an `id:` becomes `problem.yaml`), per loop or **Migrate all**;
  running loops wait; originals kept as `<file>.orig`.

### Insights and audit

Sub-tabs **Failures**, **Usage and disk**, **Endpoints and network**, **Audit trail**, over 1, 7
or 30 days: failed starts with their log's words and failed Tests; turns, tokens and cost by
user and agent/model, per day, top loops; per endpoint and agent turns, failures, median and 95%
time, last failure (local timeouts are not endpoint failures); hosts the sandboxes refused, by
loop; disk by user. The **Audit trail** filters by group of events (users and sign-in, runs,
loops and files, sharing and loop settings, server, network, other) and by whom, and searches
details; refused hosts and name lookups appear once per host and port per run.

### Resources

- The machine: CPUs, load, memory, disks (data, caches, sandbox storage). Sampled each minute,
  kept 30 days (minute samples for a day, 5 min to day 7, 30 min to day 30); charts over 1 h, 6 h,
  day, week, 30 days; hover shows the sample. Token rates per retained campaign; ongoing turns
  appear when they finish.
- Containers with CPU, memory, PIDs and loop: **creating loop** / **revising loop** (**View**
  opens progress), **active task**, or **left behind** (kill; stopped leftovers removable).
- Every loop's disk: inputs, record, log, workbench, cache. Clear a loop's tool cache or past
  passes' scratch; delete caches no loop owns.

### Sandbox

- **Network:** open, or an allowlist (hosts and subdomains, `*.domain`, IPs, CIDRs); optionally
  the model endpoints' hosts. An empty allowlist reaches nothing. HTTP(S) goes through the host's
  proxy (and its upstream). Names resolve through Flux; a disallowed lookup is refused and audited.
  Users see only that the network is limited and how many entries. Loopback stays inside.
- **Raw TCP/UDP** (per loop, Settings › Advanced): a firewall helper (needs host `nft`) admits IP/
  CIDR rules at all ports and allowed names' resolved addresses; other bare IPs are dropped.
  Native UDP needs a host route. If the helper fails, HTTP(S) still works.
- **PATH:** each directory is mounted read-only; add the server user's login PATH or others.
- **Homes** (D744): each user's `<data>/users/<name>/home` (0700), writable at `/home/flux`, keeps
  agent settings, logins and sessions. A run uses its loop owner's home. New homes get the admin's
  list of paths from the server account's home (default `.config/opencode`, plus a corporate
  OpenCode's parts under `.local/share/opencode/` and `.local/state/opencode/`), never overwriting.
- Leftover containers are removed each minute (in the audit). A run's variables reach its
  container in a 0600 file, never on the command line.
- **Command line:** `FLUX_SANDBOX_HOME` (default `~/.local/share/flux/home`),
  `FLUX_SANDBOX_NET=allowlist`, `FLUX_SANDBOX_ALLOW`, `FLUX_SANDBOX_RAW_NETWORK=1`.

### Agents and models

- One tab per tool: **Flux** (its own model: any OpenAI-compatible endpoint, e.g. OpenRouter or
  Ollama's `/v1`), each agent, **Every agent** (server variables every run gets), **+ Add an
  agent**. A tab with its own settings is marked •.
- **Per agent** (D807): name shown, program (its folder goes on the run's PATH), login command,
  extra arguments, login location for custom builds (e.g. `.cache/nga/auth.json`), starter files
  for homes, hosts it needs, who is ready (each user's Test). Offered to users only where its
  program runs. Its model: endpoint, model, key (`FLUX_<AGENT>_BASE_URL`, `_MODEL`, `_API_KEY`) and
  variables for that agent only (`FLUX_<AGENT>_ENV`).
- **Add an agent:** a lower-case name (`generate: nga`), a kind (opencode, claude, codex), a
  program path. Removable (its settings and variables go with it). Documents name it in any box
  (`orchestrate: nga`, `knowledge: {digest: nga}`). CLI: `FLUX_AGENTS='{"nga": "opencode"}'` and
  `FLUX_NGA_BIN`.
- **Seconds per turn** (D893): admin default, user override, document `timeout_s` wins
  (`generate: {by: codex, timeout_s: 3600}`); default 1800.
- **Token prices:** in/out USD per million tokens on the Flux tab and each agent's model; CLI
  `FLUX_REMOTE_PRICE_IN`, `FLUX_<AGENT>_PRICE_OUT` in flux.env.
- **The agent by default:** who writes problems and answers questions unless chosen.
- Keys stored encrypted, never shown again. With nothing set, runs use the machine's own
  configuration (flux.env, OpenCode's, Claude Code's).
- **Endpoint recovery:** on a transient API error (502/503/504/529, reset/refused), Flux waits for
  the endpoint (≤5 min each) and sends **continue** to the same session (≤3 times within the
  turn's limit); an agent stalled 15 s after the error has its subprocess replaced. Direct model
  calls retry with backoff for up to 5 minutes. Auth errors and allowlist refusals do not trigger
  it.

### Users and groups

**Users** subtab: group, per-user permissions, running limit (empty: `--max-running`), usage,
**View as**, invitations and reset links. **Groups** subtab: create, rename, **Server access**,
member counts, admin status. The last subtab is remembered.

### Maintenance

Scheduled tasks (D885), each on/off, an interval, settings, last result, **Run now**: Clean
scratch (`FLUX_TMPDIR`; off by default), Reap containers (every minute), Clean idle caches, Disk
alert, Compact databases, Condense run logs (old part gzipped beside), Prune server tables, Prune
stale records (off). Off until enabled: **Check database integrity**, **Validate loop
documents**, **Back up server database** (`flux-web.db` and `secret.key` to
`<server-data>/backups/server-<timestamp>/`, latest seven kept; back up loop folders separately).
Loop tasks run over all loops or one; running loops are never touched; every run is audited.

## Users, groups and permissions

- **Accounts:** names ignore case and surrounding spaces; passwords are exactly as typed.
  Five failures from one address lock the name there for 10 minutes; fifty overall lock it
  everywhere. HttpOnly, SameSite=Strict session cookie. A user sees only their own loops and those
  shared with them.
- **Invitations** (D818): **Users** or `flux user add NAME --invite --url https://flux.example`
  gives a link letting them set a password (10+ characters) and log in; until then the account cannot be used. **Password reset link** (`flux user link
  NAME`): old password works until used, then sessions end. A link works once, for a week; there
  is no mail.
- **Groups:** each user in one; built-in **Admin** (server administration even if renamed),
  **Internal**, **External**. Per-user permissions:
  - **Create loops** (create, upload, clone).
  - **Run loops and agents** (start, check, ask, author); owners can always stop their loops.
  - **View other members' loops** (in **Group loops**, secrets masked).
  - **Edit other members' loops** (not Run or Sharing).
  - **Run/stop other members' loops** (with the owner's credentials; needs Run).
  - **Manage sharing of other members' loops.**
  Reset, delete, rename and transfer stay owner/admin. Individual shares work across groups.
  Checked on every request.
- **Server access** (per group): **Use server settings** (internal: server model, agent and
  environment settings under the user's own) or **Own settings only** (external: only their own
  settings and the admin's agent programs). Applies on the next run. `--role
  internal|external|admin` assigns the built-in group.
- **View as:** opens Flux as an enabled user, read-only, with a **Return to admin**
  banner; audited; ends with the admin session.

## Environment variables

Server (Admin › Agents and models › Every agent), user (Account), loop (Settings), applied in
that order; secrets stored encrypted and never shown again. They reach the sandbox whatever
their names. The sandbox's and loop's own variables, process basics (`PATH`, `HOME`, `LD_*`) and
model settings cannot be set this way. `OLLAMA_BASE_URL`, `FLUX_LLM_MODEL`, `OPENROUTER_API_KEY`
are ordinary variables here.

## Testing the web UI

- `python3 tests/check.py` (from `flux/`, dev shell): ruff, unit, heavy and browser tests in
  parallel (~2.5 min); `tests/check.py unit e2e` for some.
- `python3 tests/e2e/web_ui.py`: its own `flux serve` with three users, headless Firefox through
  every page (login, New loop, upload, tabs, Files, Direct edit, variables, sharing, start/stop,
  watcher, admin, dark theme, agent Test with a stand-in Codex, phone width). Fails on script
  errors or red notices; screenshots in `~/snap/firefox/common/flux-e2e/shots/` (`FLUX_E2E_HOME`).
  `FLUX_E2E_SANDBOX=1` sandboxes the loops; `FLUX_E2E_STEPS="login refused,login,invitation,insights"`
  runs a few steps (the first two log in).
- The task tree builder (`static/looptree.js`) is tested under node: `tests/unit/test_looptree.py`.
