# CLI

Every `flux` command's own help, as `flux COMMAND --help` prints it. The checks and measurements
Flux ships are a separate command, `fluxpreset` (see [Presets](presets.md)).

## flux

```text
usage: flux [-h]
            {new,ask,consult,task,loop,probe,tools,prog,report,log,run,status,stop,attach,serve,user,login,agent,selftest} ...

Flux: an AI-driven design-space exploration loop. A model or a coding agent proposes designs, real tools check and measure them, the loop decides and keeps a record.

options:
  -h, --help            show this help message and exit

commands:
  {new,ask,consult,task,loop,probe,tools,prog,report,log,run,status,stop,attach,serve,user,login,agent,selftest}
    log                 Every model and coding-agent turn of a campaign's
                        runs: prompts, replies, tool calls, errors.
    probe               Inside an agent's turn: check a file with the loop's
                        own gate or stage.
    loop                What a loop already knows, read back: ideas, designs
                        tried, results, a design, papers; its gate on a file.
    serve               The web interface: accounts, applications, runs
                        followed live.
    user                The web interface's accounts, from the server's
                        machine.
    selftest            Check this machine: a command-driven loop, the model,
                        optional application tools.
    new                 Write a loop's baseline to fill in: the skeleton
                        problem.yaml, a README of the folder's parts,
                        library/.
    consult             A question about a loop, answered by an agent that
                        reads it and changes nothing.
    login               A coding agent's login, its home a given folder (an
                        external user's agents in the web).
    agent               Coding agents: is one ready for you.
    ask                 The loop from a prompt and files: an author writes the
                        problem, the loop runs it, the author steers.
    task                Check or run a problem document through the loop.
    report              How a campaign moved: frontier evolution, hypervolume,
                        best-so-far, the parts.
    run                 Start a command detached, its log under the trace
                        root.
    status              Is the campaign running, since when, how many passes.
    stop                Stop the campaign's run at the pass boundary (or
                        --now).
    tools               The checks a gate may run and the stages a document
                        may measure with.
    attach              Tail the log of the campaign's run (started by `flux
                        run`).

start with:
  flux new myloop            (the baseline to fill in)
  flux task check <folder>
  flux task run <folder>
  flux ask "what you want" --file spec.pdf
docs: README.md and docs/usage-guide.md
```

## flux log

```text
usage: flux log [-h] [--campaign CAMPAIGN] [--last N] [--turn K] [--full] db

positional arguments:
  db                   The campaign record.

options:
  -h, --help           show this help message and exit
  --campaign CAMPAIGN  A campaign id prefix (default: the latest).
  --last N             The last N turns (default 10; 0 for all).
  --turn K             Turn K whole: the prompt, the reply, the tool calls.
  --full               Every listed turn whole, not a line each.
```

## flux probe

```text
usage: flux probe [-h] [--stage STAGE] [--gate] {gate,measure} file

positional arguments:
  {gate,measure}  gate: the correctness checks; measure: stages.
  file            The file to check.

options:
  -h, --help      show this help message and exit
  --stage STAGE   A stage to measure (repeat for several: each on its own,
                  side by side; default: the first).
  --gate          measure: run the gate first.
```

## flux loop

```text
usage: flux loop [-h]
                 {ideas,tried,results,design,diff,library,digest,gate,measure} ...

Reads a loop's record and library. Inside an agent's turn it reads that turn's
loop; elsewhere --loop (default: the current folder). Reads add nothing to the
record.

positional arguments:
  {ideas,tried,results,design,diff,library,digest,gate,measure}
    ideas               The ideas notebook: each hypothesis, its status and
                        the evidence on record.
    tried               The designs tried, oldest first: the stages each
                        reached, its numbers or why it stopped.
    results             The measured designs at one stage, ranked by the
                        loop's objectives.
    design              A design's text as the record keeps it (a name, or the
                        start of one).
    diff                Two designs line by line; either may be a file.
    library             The library's papers, one line each; with QUERY the
                        nearest, with their digests.
    digest              One paper's digest (by file name or stem).
    gate                The loop's own gate on a file (inside a turn: a probe,
                        on its budget).
    measure             The loop's own stages on a file, each on its own
                        (inside a turn: a probe).

options:
  -h, --help            show this help message and exit
```

