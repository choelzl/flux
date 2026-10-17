# Install

Flux is a Python package. The command line needs Python 3.11 or newer; the web app, the
hardware tools and the sandbox are optional extras.

## Command line

```bash
git clone https://github.com/choelzl/flux.git flux-repo
cd flux-repo
python3 -m venv .venv
.venv/bin/pip install -e ./flux
source .venv/bin/activate
flux selftest --no-model
```

`flux selftest` checks this machine: a script-driven loop, the tools on PATH, and, without
`--no-model`, the model endpoint and a coding agent. It prints PASS, FAIL or SKIP per check.

## Sandbox

`flux task check`, `flux task run` and `flux ask` run inside a container, so a loop's code and
its agents cannot touch the rest of your machine. Install
[Podman](https://podman.io/docs/installation) (rootless, preferred) or
[Docker](https://docs.docker.com/engine/install/). Without either, these commands stop with
`the sandbox needs Podman or Docker`.

For code you trust, such as the [first loop](../guide/tutorial.md), run on your machine
directly:

```bash
export FLUX_SANDBOX=0        # or --no-sandbox on each command
```

What the sandbox sees, writes and reaches is in [Run, stop, resume](../guide/run.md#sandbox).

## Web app

```bash
pip install -e "./flux[web]"
flux user add me --admin
flux serve
```

Run `flux user add` as the user who should own the loops' files. `flux serve` prints its
address (by default `http://127.0.0.1:8765/`); open it and sign in. The
[Web app](../web/index.md) pages walk through it, and [Server and admin](../web/admin.md)
covers running it for a team.

## Hardware tools

Loops that synthesise, place or simulate hardware need Verilator, Yosys, OpenROAD and the
other packaged tools. They come with Flux's Nix development shell. Install
[Nix](https://nixos.org/download/) and enable `experimental-features = nix-command flakes` in
`~/.config/nix/nix.conf`, then:

```bash
cd flux
nix develop --accept-flake-config
flux selftest --no-model
```

The first entry may build or download tools. The shell puts Flux itself on PATH; no `pip
install` is needed inside it. Keep your loop folders wherever you like and pass their paths to
Flux.

## Models and agents

A loop whose designs come from a script needs no model. For a model or a coding agent, see
[Models and agents](../guide/models.md).

## Next

- [First loop](../guide/tutorial.md): a complete loop from an empty folder, in Python only.
- [Loop crafter](../guide/loop-crafter.md): build the document in your browser.
- [Write a loop with an agent](ask.md): describe what you want and let an agent write the loop.
