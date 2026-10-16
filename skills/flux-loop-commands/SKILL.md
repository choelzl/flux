---
name: flux-loop-commands
description: What a Flux loop's workers can read and run while the loop runs -- a coding agent inside a turn, a direct model with tools, or a step script (check, stage, generator, search, compose) -- plus the commands for reading a loop from outside. Use when writing a step or search script, when acting as an agent inside a Flux turn (PROMPT.md / BRIEF.md in the work directory), or when asked how a loop retrieves its ideas, previous attempts and designs, measured numbers, papers and digests, or how to run the gate or a stage on a file.
---

# Flux loop commands

What you can reach depends on **who you are in the loop**. Find your row, then read its section.

| You are | You know it by | Reach things through |
|---|---|---|
| A coding agent drafting a design | `PROMPT.md` in your cwd, `FLUX_PROBE` set | `flux loop ...`, the prompt's sections, `workbench/`, `flux probe`, `<artifact>.ideas.json` |
| A coding agent working a box (plan, orchestrate, select, ...) | `BRIEF.md` in your cwd | the brief; answer in `out.json` |
| A direct model with tools | tool definitions in the request | `history`, `ideas`, `knowledge`, `check`, `timing`, `compute`, `skill` (orchestrator: `standings`, `decisions`) |
| A step command (check, stage, generator, search, compose) | invoked with arguments, no stdin | the placeholders in its command line; `flux loop ... --loop {home} --json` |
| Outside the loop (you, the operator, another agent) | a shell in the checkout | `flux loop ...`, `flux report / log / status / consult`, `task run --json` |

## 0. `flux loop`: ask the loop what it already knows

One command family for everyone. It reads the loop's record and library, and adds nothing to the record.
Inside an agent's turn it reads **that turn's loop, part and campaign** (from `FLUX_PROBE`); elsewhere
name the loop with `--loop FOLDER` (default: the current folder).

```bash
flux loop tried   [--part P] [--last 12] [--failed]   # each design tried, oldest first: stages reached, numbers, why it stopped, its ideas
flux loop results [--part P] [--stage S] [--top 10]   # measured designs at one stage, ranked by the loop's own objectives; meets / misses the goals
flux loop ideas   [--part P] [--status measured]      # the ideas notebook: hypothesis, status, the evidence on record
flux loop design  NAME                                # a design's text (a previous version or attempt); a unique prefix is enough
flux loop diff    A B                                 # two designs line by line; either may be a file (yours vs the best)
flux loop library [QUERY] [--n 5]                     # every paper, one line each; with QUERY the nearest, with their digests
flux loop digest  PAPER                               # one paper's digest (file name or stem)
flux loop gate    FILE                                # the loop's own gate on a file
flux loop measure FILE [--stage S]... [--gate]        # the loop's own stages on a file, each on its own
```

Every one takes `--json` (the answer as data), `--db` and `--campaign`. Exit 0 answered; 1 a gate or
stage failed, or `diff` found differences; 2 the loop, its record or the thing named is not there
(said in one line; a loop that has not run has no record, and none is made). Inside a turn,
`gate`/`measure` are probes and count against the turn's probe budget; outside one there is no budget.

A good opening for a design turn: `flux loop results --top 5`, then `flux loop tried --failed`
(what was refused and why), then `flux loop design <best>` and work from it; before writing,
`flux loop ideas` so you do not repeat a measured idea; at the end, `flux loop diff <best> mine.sv`.

## 1. Inside an agent's design turn

Your cwd is the turn's work directory. It holds:

- `PROMPT.md` -- the brief. On a repair it also carries the prior artifact and the failure text.
  Its sections, when present:
  - **TRIED SO FAR** -- the best and latest measured designs for this part (up to 8): name,
    stage, metrics. Read it before proposing; do not resubmit a tried design.
  - **IDEAS NOTEBOOK** -- hypotheses across passes with their recorded evidence
    (`idea-...: title [status]`, then design / stage: status and numbers). Measured is not better.
  - **LIBRARY** -- the digests of the 5 nearest papers, with their paths; open the paper itself
    with `pdftotext` or `python3` when the digest is not enough.
- `workbench/` -- a link to the loop's workbench, kept across runs and shared by sub-loops:
  `workbench/tools/` for scripts you build and want next time, `workbench/notes/` for what you
  learned. Read it first; write to it last. It is the only place outside `out/` you may write.

**Run the loop's own gate or stage on a file** (`flux probe` or the same through `flux loop gate|measure`; raw tools are denied):

```bash
flux probe gate design.sv                           # GATE: passed | GATE: <n> failures + why
flux probe measure design.sv                        # first stage: <stage>: m=v, ...; limits ... met|FAILS; objectives
flux probe measure design.sv --stage confirm --gate # gate first, then the named stage
flux probe measure design.sv --stage screen --stage confirm   # each on its own, side by side
```

Exit 0 pass / within limits, 1 fail, 2 refused (no probe context, probe budget spent, unknown
stage). The cap per turn is the box's `probe: {gate: 20, stages: 3}`; the output ends with
`[gate probe k of cap]`. In a prototype turn only `gate` is allowed. Each probe is recorded as
your own check, not as the loop's verdict.

Denied inside a turn: verilator, yosys, openroad, sta, klayout, champsim, compilers (gcc, g++,
cc, clang), make, cmake, ninja, pytest, `flux rtl`, `flux task`, `flux run`, bash, sh. Allowed:
`python3`, `pdftotext`, Icarus.

**Record your hypothesis** beside the artifact, in `<artifact>.ideas.json` (optional; never
instead of the artifact):