### flux loop ideas

```text
usage: flux loop ideas [-h]
                       [--status {proposed,checked,measured,failed,interrupted}]
                       [--loop LOOP] [--db DB] [--campaign CAMPAIGN]
                       [--part PART] [--json]

options:
  -h, --help            show this help message and exit
  --status {proposed,checked,measured,failed,interrupted}
  --loop LOOP           The loop's folder or document (default: the agent
                        turn's, else the current folder).
  --db DB               The record (default: the loop's out/<id>.db).
  --campaign CAMPAIGN   A campaign id or prefix (default: the loop's).
  --part PART           One part (default: the turn's part, else every part /
                        the whole).
  --json                The answer as JSON.
```

### flux loop tried

```text
usage: flux loop tried [-h] [--last N] [--failed] [--loop LOOP] [--db DB]
                       [--campaign CAMPAIGN] [--part PART] [--json]

options:
  -h, --help           show this help message and exit
  --last N             The last N designs (default 12; 0 for all).
  --failed             Only those refused before any measurement.
  --loop LOOP          The loop's folder or document (default: the agent
                       turn's, else the current folder).
  --db DB              The record (default: the loop's out/<id>.db).
  --campaign CAMPAIGN  A campaign id or prefix (default: the loop's).
  --part PART          One part (default: the turn's part, else every part /
                       the whole).
  --json               The answer as JSON.
```

### flux loop results

```text
usage: flux loop results [-h] [--stage STAGE] [--top N] [--loop LOOP]
                         [--db DB] [--campaign CAMPAIGN] [--part PART]
                         [--json]

options:
  -h, --help           show this help message and exit
  --stage STAGE        The stage (default: the goal's, else the deepest
                       measured).
  --top N              The best N (default 10; 0 for all).
  --loop LOOP          The loop's folder or document (default: the agent
                       turn's, else the current folder).
  --db DB              The record (default: the loop's out/<id>.db).
  --campaign CAMPAIGN  A campaign id or prefix (default: the loop's).
  --part PART          One part (default: the turn's part, else every part /
                       the whole).
  --json               The answer as JSON.
```

### flux loop design

```text
usage: flux loop design [-h] [--loop LOOP] [--db DB] [--campaign CAMPAIGN]
                        [--json]
                        name

positional arguments:
  name

options:
  -h, --help           show this help message and exit
  --loop LOOP          The loop's folder or document (default: the agent
                       turn's, else the current folder).
  --db DB              The record (default: the loop's out/<id>.db).
  --campaign CAMPAIGN  A campaign id or prefix (default: the loop's).
  --json               The answer as JSON.
```

### flux loop diff

```text
usage: flux loop diff [-h] [--context CONTEXT] [--loop LOOP] [--db DB]
                      [--campaign CAMPAIGN] [--json]
                      a b

positional arguments:
  a
  b

options:
  -h, --help           show this help message and exit
  --context CONTEXT    Lines of context (default 3).
  --loop LOOP          The loop's folder or document (default: the agent
                       turn's, else the current folder).
  --db DB              The record (default: the loop's out/<id>.db).
  --campaign CAMPAIGN  A campaign id or prefix (default: the loop's).
  --json               The answer as JSON.
```

### flux loop library

```text
usage: flux loop library [-h] [--n N] [--loop LOOP] [--db DB]
                         [--campaign CAMPAIGN] [--json]
                         [query]

positional arguments:
  query

options:
  -h, --help           show this help message and exit
  --n N                How many papers for a query (default 5).
  --loop LOOP          The loop's folder or document (default: the agent
                       turn's, else the current folder).
  --db DB              The record (default: the loop's out/<id>.db).
  --campaign CAMPAIGN  A campaign id or prefix (default: the loop's).
  --json               The answer as JSON.
```

