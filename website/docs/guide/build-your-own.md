---
hide:
  - navigation
---

# Build your own problem

From an empty folder to a running search in six steps. Prefer a form? Use the
[Loop crafter](loop-crafter.md). Prefer a sentence? `flux ask "what you want" --file spec.pdf`
writes the document for you.

## 1. Pick a kind and write the start

```bash
flux example rtl myproblem
```

| kind | the designs come from | judged by | AI model? |
|---|---|---|---|
| `rtl` | a model writes a Verilog module | Verilator against `golden.py`, Yosys and OpenROAD | yes |
| `python` | a model writes a Python function | `check.py` (correct?), `bench.py` (how fast?) | yes |
| `sweep` | a script writes one design per knob setting | `check.py`, `bench.py` | no |
| `rtl-sweep` | a script writes one module per knob setting | Verilator and Yosys | no |
| `tune` | the knobs go straight into your own commands | `check.py`, `bench.py` | no |

`flux example` writes `myproblem/` with a document, the scripts it names and a README. It runs as it
is. `flux new myproblem` writes the baseline instead: the document's every part with what goes there, to fill in. For larger complete problems to copy from, see the repository's
[`flux/applications/`](https://github.com/choelzl/flux/tree/main/flux/applications) folder.

## 2. Say what you want

Edit `myproblem/problem.yaml`:

- `statement`: the request in plain words. The model reads it.
- `contract`: rules every design must follow (names, ports, what is forbidden).
- `objectives`: what "better" means, first one first, e.g.
  `{metric: fmax_mhz, direction: maximize, goal: 1000}` then `{metric: area_um2, direction: minimize}`.

## 3. Say what is correct

- `rtl`: edit `golden.py`: `PORTS` and a `golden(**inputs)` function returning the right outputs.
- `python`, `sweep`, `tune`: edit `check.py` so it prints `N failing` (0 when correct).
- For a knob search: list the knobs under `flow.orchestrate.space` and write each design in the generator script.

Papers help. Put PDFs, notes or reference code in `flux/mentor/knowledge/library/` (every
problem on the machine) or in `library/` beside the document (this problem's own). Each paper is
summed up once by the model (or `flow.knowledge: {digest: opencode}`).
Excerpts that match the statement, contract and parts reach the model's prompts, and the coding
agents get the file paths to open. `flow: {knowledge: off}` turns it off.

## 4. Check it

```bash
flux task check myproblem
```

It runs nothing. It lists the loop's boxes, the stages and their tools, the library, and says
what is missing.

## 5. Run it

```bash
flux task run myproblem --passes 1
```

Drop `--passes 1` to let it run until you stop it. Add `--tui` for the live screen. A search
(`flow.orchestrate` with its `space`) tries one design a pass, the next picked from what the last ones
measured: give it a pass per point, or `budget.batch: N` for N designs a pass.
[Run a problem](run.md) has the options, stopping and resuming, and choosing the AI model.

## 6. Read the result

The report at the end names the design to build, the trade-offs, and every refused design with
the reason. The chosen design is in `myproblem/out/`; `flux report myproblem/out/myproblem.db`
writes an HTML page of the whole search.

## Key reference

| key | what it says |
|---|---|
| `id` | a short name (letters, digits, `_`); names the record, so an edited document resumes it |
| `statement`, `contract` | the request and its rules, in words |
| `language` | `systemverilog`, `verilog`, `python`, `c`, `cpp`, `text`, ...: the file type |
| `objectives` | `{metric, direction, goal}`: direction `minimize` or `maximize`; each `goal` is a limit (at least / at most), the goal-less ones decide in order, `balance: true` ones as their knee; `{keep: 0.9, above: 1.0}` is a limit relative to the best |
| `flow` | each box of [the loop](loop-shape.md): who fills it, and its own settings (below) |
| `flow.test` | a command that prints `N failing` or exits non-zero; or a map of named checks, run in order |
| `flow.measure` | measurements, cheapest first, by name: `screen: <command>`; a command of yours prints `name=value` and lists `metrics:`; `cutoff:` one gate `{metric, at\|below\|within}` or a list, all must pass |
| `flow.orchestrate` | the search: `sweep`, ..., or `{policy: sweep, space: {knob: [choices]}, seeds: [...]}` (the settings measured first) |
| `flow.knowledge` | `{files: [...]}` the model reads with every prompt; `agent: opencode` digests the library instead of the model; `off` |
| `flow.select` | `{finalists: 3}`: how many reach the costliest stage |
| `budget` | `steps`, `passes`, `repair_attempts`, `workers`, `prototype` |

In commands: `{artifact}` is the design file, `{home}` the document's folder, `{python}` the
Python in use, and `{knob}` each knob of `flow.orchestrate.space`. A command starting with `flux` runs this Flux.

A gate can be several checks, cheapest first. Each has a name and a command; the first that
fails refuses the design, and the repair is told where it failed:

```yaml
flow:
  test:                    # by name, run in the order written
    lint: flux rtl lint {artifact}
    golden: {run: "flux rtl test {artifact} --golden {home}/golden.py", timeout_s: 300}
```

A stage's `cutoff` is its gate: `cutoff: {metric: fmax_mhz, at: 1000}` sends on only the designs
that meet timing at 1 GHz. `flux tools` lists every check and stage Flux has, with its command.

`budget.prototype: true` (the default with a golden model) has the model write the algorithm in
Python first, checked on every input; Flux then writes the RTL. `prototype: systemc` does the same
with a SystemC module, translated by ICSC in `nix develop .#systemc` (elsewhere the model writes
the RTL from it). Use `false` for plain logic such as adders.

Every key is in the
[author reference](https://github.com/choelzl/flux/blob/main/flux/core/loop/src/flux_loop/author_reference.md).

## When you need code: commands beside the document

When a document cannot say it in prose or numbers (a solver, a simulator, a search of its own),
write a script and name it in the box it belongs to:

| what the document cannot say | the command beside it | what it reads and writes |
|---|---|---|
| a check of your own | `flow.test: {name: "{python} {home}/check.py {artifact}"}` | prints `N failing`; exit 3 = did not build |
| a measurement | `flow.measure: {name: {command: ..., metrics: [...]}}` | prints `name=value` |
| a generator over a space | `flow.generate: {command: "... {knob} {artifact}"}` or `{point}` | writes `{artifact}` |
| a search of your own -- a solver, a proof, a model it asks itself | `flow.orchestrate: {command: "... {history} {state} {params}"}` | reads what was measured and refused, keeps its state, prints the next candidates, lessons, a conclusion (D799) |
| a composition of sub-loops | the parent's `flow.generate: {command: "... {parts} {artifact}"}` | reads each sub-loop's answer, writes the whole (D801) |
| settings | `params:` | `{params}`: a JSON file any command reads |

Worked examples:
[macarray](https://github.com/choelzl/flux/tree/main/flux/applications/macarray),
[bankmap](https://github.com/choelzl/flux/tree/main/flux/applications/bankmap),
[interconnect_mapping](https://github.com/choelzl/flux/tree/main/flux/applications/interconnect_mapping),
[nlu](https://github.com/choelzl/flux/tree/main/flux/applications/nlu) (seven sub-loops in folders).
