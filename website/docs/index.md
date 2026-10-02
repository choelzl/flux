---
hide:
  - navigation
---

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
        .venv/bin/flux task run primes --passes 6
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
        flux task run applications/adder16 --screen-only --passes 12
        ```

    Flux builds twelve 16-bit adders, proves each one correct, synthesises them and prints the
    one to build.

## Where next

| you want to | read |
|---|---|
| see a whole problem, start to finish | the [tutorial](guide/tutorial.md): a square root circuit in ten minutes |
| write your own problem by hand | [build your own](guide/build-your-own.md): the document, key by key |
| fill in a form instead | the [loop crafter](guide/loop-crafter.md): a form that writes the document |
| run it, steer it, read the result | [run a problem](guide/run.md): the options, the report, the AI model |
| know what happens inside | [the loop](guide/loop-shape.md): every step, and who can do it |

## What it can do

- **RTL from a golden model.** You give a Python function that computes the right answer; a
  model writes the Verilog; Flux tests every design against your function in Verilator.
- **Prototypes first.** For numeric designs the model first writes the algorithm in Python or
  SystemC, checked on every input in seconds; Flux then turns it into RTL itself (SystemC through
  the ICSC translator, in `nix develop .#systemc`).
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

## How it works

Every problem runs through the same loop: a design is proposed, written, checked, measured
stage by stage, and the best one is chosen. Each step can be done by *rules* (plain code), a
*model* (an AI language model) or a *coding agent*; you choose in the document, and the defaults
are usually right. Two steps are never handed to an AI: the checks that say a design is correct,
and the measurements. [The loop](guide/loop-shape.md) draws every step.