### flux loop digest

```text
usage: flux loop digest [-h] [--loop LOOP] [--db DB] [--campaign CAMPAIGN]
                        [--json]
                        paper

positional arguments:
  paper

options:
  -h, --help           show this help message and exit
  --loop LOOP          The loop's folder or document (default: the agent
                       turn's, else the current folder).
  --db DB              The record (default: the loop's out/<id>.db).
  --campaign CAMPAIGN  A campaign id or prefix (default: the loop's).
  --json               The answer as JSON.
```

### flux loop gate

```text
usage: flux loop gate [-h] [--loop LOOP] [--db DB] [--campaign CAMPAIGN]
                      [--part PART] [--json]
                      file

positional arguments:
  file

options:
  -h, --help           show this help message and exit
  --loop LOOP          The loop's folder or document (default: the agent
                       turn's, else the current folder).
  --db DB              The record (default: the loop's out/<id>.db).
  --campaign CAMPAIGN  A campaign id or prefix (default: the loop's).
  --part PART          One part (default: the turn's part, else every part /
                       the whole).
  --json               The answer as JSON.
```

### flux loop measure

```text
usage: flux loop measure [-h] [--stage STAGE] [--gate] [--loop LOOP] [--db DB]
                         [--campaign CAMPAIGN] [--part PART] [--json]
                         file

positional arguments:
  file

options:
  -h, --help           show this help message and exit
  --stage STAGE        A stage (repeatable; default: the first).
  --gate               Run the gate first.
  --loop LOOP          The loop's folder or document (default: the agent
                       turn's, else the current folder).
  --db DB              The record (default: the loop's out/<id>.db).
  --campaign CAMPAIGN  A campaign id or prefix (default: the loop's).
  --part PART          One part (default: the turn's part, else every part /
                       the whole).
  --json               The answer as JSON.
```

## flux serve

```text
usage: flux serve [-h] [--host HOST] [--port PORT] [--data DATA]
                  [--max-running MAX_RUNNING] [--secure-cookie] [--no-sandbox]

options:
  -h, --help            show this help message and exit
  --host HOST           Address to listen on (default 127.0.0.1; put a TLS
                        proxy in front for others).
  --port PORT
  --data DATA           The server's data (default: $XDG_DATA_HOME/flux/web).
  --max-running MAX_RUNNING
                        Runs at once per user.
  --secure-cookie       Session cookie over HTTPS only (behind a TLS proxy).
  --no-sandbox          Runs on the host, not sandboxed: a single trusted user
                        only.
```

## flux user

```text
usage: flux user [-h] [--invite] [--url URL] [--admin]
                 [--role {admin,internal,external}] [--data DATA]
                 {add,list,passwd,disable,enable,role,link} [name]

positional arguments:
  {add,list,passwd,disable,enable,role,link}
  name

options:
  -h, --help            show this help message and exit
  --invite              add: no password -- a link for them to choose it.
  --url URL             add --invite, link: the server's address, to print the
                        whole link.
  --admin               add: an admin (as --role admin).
  --role {admin,internal,external}
                        add or role: internal (the server's model and agent
                        settings, the default), external (their own, their own
                        home for the agents' logins) or admin.
  --data DATA           The server's data (default: $XDG_DATA_HOME/flux/web).
```

## flux selftest

```text
usage: flux selftest [-h] [--full] [--no-model] [--model MODEL]
                     [--model-timeout MODEL_TIMEOUT]

options:
  -h, --help            show this help message and exit
  --full                Also the README's first run (adder16, about three
                        minutes).
  --no-model            Only the checks that need no model.
  --model MODEL         The model name to check (default: the one a run would
                        use).
  --model-timeout MODEL_TIMEOUT
                        Seconds for the model-written problem.
```

## flux new

