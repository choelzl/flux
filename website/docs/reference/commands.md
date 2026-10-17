# Commands and placeholders

Every command a document names runs the same way, receives the same placeholders, and follows
a contract: its arguments, what it prints, its exit code.

## How commands run

Every command a document names (checks, stages, estimates, the generator, the search, the
baseline, the composition) runs the same way. Your own agent's command differs; see
[agent command](#agent-command).

- **Written as** a list of arguments or one string. A string is split like a shell would
  split it, but **no shell runs**: for a pipe or redirection, call one yourself:
  `["sh", "-c", "cat {artifact} | wc -l"]`.
- **`flux` first** runs Flux's own CLI with the same interpreter (`flux tools ...`).
- **`fluxpreset` first** runs one of [Flux's presets](presets.md) the same way
  (`fluxpreset rtl measure ...`).
- **Working directory** is `{workdir}`, the pass's trace folder, shared by every design in
  the pass. Name your files with `{home}/...`; relative paths do not reach the loop's folder.
- **Environment:** Flux's own, unchanged. Nothing is added.
- **Standard input:** inherited from Flux, not data. A command must not read stdin.
- **Output:** stdout and stderr are captured separately; checks and stages read both,
  joined. Line endings are normalised. Live shows the command, its folder, the ends of its
  output as it runs, and its exit.
- **Timeouts** stop the whole process group. A missing program is reported as "not found on
  PATH" before the run; `flux task check` lists them.
- **Placeholders** are replaced inside every argument, without quoting. At load, a
  `{name}` that is neither the loop's nor a knob is refused (an argument containing spaces,
  such as an `sh -c` script, is not checked). A placeholder with no value for that command is
  passed through as written.

## Command placeholders

| Placeholder | Value | Generator | Check, stage, estimate | Search | Baseline | Composition |
|---|---|---|---|---|---|---|
| `{artifact}` | Path of the design file (`<workdir>/<name><ext>`) | writes it | reads it | `""` | writes it | writes the whole |
| `{home}` | The document's folder (absolute) | ✓ | ✓ | ✓ | ✓ | ✓ |
| `{workdir}` | The pass's working folder | ✓ | ✓ | ✓ | ✓ | the compose folder |
| `{python}` | The interpreter running Flux | ✓ | ✓ | ✓ | ✓ | ✓ |
| `{name}` | The design's name (`mul8#3`, `slice_sieve`) | ✓ | ✓ | `search` | ✓ | the loop's id |
| `{part}` | The part being drafted, else `""` | ✓ | `""` | `""` | `""` | `""` |
| `{params}` | Path of a JSON file of `params` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `{knob}` | One knob's value (`True`/`False` for booleans) | search mode | ✓ | | first choices | |
| `{point}` | Path of a JSON file of every knob | search mode | ✓ | `""` | ✓ | `""` |
| `{failure}` | Why the last attempt was refused (text) | drafting | | | `""` | |
| `{attempt}` | `1`, `2`, ... | drafting | | | `1` | |
| `{history}`, `{state}` | Search history and the search's own state file | | | ✓ | | |
| `{parts}` | Path of a JSON map of the children's designs | | | | | ✓ |
| `{scope}` | The scope a pass works on, in `orchestrate.scope`'s `brief` and `check` | | | | | |
| `{prompt}`, `{prompt_file}`, `{session}`, `{answer}` | Brief and session, for [your own agent](#agent-command) | | | | | |

**File formats:**

- **`{params}`** is `params.json` in `{workdir}`: the document's `params` as a JSON object
  (a sub-loop's merged over its parent's), rewritten for every command.
- **`{point}`** is `<artifact>.point.json`: the knobs as a JSON object, components nested
  (`{"algorithm": "list_sieve", "prefetch": {"distance": 2}}`); an optional component that is
  off is left out.
- **`{failure}`** is the text itself, not a file: the last 4000 characters of the failing
  check's output (prefixed `failed at <check>:` with several checks), or a send-back reason.
  It is empty on the first attempt.
- **`{parts}`** is `parts.json` in the compose folder: `{"<child name>": "<absolute path of
  its decided design>"}`, in declared order.

## Command contracts

### Generator command

`generate: {command: ...}` writes one design to `{artifact}`. Its stdout is not the design.

**Drafting** (no space): each draft is checked; if the checks refuse it, the command runs
again with `{failure}` and `{attempt}` set, up to `budget.repair_attempts` times. A failing
command (non-zero exit, or exit 0 without the file) ends the repairs at once; so does
writing the same design twice.

**Search** (with a space): the command runs once per proposed point, with that point's
`{knob}`s and `{point}`. A failing command drops the point; there is no repair. The design is
named after its knob values.

```python
# document: generate: {command: "{python} {home}/gen.py {artifact} {attempt} {failure}"}
import sys
out, attempt, failure = sys.argv[1], int(sys.argv[2]), sys.argv[3]   # failure is "" at first
design = "def count_primes(n):\n    ...\n" if not failure else "...a design that fixes it..."
open(out, "w").write(design)                                         # exit 0
```

### Check command

A check reads `{artifact}` and reports failures. Flux reads stdout and stderr together:

1. **Exit 3** means the design did not build, whatever was printed. For a check named
   `build`, any non-zero exit means that.
2. Otherwise, with `count_re` (default `(\d+) failing`): the **first** match's number is the
   failure count. With `fail_re` only: each match is one failure.
3. With no match: exit 0 passes, any other exit is one failure.

A printed count wins over the exit code: `2 failing` with exit 0 fails, and `0 failing` with
exit 1 passes. The first check with failures refuses the design; its output (last 4000
characters) is what Live shows and what a model or agent gets back to repair. A check's
debug output on stderr never fails it unless a pattern matches there.

```python
# document: test: {correctness: "{python} {home}/check.py {artifact}"}
import importlib.util, sys
try:
    spec = importlib.util.spec_from_file_location("design", sys.argv[1])
    design = importlib.util.module_from_spec(spec); spec.loader.exec_module(design)
except Exception as exc:
    print(f"did not load: {exc}"); sys.exit(3)                 # 3: did not build
cases = {10: 4, 100: 25, 1000: 168}
bad = [n for n, want in cases.items() if design.count_primes(n) != want]
for n in bad:
    print(f"FAIL n={n}: got {design.count_primes(n)}, expected {cases[n]}")
print(f"{len(bad)} failing of {len(cases)}")
sys.exit(1 if bad else 0)
```

### Measurement command

A stage reads `{artifact}` and prints its numbers. It **must exit 0**; on any other exit its
numbers are discarded and the design is refused at that stage. Flux reads stdout and stderr:

- **`name=value`**, at the start of a line or after a space: the **first** occurrence of each
  metric counts. `value` is a number (`1.5`, `-2e3`); `nan` and `inf` are unmeasured.
- **A JSON object** on a line of its own (it may span lines): its keys that are declared
  metrics are read. A `name=value` line overrides it.
- **A dictionary metric** is `name={"test": 12, "other": null}` (the last occurrence counts).
- **`metrics_re`**, if given, replaces the `name=value` patterns: one capture group each.

Exit 0 with no metric found refuses the design ("no metric matched in the output"). A stage
that reports only some of its metrics is still recorded with those it did. For timings, set
`budget.workers: 1`: runs at the same time disturb each other.

```python
# document: measure: {bench: {command: "{python} {home}/bench.py {artifact}", metrics: [time_ms]}}
import importlib.util, statistics, sys, time
spec = importlib.util.spec_from_file_location("design", sys.argv[1])
design = importlib.util.module_from_spec(spec); spec.loader.exec_module(design)
runs = []
for _ in range(5):
    t = time.perf_counter(); design.count_primes(1_000_000); runs.append((time.perf_counter() - t) * 1e3)
print(f"time_ms={statistics.median(runs):.3f} time_spread_ms={max(runs) - min(runs):.3f}")
```

**The measurement cache:** a design measured once by a stage is not measured again while
nothing that stage depends on changes: the design's text (or knobs), the stage's command,
metrics and patterns, `params`, the knobs the command names, the files in the folder (except
`out/`, `runs/`, `workbench/`, `library/` and the documents), the `{home}/...` files the
command names, and the builds of its programs and `needs`. Change any of them and it is
measured again. Only successful measurements are cached.

### Estimate command

`estimate: {kind: command, command: ..., margin: 0.1}` runs before the stage, once for each
design the cache does not hold, with the stage's placeholders and timeout. It prints the
stage's metrics in the same format. A non-zero exit or no numbers means no estimate: the
tool runs. A design whose estimate misses a cutoff or a limit by more than `margin` (a
fraction of the limit) is skipped and recorded as refused by the estimate.

```python
# document: estimate: {kind: command, command: "{python} {home}/estimate.py {artifact}"}
import sys
print(f"time_ms={len(open(sys.argv[1]).read()) * 0.01:.3f}")
```

### Search command

`orchestrate: {command: ..., timeout_s: 600}` is your own search. Each round, Flux writes
`{history}` and runs the command; the command answers with JSON on stdout.

**`{history}`** (`search-history.json` in `{workdir}`):

```json
{"measured": [{"name": "w4", "knobs": {"w": 4}, "stage": "bench", "metrics": {"time_ms": 3.1}, "by": "mine"}],
 "refused":  [{"name": "w9", "why": "failed at correctness: 2 failing"}],
 "guidance": "operator notes, if any",
 "round": 3}
```

**`{state}`** (`out/<record>.search-state.json`) is yours: read it if it exists, write it
every round; it survives restarts.

**The answer** is the last line of stdout that is a JSON object (or the whole stdout):

| Key | Meaning |
|---|---|
| `candidates` | `[{name, artifact, knobs, why, by}]`: `name` required; `artifact` is the design's **text** (not a path); `knobs` reach the checks and stages as `{knob}`/`{point}` |
| `lessons` | Lines added to the run's lessons |
| `not_established` | Lines added to the report |
| `conclusion` | What the search concluded, recorded when nothing is decided |
| `say` | A line printed with the round |
| `done` | `true`: this is the last batch |

No candidates also ends the search. Candidates go straight to the checks and stages; the
generator does not run for them. A non-zero exit or no JSON ends the search with a note.

```python
# document: orchestrate: {command: "{python} {home}/search.py {history} {state}", timeout_s: 300}
import json, os, sys
history, state_path = json.load(open(sys.argv[1])), sys.argv[2]
state = json.load(open(state_path)) if os.path.exists(state_path) else {"next": 1}
tried = {m["name"] for m in history["measured"]} | {r["name"] for r in history["refused"]}
cands = [{"name": f"w{w}", "artifact": f"WIDTH = {w}\n", "knobs": {"w": w}, "why": "next width"}
         for w in (state["next"], state["next"] + 1) if f"w{w}" not in tried]
state["next"] += 2
os.makedirs(os.path.dirname(state_path), exist_ok=True)
json.dump(state, open(state_path, "w"))
print(json.dumps({"candidates": cands, "done": state["next"] > 8}))
```

### Baseline command

`baseline: {command: ...}` runs once, before pass 1, with the first point (every knob's first
choice, overlaid with the first seed). It either writes the reference design to `{artifact}`,
or prepares the project and writes nothing (then the checks and stages run on the project,
through `{home}/...` paths). A non-zero exit records "baseline failed" and the loop goes on.

### Composition command

A parent with `subtasks` and `generate: {command: "{python} {home}/compose.py {parts} {artifact}"}`
runs it once every child has decided. `{parts}` maps each child to its decided design's
path; the command writes the whole to `{artifact}`. The whole goes to the measurements
directly (it is not checked again). A failure leaves no whole, with a note in the report.

```python
import json, sys
parts = json.load(open(sys.argv[1]))                       # {"recip": "/.../recip.sv", ...}
open(sys.argv[2], "w").write("\n".join(open(p).read() for p in parts.values()))
```

### Agent command

Your own coding agent: `by: {command: [my-agent, "--prompt", "{prompt_file}"], output: text,
resume: [...]}`. Write `command` as a **list**.

- Placeholders use Python formatting: `{prompt}` (the brief; very long briefs become an
  instruction to read the file), `{prompt_file}`, `{artifact}`, `{workdir}` (the session
  folder), `{part}`, `{name}`, `{python}`, `{home}`, `{workbench}`, `{probe}`; a resume
  command also gets `{session}` and `{answer}`. Write a literal brace as `{{` or `}}`; an
  unknown name refuses the draft.
- The brief goes to stdin unless the command names `{prompt}`, `{prompt_file}` or `{answer}`.
- It runs in its session folder with Flux's environment, plus `FLUX_<NAME>_ENV` and
  `FLUX_PROBE`. Default timeout 1800 s.
- It writes `{artifact}`. If it does not, its reply is read: a JSON `artifact` field, else the
  largest fenced code block.
