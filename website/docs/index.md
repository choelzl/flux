# Flux

**Flux searches for the best hardware design for a problem you describe: a model or a script
proposes designs, real tools check and measure them, and Flux picks the winner and tells you why.**

You describe the problem in one short file, the **document** (`*.problem.yaml`): what to build,
how to tell a right design from a wrong one, what to measure and what "better" means. Flux does
the rest and keeps a record of every design it tried.

## Get started

=== "Quick try (Python only)"

    Needs Python 3.11 or newer. No hardware tools, no AI model.

    1. Get the code and install it:

        ```bash
        git clone https://github.com/choelzl/flux.git flux-repo
        cd flux-repo
        python3 -m venv .venv && .venv/bin/pip install -e ./flux
        ```

    2. Write a ready-to-run problem:

        ```bash
        .venv/bin/flux new primes --kind sweep
        ```

    3. Run it:

        ```bash
        .venv/bin/flux task run primes/primes.problem.yaml --passes 1
        ```

    Flux times six ways of counting primes and prints the fastest (`odd_sieve`, about 6 ms here).

=== "Full install (hardware tools)"

    Needs [Nix](https://nixos.org/download) with flakes on
    (`experimental-features = nix-command flakes` in `~/.config/nix/nix.conf`). Nix brings
    Verilator, Yosys, OpenROAD and ChampSim.

    1. Get the code and enter the tool shell (the first time downloads the tools):

        ```bash
        git clone https://github.com/choelzl/flux.git flux-repo
        cd flux-repo/flux
        nix develop --accept-flake-config
        ```

    2. Check that everything works (each line says PASS, FAIL or SKIP, with the reason):

        ```bash
        flux selftest --no-model
        ```

    3. Run a first hardware search, no AI model needed (about three minutes):

        ```bash
        flux task run applications/adder16/adder16.problem.yaml --screen-only --passes 1
        ```

    Flux builds twelve 16-bit adders, proves each one correct, synthesises them and prints the
    one to build.

Next: [run the applications](demos/index.md), follow the [tutorial](guide/tutorial.md), or
[build your own problem](guide/build-your-own.md).

## What it can do

- **RTL from a golden model.** You give a Python function that computes the right answer; a
  model writes the Verilog; Flux tests every design against your function in Verilator.
- **Prototypes first.** For numeric designs the model first writes the algorithm in Python or
  SystemC, checked on every input in seconds; Flux then turns it into RTL itself (SystemC through
  the ICSC translator).
- **Design-space sweeps and searches.** List the knobs; pick a search: `sweep`, `montecarlo`,
  `gradient`, `anneal`, `genetic`, `pareto`, `llm` (a model picks the points) or a coding agent.
- **Coding agents in any box.** Claude Code, Codex or OpenCode can write the designs or answer
  any box of the loop that does not establish facts.
- **Real measurements.** Yosys and OpenROAD on the ASAP7 process for speed, area and power;
  ChampSim for cache prefetcher studies; ZigZag for accelerator sizing; any command of yours.
- **Calibration.** Cheap stages are compared with costly ones, so a quick estimate is never
  reported as a measured result.
- **An honest report.** The design to build first, then the trade-offs, what was measured and
  what was only modelled, and every design that was refused, with the reason.
- **From a sentence.** `flux ask "what you want"` writes the document for you.

## The loop

Every problem runs through the same loop. Each **box** does one job and can be filled by
*rules* (plain code), a *model* (an AI language model) or a *coding agent*. Boxes that establish
facts are never handed to an AI.

| box | what it does | who can fill it |
|---|---|---|
| validate | checks the document before anything runs | rules, model, coding agent |
| orchestrate | picks the next piece of work | rules, model, coding agent |
| plan | plans each pass: parts, order, method, budget | none, model, coding agent |
| dse | searches the knobs | a search policy, model, coding agent |
| generate | writes each design | model, script, fixed list, coding agent |
| test | the **gate**: refuses any wrong design | rules only, never delegated |
| critique | challenges the parts and the decision | none, model, coding agent |
| analytical | cheap estimates: formulas, cost models | rules or a learned estimate, never delegated |
| simulation | real tools: Verilator, Yosys, OpenROAD, ChampSim | tools only, never delegated |
| calibrate | compares cheap stages with costly ones | on or off, never delegated |
| select | picks the winner from the objectives | objectives; a coding agent may break ties |
| feedback | your notes, typed during a run | you, or none |
| knowledge | what the model reads: notes, papers | files, or a model's digest |
| extract | lessons mined from past runs | none, mined, coding agent |
| records | keeps every design, number and refusal | always on, never delegated |

[More on the loop](guide/loop-shape.md).

## Applications

| application | what it finds |
|---|---|
| [NLU](demos/nlu.md) | an FP16 unit for seven math functions, each within 1 ULP, at 800 MHz |
| [MAC array](demos/macarray.md) | the smallest multiply-accumulate element that makes 1 GHz |
| [Prefetcher](demos/prefetcher.md) | the best cache prefetcher configuration, or a new prefetcher, in ChampSim |
| [Bank mapping](demos/bankmap.md) | a conflict-free memory-bank mapping, or a proof none exists |
| [Interconnect mapping](demos/interconnect_mapping.md) | memory bank hash and interconnect, chosen together |
| [GELU FP16](demos/gelu_fp16.md) | an FP16 GELU within 1 ULP, invented as a formula by a coding agent |
| [NPU GEMM](demos/npu_gemm.md) | the smallest accelerator that runs a workload in 500 cycles |
| [Starter examples](demos/starters.md) | `adder16`, `mul8`, `primes`: the smallest complete problems |

## Loop crafter

Prefer a form to a text file? The [Loop crafter](guide/loop-crafter.md): fill in a form, get a
`problem.yaml`.
