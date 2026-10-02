<p align="center">
  <img src=".github/logo.svg" alt="Flux logo" width="160">
</p>

# Flux

**Flux searches for the best hardware design for a problem you describe: a model or a script
proposes designs, real tools check and measure them, and Flux picks the winner and tells you why.**

You describe the problem in one short file, the **document** (`*.problem.yaml`): what to build,
how to tell a right design from a wrong one (the **gate**), what to measure (the **stages**,
cheapest first) and what "better" means (the **objectives**). Flux keeps a record of every design
it tried. Website: <https://choelzl.github.io/flux/>.

## Get started

### Quick try (Python 3.11+ only, no hardware tools, no AI model)

1. Get the code and install it:

   ```bash
   git clone https://github.com/choelzl/flux.git flux-repo
   cd flux-repo
   python3 -m venv .venv && .venv/bin/pip install -e ./flux
   ```

2. Write a ready-to-run problem, then run it:

   ```bash
   .venv/bin/flux new primes --kind sweep
   .venv/bin/flux task run primes --passes 6    # a pass a point
   ```

Extras: `pip install -e "./flux[bankmap]"` (z3), `[nlu]` (scipy), `[zigzag]`, `[all]`.

### Full install (Verilator, Yosys, OpenROAD, ChampSim)

1. Install [Nix](https://nixos.org/download) and enable flakes: add
   `experimental-features = nix-command flakes` to `~/.config/nix/nix.conf`.
2. Enter the tool shell (the first time downloads the tools):

   ```bash
   git clone https://github.com/choelzl/flux.git flux-repo
   cd flux-repo/flux
   nix develop --accept-flake-config
   ```

3. Check that everything works (PASS, FAIL or SKIP per line, with the reason):

   ```bash
   flux selftest --no-model
   ```

4. Run a first hardware search, no AI model needed (about three minutes):

   ```bash
   flux task run applications/adder16 --screen-only --passes 12
   ```

### Add an AI model

By default Flux uses a local [Ollama](https://ollama.com) and the model in `FLUX_LLM_MODEL`
(default `qwen3.8:latest`): `ollama pull qwen3.8:latest`. For any OpenAI-compatible server
(LocalAI, llama.cpp, vLLM, OpenRouter), set these instead. Then, from `flux/`, run a problem the
model writes:

```bash
export FLUX_REMOTE_BASE_URL=http://my-server:8080
export FLUX_REMOTE_MODEL=<model name on that server>
export FLUX_REMOTE_API_KEY=<key>                   # only if the server wants one
flux task run applications/primes --passes 3
```

To set them once for this machine, put the same lines (without `export`) in
`~/.config/flux/flux.env`; keep the key in a file of its own and name it with
`FLUX_REMOTE_API_KEY_FILE=~/.config/flux/my.key`. A variable set in the shell still wins.

[docs/models.md](docs/models.md) has recipes and coding-agent setup.

## What it can do

- **RTL from a golden model.** You give a Python function that computes the right answer; a
  model writes the Verilog; Verilator tests every design against your function.
- **Prototypes first.** For numeric designs the model writes the algorithm in Python or SystemC,
  checked on every input in seconds; Flux then writes the RTL (SystemC through ICSC, in
  `nix develop .#systemc`).
- **Design-space sweeps and searches.** List the knobs; pick `sweep`, `montecarlo`, `gradient`,
  `anneal`, `genetic`, `pareto`, `llm` (a model picks the points) or a coding agent.
- **Coding agents in any box.** Claude Code, Codex or OpenCode can write the designs or answer
  any box that does not establish facts.
- **Real measurements.** Yosys and OpenROAD on ASAP7 (speed, area, power), ChampSim prefetcher
  studies (`flux champsim`), ZigZag accelerator sizing, or any command of yours.
- **Calibration.** Cheap stages are compared with costly ones; a quick estimate is never
  reported as a measurement.
- **An honest report.** The design to build first, then the trade-offs, measured vs modelled,
  and every refused design with the reason.
- **From a sentence.** `flux ask "what you want" --file spec.pdf` writes the document.

## The loop

Every document runs through the same boxes. Each is filled by rules, a model or a coding agent,
chosen in the document's `flow:` block. Boxes that establish facts are never delegated.

| box | what it does | who can fill it |
|---|---|---|
| validate | checks the document before anything runs | rules, model, coding agent |
| orchestrate | picks the next piece of work | rules, model, coding agent |
| plan | plans each pass: parts, order, method, budget | none, model, coding agent |
| dse | searches the knobs | a search policy, model, coding agent |
| generate | writes each design | model, script, fixed list, coding agent |
| test | the gate: refuses any wrong design | never delegated |
| critique | challenges the parts and the decision | none, model, coding agent |
| analytical | cheap estimates: formulas, cost models | rules or a learned estimate; never delegated |
| simulation | real tools | never delegated |
| calibrate | compares cheap stages with costly ones | on or off; never delegated |
| select | picks the winner from the objectives | objectives; a coding agent may break ties |
| feedback | your notes, typed during a run | you, or none |
| knowledge | what the model reads | files, or a model's digest |
| extract | lessons mined from past runs | none, mined, coding agent |
| records | keeps every design, number and refusal | always on; never delegated |

`flux task check <document>` prints this for a document, with the half in force.

## Applications

Each folder in [`flux/applications/`](flux/applications/) holds one document.

| application | what it finds | AI model? |
|---|---|---|
| [`adder16`](flux/applications/adder16/) | the smallest 16-bit adder at 3000 MHz, from 12 generated designs | no |
| [`mul8`](flux/applications/mul8/) | a signed 8x8 multiplier at 1000 MHz, written by a model | yes |
| [`primes`](flux/applications/primes/) | the fastest Python `count_primes(n)` (not hardware) | yes |
| [`npu_gemm`](flux/applications/npu_gemm/) | the smallest accelerator that runs a workload in 500 cycles (ZigZag) | no |
| [`gelu_fp16`](flux/applications/gelu_fp16/) | an FP16 GELU within 1 ULP, invented as a formula by a coding agent | yes |
| [`nlu`](flux/applications/nlu/) | an FP16 unit for seven math functions, each within 1 ULP, at 800 MHz | yes |
| [`macarray`](flux/applications/macarray/) | the smallest multiply-accumulate element at 1000 MHz | for invention only |
| [`prefetcher`](flux/applications/prefetcher/) | a ChampSim L2 prefetcher configuration, or a new prefetcher (traces not in git) | yes |
| [`bankmap`](flux/applications/bankmap/) | a conflict-free memory-bank mapping, or a proof none exists | no with `--steps 2` |
| [`interconnect_mapping`](flux/applications/interconnect_mapping/) | a memory bank hash and interconnect, chosen together | no |

## Your own problem

```bash
flux new myproblem --kind rtl        # or python, sweep, rtl-sweep, tune
flux task check myproblem
flux task run myproblem --passes 1
```

Edit the statement, the contract and the golden model (or `check.py`) to make it yours. Prefer a
form? The [Loop crafter](https://choelzl.github.io/flux/guide/loop-crafter/): fill in a form, get
a `problem.yaml`. Step by step: [docs/tutorial.md](docs/tutorial.md). Every key:
[author reference](flux/core/loop/src/flux_loop/author_reference.md).

## Long runs

A run goes on until you stop it (Ctrl-C, `q` in `--tui`, `flux stop <record>`) or `--passes N`.
Run it again and it resumes; nothing is measured twice.

```bash
flux run -- flux task run <doc>     # start detached
flux status <db>                    # running? how many passes
flux attach <db>                    # follow the log
flux stop <db>                      # stop at the end of the pass
flux report <db>                    # an HTML report of the search
flux log <db>                       # every model and agent turn
```

Scripts: `flux task run <doc> --json answer.json` writes the answer as JSON
([docs/agent-surface.md](docs/agent-surface.md)). Coding agents: copy [`skills/flux/`](skills/flux/SKILL.md)
where your agent looks for skills.

## Safety

A document is a program: its gate, stages and generator commands run as you. Read a document
from someone else before running it. Coding agents run shell commands with the permissions
their configuration gives them. Run untrusted documents and agents in a container or VM, and
keep API keys in the environment, never in a document.

## Documentation

- [docs/tutorial.md](docs/tutorial.md): a problem of your own, step by step.
- [docs/usage-guide.md](docs/usage-guide.md): every command and option.
- [docs/cookbook.md](docs/cookbook.md): which recipe for which problem.
- [docs/extending.md](docs/extending.md): your own checker, search policy or search command.
- [docs/models.md](docs/models.md): models and coding agents.
- [docs/architecture.md](docs/architecture.md), [docs/glossary.md](docs/glossary.md),
  [docs/decisions.md](docs/decisions.md): how it is built, the words, the design decisions.
- [flux/README.md](flux/README.md): the code layout and the tests;
  [CONTRIBUTING.md](CONTRIBUTING.md): how to contribute.

## Status and license

Research software under active development; interfaces and the record format still change. Tests:
`nix develop --command python3 -m pytest -q tests/unit` from `flux/`. License: to be decided.