```text
usage: flux new [-h] [--dir DIR] name

positional arguments:
  name        The loop's name (1-60 letters, digits, _ and -): its id, and its
              folder unless --dir.

options:
  -h, --help  show this help message and exit
  --dir DIR   Where to write it (default: ./<name>); it must not exist or be
              empty.
```

## flux consult

```text
usage: flux consult [-h] --loop LOOP --out OUT [--author AUTHOR]
                    [--model MODEL] [--no-sandbox]
                    question

positional arguments:
  question         What you want to know, in words.

options:
  -h, --help       show this help message and exit
  --loop LOOP      The loop's folder (its document, files, out/<id>.db,
                   runs/loop.log).
  --out OUT        Where the answer goes (answer.md); the agent's working
                   folder.
  --author AUTHOR  Who answers: model (default), or a coding agent preset
                   (opencode, claude, codex).
  --model MODEL    The model, when the model answers.
  --no-sandbox     Run on this machine, not in the sandbox (also
                   FLUX_SANDBOX=0).
```

## flux login

```text
usage: flux login [-h] --home HOME [--no-sandbox] ...

positional arguments:
  cmd           -- and the agent's login command, e.g. -- opencode auth login

options:
  -h, --help    show this help message and exit
  --home HOME   The folder the agent's login is written to: HOME, writable.
  --no-sandbox  Run on this machine, not in the sandbox (also FLUX_SANDBOX=0).
```

## flux agent

```text
usage: flux agent [-h] {test} ...

positional arguments:
  {test}
    test      The agent's program, its login, its own status; --live: one
              short answer as a loop asks it.

options:
  -h, --help  show this help message and exit
```

### flux agent test

```text
usage: flux agent test [-h] [--live] [--json JSON] [--no-sandbox] agent

positional arguments:
  agent         opencode, claude, codex, or an agent the server adds

options:
  -h, --help    show this help message and exit
  --live        Also ask it one short question (a few hundred tokens).
  --json JSON   Also write the result as JSON to this file ('-': stdout only).
  --no-sandbox  Run on this machine, not in the sandbox (also FLUX_SANDBOX=0).
```

## flux ask

```text
usage: flux ask [-h] [--tui] [--file FILE] [--hidden HIDDEN] [--skill SKILL]
                [--author AUTHOR] [--dir DIR] [--passes PASSES]
                [--checks CHECKS] [--no-run] [--questions {decide,operator}]
                [--wait-s WAIT_S] [--repair] [--session SESSION] [--note NOTE]
                [--steps STEPS] [--screen-only] [--model MODEL]
                [--num-predict NUM_PREDICT] [--replies REPLIES]
                [--author-replies AUTHOR_REPLIES] [--no-sandbox]
                [prompt]

positional arguments:
  prompt                What you want, in words (none: the setup screen
                        opens).

options:
  -h, --help            show this help message and exit
  --tui                 The setup screen, then the loop screen with a review
                        of the problem before it runs.
  --file, -f FILE       An input: a spec, code, a reference, a PDF, tests
                        (repeatable; a folder is copied whole).
  --hidden HIDDEN       A path in --dir hidden from the loop's AI agents and
                        an agent author (repeatable): the document gets it in
                        flow.knowledge.hidden.
  --skill SKILL         A skill folder (SKILL.md), or a folder of them, for
                        the author and the designers (repeatable).
  --author AUTHOR       Who writes the problem: model (default), a coding
                        agent preset (opencode, claude, codex), or a JSON
                        agent spec.
  --dir DIR             The working directory (default: ./out/ask_<slug>; its
                        name is the problem's id).
  --passes PASSES       Stop after N passes (default: run until stopped --
                        `flux stop`, Ctrl-C, the TUI).
  --checks CHECKS       Repairs of a refused document per pass (default 3).
  --no-run              Write and check the document; run nothing.
  --questions {decide,operator}
                        operator: an agent author may ask a question, answered
                        in runs/author/inbox.jsonl (the web's page) within
                        --wait-s; decide (default): it decides.
  --wait-s WAIT_S       How long a question waits for its answer (default
                        600).
  --repair              The document is in --dir already, refused: check it
                        and repair it, write nothing new.
  --session SESSION     The agent author's session to continue.
  --note NOTE           With --repair: what the person says to do.
  --steps STEPS         The loop's steps per pass (default: the document's).
  --screen-only         Stop every pass at the first stage.
  --model MODEL         The model (the author when --author model, and the
                        loop's).
  --num-predict NUM_PREDICT
  --replies REPLIES     Scripted replies (a JSON list) for the loop's model:
                        no model.
  --author-replies AUTHOR_REPLIES
                        Scripted replies for a model author (tests, dry runs).
  --no-sandbox          Run on this machine, not in the container sandbox
                        (Podman or Docker; also FLUX_SANDBOX=0).
```

