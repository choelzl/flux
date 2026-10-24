# Flux

**Flux searches for better designs and keeps only what it can prove.** It makes designs with a
script, a model or a coding agent, checks each one against your tests, measures the ones that
pass with real tools, and decides from your objectives. It is built for hardware and software
design-space exploration: RTL on an open 7 nm flow, Python and C code, cache prefetchers, or any
program with settings to tune.

- **Documentation:** <https://choelzl.github.io/flux/>
- **Live preview of the web app** (sample loops, no server): <https://choelzl.github.io/flux/preview/>
- **Example applications** (their problem documents and READMEs): [`examples/`](examples/)

## What a loop does

1. **Design**: a script, Flux's own agent (FluxAgent) or a coding agent (Claude Code, Codex,
   OpenCode) proposes a design.
2. **Check**: your tests decide whether it is correct; a design that fails never gets measured.
3. **Measure**: the survivors run through real tools, cheapest stage first.
4. **Choose**: your objectives pick the best, on the Pareto front, with every number on record.

The web app follows runs live, compares results, and lets you chat with an agent about a loop.

## The code

This repository holds only the published documentation site (the `gh-pages` branch, built from
the source and pushed automatically). The source is not public: ask its maintainer for access.
