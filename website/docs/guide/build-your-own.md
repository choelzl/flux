# Build your own problem

From an empty folder to a running search in six steps. Prefer a form? Use the
[Loop crafter](loop-crafter.md). Prefer a sentence? `flux ask "what you want" --file spec.pdf`
writes the document for you.

## 1. Pick a kind and write the start

```bash
flux new myproblem --kind rtl
```

| kind | the designs come from | judged by | AI model? |
|---|---|---|---|
| `rtl` | a model writes a Verilog module | Verilator against `golden.py`, Yosys and OpenROAD | yes |
| `python` | a model writes a Python function | `check.py` (correct?), `bench.py` (how fast?) | yes |
| `sweep` | a script writes one design per knob setting | `check.py`, `bench.py` | no |
| `rtl-sweep` | a script writes one module per knob setting | Verilator and Yosys | no |
| `tune` | the knobs go straight into your own commands | `check.py`, `bench.py` | no |

`flux new` writes `myproblem/` with a document, the scripts it names and a README. It runs as it
is.

## 2. Say what you want

Edit `myproblem/myproblem.problem.yaml`:

- `statement`: the request in plain words. The model reads it.
- `contract`: rules every design must follow (names, ports, what is forbidden).
- `objectives`: what "better" means, first one first, e.g.
  `{metric: fmax_mhz, direction: maximize, goal: 1000}` then `{metric: area_um2, direction: minimize}`.

## 3. Say what is correct

- `rtl`: edit `golden.py`: `PORTS` and a `golden(**inputs)` function returning the right outputs.
- `python`, `sweep`, `tune`: edit `check.py` so it prints `N failing` (0 when correct).
- For a knob search: list the knobs under `space:` and write each design in the generator script.

Papers help. Put PDFs, notes or reference code in `flux/mentor/knowledge/library/` (every
problem on the machine) or in a folder beside the document named by `knowledge: {library: papers}`.
Excerpts that match the statement, contract and parts reach the model's prompts, and the coding
agents get the file paths to open. `flow: {knowledge: none}` turns it off.

## 4. Check it

```bash
flux task check myproblem/myproblem.problem.yaml
```

It runs nothing. It lists the loop's boxes, the stages and their tools, the library, and says
what is missing.

## 5. Run it

```bash
flux task run myproblem/myproblem.problem.yaml --passes 1
```

Drop `--passes 1` to let it run until you stop it. Add `--tui` for the live screen.

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
| `gate` | a command that prints `N failing` or exits non-zero; or a list of named checks, run in order |
| `stages` | measurements, cheapest first: `{name, command}`; a command of yours prints `name=value` and lists `metrics:`; `cutoff:` one gate `{metric, at\|below\|within}` or a list, all must pass |
| `objectives` | `{metric, direction, goal}`: direction `minimize` or `maximize`; each `goal` is a limit (at least / at most), the goal-less ones decide in order, `balance: true` ones as their knee; `{keep: 0.9, above: 1.0}` is a limit relative to the best |
| `space` | knob -> its choices, for a search |
| `seeds` | settings measured before the search starts |
| `knowledge` | `{files: [...]}` the model reads with every prompt; `{library: papers}` a folder of papers |
| `flow` | who fills each box ([the loop](loop-shape.md)) |
| `budget` | `steps`, `passes`, `repair_attempts`, `finalists`, `workers`, `prototype` |

In commands: `{artifact}` is the design file, `{home}` the document's folder, `{python}` the
Python in use, and `{knob}` each knob of `space:`. A command starting with `flux` runs this Flux.

A gate can be several checks, cheapest first. Each has a name and a command; the first that
fails refuses the design, and the repair is told where it failed:

```yaml
gate:
  - {name: lint, run: "flux rtl lint {artifact}"}
  - {name: golden, run: "flux rtl test {artifact} --golden {home}/golden.py"}
```

A stage's `cutoff` is its gate: `cutoff: {metric: fmax_mhz, at: 1000}` sends on only the designs
that meet timing at 1 GHz. `flux tools` lists every check and stage Flux has, with its command.

`budget.prototype: true` (the default with a golden model) has the model write the algorithm in
Python first, checked on every input; Flux then writes the RTL. `prototype: systemc` does the same
with a SystemC module, translated by ICSC in `nix develop .#systemc` (elsewhere the model writes
the RTL from it). Use `false` for plain logic such as adders.

Every key is in the
[author reference](https://github.com/choelzl/flux/blob/main/flux/core/loop/src/flux_loop/author_reference.md).

## When you need code: a world

When a document cannot say it (a solver, a simulator, a custom search), write a Python class and
name it once: `world: flux_toy.world:World`. The loop calls the methods it has and keeps its
defaults for the rest. The usual ones:

| method | job |
|---|---|
| `judge(built, cand, subgoal, state) -> Verdict` | the gate: `Verdict(ok, score, why)` |
| `measure(cand, stage, state) -> dict` | one stage: metric name -> number |
| `search(state)` | a generator yielding batches of candidates |
| `design_prompt`, `parse_design` | how a model is asked for a design, and how its reply becomes one |

The world's settings are the document's `params:`. `flux task check` lists every method a world
may fill. Worked examples:
[bankmap](https://github.com/choelzl/flux/tree/main/flux/applications/bankmap),
[macarray](https://github.com/choelzl/flux/tree/main/flux/applications/macarray),
[nlu](https://github.com/choelzl/flux/tree/main/flux/applications/nlu).