## flux task

```text
usage: flux task [-h] {check,migrate,run} ...

positional arguments:
  {check,migrate,run}
    check              Validate a task document and its tools; run nothing.
    migrate            Bring a problem's documents of an earlier form to
                       today's.
    run                Run a task document through the loop.

options:
  -h, --help           show this help message and exit
```

### flux task check

```text
usage: flux task check [-h] [--no-sandbox] file

positional arguments:
  file          The problem's folder, or one of its documents (problem.yaml,
                NAME.problem.yaml).

options:
  -h, --help    show this help message and exit
  --no-sandbox  Check on this machine, not in the sandbox (the check imports
                the document's code).
```

### flux task migrate

```text
usage: flux task migrate [-h] [--write] [--journal EVENTS] folder

positional arguments:
  folder            The problem's folder.

options:
  -h, --help        show this help message and exit
  --write           Write them (each only when it loads; the original kept as
                    <file>.orig).
  --journal EVENTS  A task journal (events.jsonl) of an earlier form instead:
                    brought to today's (the original kept as
                    events.jsonl.orig.gz).
```

### flux task run

```text
usage: flux task run [-h] [--skill SKILL] [--db DB] [--steps STEPS]
                     [--repair REPAIR] [--model MODEL] [--replies REPLIES]
                     [--out OUT] [--no-structured] [--role ROLE=NAME]
                     [--agent HALF [HALF ...]] [--plan FILE] [--tui] [--think]
                     [--num-predict NUM_PREDICT] [--passes N]
                     [--tool-hops TOOL_HOPS] [--hop-share HOP_SHARE]
                     [--patience PATIENCE] [--regenerate PART [PART ...]]
                     [--screen-only] [--reevaluate] [--reevaluate-of ID]
                     [--remeasure] [--rerun-baseline] [--json FILE]
                     [--no-prototype] [--no-patching] [--no-sandbox]
                     file

positional arguments:
  file                  The problem's folder, or one of its documents
                        (problem.yaml, NAME.problem.yaml).

options:
  -h, --help            show this help message and exit
  --skill SKILL         A skill folder (SKILL.md), or a folder of them, beside
                        the document's own (repeatable).
  --db DB               Campaign record (default: <document dir>/out/<task
                        id>.db).
  --steps STEPS         Planner steps (default: the document's).
  --repair REPAIR       Repair attempts per generation.
  --model MODEL         The model name on the endpoint in use: the local
                        Ollama by default, or the server FLUX_REMOTE_BASE_URL
                        names.
  --replies REPLIES     A JSON list of scripted replies: runs without a model.
  --out OUT             Write the decided artifact here (default: <document
                        dir>/out/<task id><extension>).
  --no-structured       Plain decoding, no schema.
  --role ROLE=NAME      Switch who fills one of the four roles, repeatable:
                        --role orchestrator=rules. `flux task check` lists the
                        choices.
  --agent HALF [HALF ...]
                        The AGENT takes these halves: tools (the model calls
                        compute/check/history/knowledge inside its turns),
                        orchestrate (it picks what next, which part, which
                        step of the ladder, with its reasons on record), plan
                        (it writes the loop plan the pass follows); all for
                        the three.
  --plan FILE           A loop plan document to follow: parts, budget, stages,
                        roles, tools; a field set to "agent" is the agent's to
                        fill.
  --tui                 The curses screen: tasks, results, log, the r loop
                        toggle.
  --think               Ask the model for its reasoning on every turn.
  --num-predict NUM_PREDICT
                        Output tokens per turn (default 6000).
  --passes N            Stop after N passes. Default: the document's
                        `budget.passes`, else run until stopped (`flux stop`,
                        Ctrl-C, q in the TUI); a pass at rest is followed by
                        one that explores.
  --tool-hops TOOL_HOPS
                        Rounds of tool calls a turn may make.
  --hop-share HOP_SHARE
                        Share of the model's context window a round that may
                        call tools may write (0.5).
  --patience PATIENCE   Prototype turns granted after each new best.
  --regenerate PART [PART ...]
                        Parts to draft again instead of resuming from the
                        record; all for every part.
  --screen-only         Stop the chain at the synthesis screen.
  --reevaluate          Invalidate the record's checks and measurements and
                        check and measure every design on it again.
  --reevaluate-of ID    Check and measure again the designs one invalidation
                        by hand took out (its id in Results).
  --remeasure           Measure again the designs a rejection asked to (the R
                        pass), then rest.
  --rerun-baseline      Measure pass 0 again though the document's baseline is
                        unchanged (otherwise the one on record stands).
  --json FILE           Also write the answer as JSON: the decision, the
                        frontier, what was refused, the application's own
                        result.
  --no-prototype        No prototype stage: the target directly.
  --no-patching         Repair by rewrite, not by edits.
  --no-sandbox          Run on this machine, not in the container sandbox
                        (Podman or Docker; also FLUX_SANDBOX=0).

Exit status: 0 a design meets every requirement (the decision, its artifact
written); 3 correct designs were measured but none meets every requirement yet
(no decision; the closest is reported, no artifact written); 1 no design was
measured, or the run could not start; 130 interrupted.
```