```json
{"idea": {"title": "Booth radix-4 with a Wallace tree", "hypothesis": "halves partial products; shorter critical path", "test": "fmax_mhz at confirm"},
 "ideas": [{"title": "Dadda reduction", "hypothesis": "fewer adders than Wallace at equal depth"}]}
```

To revisit an existing idea write `"idea": {"id": "idea-..."}`. The loop records the real
checks and measurements against it; never write results yourself.

## 2. Inside a box turn

`BRIEF.md` says the box (validate, orchestrate, plan, dse, generate, critique, extract, select),
the state it decides on and the answer's shape. Write the answer to `out.json` (`out-NNN.json`
when the box runs with `session: pass`). A plan may set parts, stages, roles, tools and budgets,
**never the gate**.

## 3. A direct model's tools

Enabled with `--agent tools|orchestrate|plan|all` or `budget.tools`. The tool descriptions in
the request are authoritative; in short:

| Tool | Gives |
|---|---|
| `history {limit: 8..30}` | what this campaign already tried for the part: attempts, score, diagnosis |
| `ideas {action: list|propose|select, idea}` | the cross-pass notebook; propose for later or select for this experiment |
| `knowledge {query}` | searches the method sheet and the library's papers |
| `check {prototype}` | runs THE test on a prototype exactly as the gate will (prototype stage only) |
| `timing {steps}` | the placed critical path of the current best |
| `compute {code}` | a short Python snippet in a sandbox |
| `skill {name, file}` | reads a skill handed to the loop |
| `standings` (orchestrator) | the goal, where the loop is now, the whole and its parts, admitted and sent back |
| `decisions` (orchestrator) | the last 20 picks with their reasons |

## 4. In a step command

A step command gets **only its arguments**: no stdin, no extra environment, cwd = the pass's
trace folder, the whole process group killed at `timeout_s`. Ask for what you need with
placeholders in the document:

| Need | Placeholder | Shape |
|---|---|---|
| The design to check or measure | `{artifact}` | path |
| The loop folder (golden.py, scripts) | `{home}` | absolute path |
| The previous failure (generator repairing) | `{failure}`, `{attempt}` | text (last 4000 chars), 1, 2, ... |
| The loop's `params:` | `{params}` | params.json |
| This point's settings | `{knob}` each, or `{point}` | value / `<artifact>.point.json` |
| Everything measured and refused so far (search) | `{history}` | `{"measured":[{name,knobs,stage,metrics,by}],"refused":[{name,why}],"guidance","round"}` |
| The search's own memory across rounds | `{state}` | a file you read and rewrite |
| The children's designs (compose) | `{parts}` | parts.json |
| The scope this pass works on | `{scope}` | text |

What a step prints:

- **check** -- a failing count (matched by `count_re`) or a non-zero exit refuses; exit 3 means
  "did not build". The tail of the output becomes the generator's `{failure}`.
- **stage** -- exit 0 and `name=value` lines, a JSON object, or a dictionary metric
  `name={"case": 1.2}`.
- **search command** -- JSON `{"candidates":[{"name","artifact","knobs","why","by"}],"lessons","not_established","conclusion","say","done"}`.

## 5. From outside the loop

```bash
flux status out/mul8.db                 # running? pid, passes this run, at rest, stop requested
flux attach out/mul8.db --lines 40      # follow the live log
flux report out/mul8.db [--objective fmax_mhz:maximize:1000] [--out report.html]   # standings, frontier, library cited
flux log out/mul8.db --last 10          # every model/agent turn: prompt, reply, tool calls, errors
flux log out/mul8.db --turn 7           # one turn whole
flux consult "why does recip miss timing?" --loop applications/nlu --out /tmp/ask   # answer.md from a read-only snapshot
flux task run DOC --passes 1 --json answer.json   # the answer as JSON
flux task run DOC --reevaluate          # re-check and re-measure every design on the record
flux stop out/mul8.db [--now] [--why "good enough"]
```

`--json` shape: `{task, record, stopped, decided_by, decision: {name, key, stage, knobs,
metrics} | null, feasible, closest: {..., unmet} | null, artifact, frontier: [row], refused:
[{name, why}], not_established, lessons, report: [lines]}`.

The record is SQLite (`out/<id>.db`); read it through `flux loop` and these commands, not by
hand, unless asked. Designs: the decided one is `out/<id><ext>`, any other is
`flux loop design NAME`; every attempt's trace sits under
`FLUX_TRACE_ROOT` (default `$TMP/flux-traces/<campaign>/<stamp>/`, with `agents/<box>/NNN/`
and `turns.jsonl`).

## 6. Papers and digests

Read them with `flux loop library [QUERY]` and `flux loop digest PAPER`. Digesting is the loop's own
work, with no command of its own: put the documents in the loop's `library/` (or the shared library,
`FLUX_LIBRARY`) and each pass's setup digests up to 8 new ones (`FLUX_DIGEST_PER_PASS`), each read
for this loop's statement; the digests are kept on the record and shared across runs in
`~/.cache/flux/digests` (`FLUX_DIGESTS`), where `flux loop` also finds them. The digest's worker is `knowledge.digest`
(`model` or an agent). Readable formats: text (md, txt, rst, tex, bib, csv, tsv, json, yaml,
xml, org, adoc), Office (docx, xlsx, pptx and the odt/ods/odp family), web (html, epub),
pdf (pdftotext), legacy doc/xls/ppt/rtf (LibreOffice), and source files (sv, v, vhd, py, cpp,
c, h, scala). Digests reach every prompt (LIBRARY section) and the `knowledge` tool.