## flux report

```text
usage: flux report [-h] [--campaign CAMPAIGN] [--objective SPEC] [--out OUT]
                   db

positional arguments:
  db                   The campaign record.

options:
  -h, --help           show this help message and exit
  --campaign CAMPAIGN  A campaign id prefix (default: the latest in the
                       record).
  --objective SPEC     metric[:direction][:goal][:stage][:tie], repeatable, in
                       order; stands in for a record without the vector.
  --out OUT            Write the HTML report here (default: next to the
                       record, <record>-report.html).
```

## flux run

```text
usage: flux run [-h] [--log LOG] ...

positional arguments:
  argv        -- the command and its arguments

options:
  -h, --help  show this help message and exit
  --log LOG   The log file (default: <trace root>/runs/<stamp>.log).
```

## flux status

```text
usage: flux status [-h] [--campaign CAMPAIGN] db

positional arguments:
  db                   The campaign record.

options:
  -h, --help           show this help message and exit
  --campaign CAMPAIGN  A campaign id prefix (default: the latest).
```

## flux stop

```text
usage: flux stop [-h] [--campaign CAMPAIGN] [--now] [--why WHY] db

positional arguments:
  db                   The campaign record.

options:
  -h, --help           show this help message and exit
  --campaign CAMPAIGN  A campaign id prefix (default: the latest).
  --now                SIGINT the run now instead of waiting for the pass
                       boundary.
  --why WHY            A word on why, kept with the request.
```

## flux tools

```text
usage: flux tools [-h] [--json]

options:
  -h, --help  show this help message and exit
  --json      The catalog as JSON (what the loop crafter reads).
```

## flux attach

```text
usage: flux attach [-h] [--campaign CAMPAIGN] [--lines LINES] db

positional arguments:
  db                   The campaign record.

options:
  -h, --help           show this help message and exit
  --campaign CAMPAIGN
  --lines LINES
```
