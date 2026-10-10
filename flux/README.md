# flux

The Flux tool itself: the loop, the tools it calls, the applications and the command line.
This page is for working on Flux. To use it, start at the [repository README](../README.md);
the words are defined in [the glossary](../docs/glossary.md), the loop's design in
[architecture.md](../docs/architecture.md), and the reason behind each choice in
[the design log](../docs/decisions.md).

| to | read |
|---|---|
| run a problem, start your own, run long campaigns | [the repository README](../README.md) |
| pick a recipe: tuning, sweeps, models writing designs | [cookbook.md](../docs/cookbook.md) |
| every document key | [author_reference.md](core/loop/src/flux_loop/author_reference.md) |
| a policy, a world, a role of your own | [extending.md](../docs/extending.md) |
| models, servers and coding agents | [models.md](../docs/models.md) |
| drive Flux from a script or an agent | [agent-surface.md](../docs/agent-surface.md) |
| every command and option | [usage-guide.md](../docs/usage-guide.md) |

## Layout

The tree follows the kinds of module the architecture is built from:

| directory | what lives there |
|---|---|
| `core/` | the loop (`core/loop`), the front arithmetic, the IR, the stores, the LLM layer, profiling, the TUI |
| `mentor/` | what guides the rest: the document corpus and its retrieval, mined facts, records read back, operator feedback |
| `applications/` | one folder per design problem: its document, its world package if it has one, its README |
| `interfaces/` | how it is driven: the CLI |
| `tests/` | the unit suite (core + heavy), the integration suite |

`applications/` is the part that grows. Nine today; copy one with its local commands or use `flux new` for a blank loop:

| application | the problem | the chain | the model's roles |
|---|---|---|---|
| [`adder16/`](applications/adder16/) | the smallest 16-bit adder that makes 2.9 GHz placed, from six architectures a script writes | `python rtl.py test` against `golden.py`, Yosys screen, OpenROAD placement | none: a design-space sweep |
| [`mul8/`](applications/mul8/) | the smallest signed 8x8 multiplier that makes 1.6 GHz placed | `python rtl.py test` against `golden.py`, Yosys screen, OpenROAD placement | writes and repairs the RTL |
| [`gelu_fp16/`](applications/gelu_fp16/) | an FP16 GELU within 1 ULP on every input, as a formula | `python -m flux_loop.golden_proto` on the Python prototype, the loop's spelling to SystemVerilog, Yosys screen, OpenROAD placement | a coding agent writes the prototype |
| [`primes/`](applications/primes/) | not hardware: the fastest Python `count_primes(n)` | `check.py` against a reference, `bench.py` timing | writes it, then makes it faster pass after pass |
| [`nlu/`](applications/nlu/) | an FP16 non-linear unit of seven functions, each within 1 ULP on all 65536 inputs, under one mux at 800 MHz routed, with the least area and power | a Python prototype proven on every input, translated to SystemVerilog, Yosys + OpenSTA screen, OpenROAD placement, full place-and-route for the whole | writes and repairs the prototype, invents algorithms, orchestrates with tools |
| [`macarray/`](applications/macarray/) | the multiply-accumulate element's microarchitecture at a workload's precision | Verilator on golden vectors, Yosys + OpenSTA screen, OpenROAD placement along the fmax-vs-area front | invents multiplier structures beyond the four built in |
| [`prefetcher/`](applications/prefetcher/) | tune, compose and invent ChampSim L2 prefetchers for three 5G traces, with no package of its own | `bingo.py check`, a short ChampSim screen, a long confirmation, along the speedup-vs-storage front | refines Bingo's knobs once; a second document has it write a new prefetcher in C++ |
| [`bankmap/`](applications/bankmap/) | a conflict-free bank mapping for given strides through a given interconnect | a pigeonhole or SAT-colouring proof, z3 over XOR folds, an exhaustive checker | proposes mappings past the solver's reach |
| [`interconnect_mapping/`](applications/interconnect_mapping/) | a banked L1's address hash and interconnect against tensor tiles: two small loops (hash per interconnect, interconnect fit per hash) under a big one | an exact GF(2) injectivity gate, a cycle model over train and holdout traffic, a four-way front | proposes hashes |

An application's measuring commands and the domain library it evaluates live with that
application (ChampSim in `prefetcher/tools/`, `rtl.py` in each RTL application); nothing
registers them by name. `mentor/knowledge/corpus/` holds documents to
retrieve from.

This repository builds no third-party tool itself. Verilator,
Yosys, OpenROAD and ChampSim come from the `nixchip` flake input; adding a tool means adding
it to nixchip or to `flake.nix` from nixpkgs.

## Where the shared pieces live

Everything a world needs already exists as a package; building one is mostly wiring:

| you need | use | from |
|---|---|---|
| the loop: parts, a prototype stage, the chain, the ladder, the record, the roles | `flux_loop.run_loop` over a problem document | `core/loop` |
| a measurement cache that survives resumes and is dropped when a tool changes | `MeasurementCache`, keyed by tool fingerprints; `cache:` in the document | `core/loop` |
| a queryable record of every trial, readable back | `CampaignStore` through `flux_records.Records` | `core/stores`, `mentor/records` |
| the front, the knee, the cheapest point meeting a goal, the hypervolume | `flux_frontier` | `core/frontier` |
| one model call, schema-constrained or with tools | `flux_llm.OpenAIChatProposer` -> `Reply` | `core/llm` |
| the mentor's sources: a sheet, a library, the record read back, mined facts | `flux_knowledge.Mentor` + `Corpus`/`Library`/`RecordReadback`/`Mined` | `mentor/knowledge`, `mentor/records` |
| operator guidance typed while the loop runs | `FeedbackChannel` | `mentor/feedback` |
| Verilator checks of generated RTL against golden vectors, Yosys + OpenROAD on ASAP7 | `rtl.py`, one file in the application (ASAP7 from OpenROAD-flow-scripts) | bundled RTL applications |
| a measurement as a record, one tool launch | `Result`, `run_tool` | `core/stores`, `core/loop` |
| tool fingerprints for cache keys and provenance | `toolchain_fingerprint` | `core/loop` |

## The packages

One row per installable package; the authoritative list is `flake.nix`'s `localSrcDirs`.

| package | path | what it is |
|---|---|---|
| `flux-loop` | `core/loop/` | the loop: the document loader, the roles, the prototype stage and its language (`pyint`), the ladder, the chain, calibration between stages, the record and its reload, the report, the author behind `flux ask`, skills, `status/stop/attach` |
| `flux-frontier` | `core/frontier/` | Pareto front, knee, cheapest-meeting, hypervolume, Pareto-UCT |
| `flux-store` | `core/stores/` | `CampaignStore` (the record), `ResultStore` (content-addressed documents and the trials' results) |
| `flux-llm` | `core/llm/` | the proposer protocol, the OpenAI-compatible client, tool calls inside a turn, the text-call parser |
| `flux-profile` | `core/profile/` | the timing tree every phase reports into; the roles' colours |
| `flux-tui` | `core/tui/` | the curses screens: the `flux ask` setup screen, roles, the current turn, timing, results with the front, feedback |
| `flux-knowledge` | `mentor/knowledge/` | the corpus, the BM25 library, the `Mentor` bundle of sources |
| `flux-records`, `flux-feedback` | `mentor/` | the record's meaning over the store, with laws extracted from it and facts mined from it; the operator channel |
| `flux-nlu`, `flux-macarray`, `flux-bankmap`, `flux-imapping` | `applications/` | the four world packages (the other five applications have none) |
| `flux-cli` | `interfaces/cli/` | the `flux` command |

## Development setup

`nix develop` gives a working environment with every tool (`pip install -e ./flux` gives the
loop and the CLI alone; see the repository README):

```sh
cd flux
nix develop --command python3 tests/check.py                     # every check at once: ruff, unit, heavy, e2e (~2.5 min, D822)
nix develop --command python3 -m pytest -q tests/unit            # the core suite
nix develop --command python3 -m pytest -q tests/unit -m heavy   # the slow, tool-backed tests (Yosys, OpenROAD, Verilator, z3, ChampSim)
nix develop --command flux --help
```

`pytest.ini` leaves out the `heavy` tests by default; the heavy files are listed in
`tests/unit/conftest.py`. CI runs both. `tests/integration/` needs whichever real tool
each test targets (Verilator, Yosys and OpenROAD for `rtl.py`, a model for the prose
checks); run the files relevant to what you change. Every test gets its own trace root, so a
test run never disturbs a live campaign.

`flake.nix` builds the third-party Python dependencies as Nix derivations. The local `flux-*` packages are not built: the
shell's `PYTHONPATH` points at each package's `src/`, an editable install without a virtual
environment.

`nix develop` is one shell, the one `flux serve` runs every task from: the Python environment,
Verilator, Yosys with the slang front end, Icarus, ChampSim, SystemC and, on linux, OpenROAD
and ICSC; nixchip's hook exports `<TOOL>_HOME`, `_BIN`, `_LIB` and
`_INCLUDE` for each nixchip tool in it. The loop writes scratch files under `FLUX_TMPDIR` and traces (prompts, replies,
checked prototypes) under `FLUX_TRACE_ROOT` (default `$TMPDIR/flux-traces`), one directory
per campaign.

### Run the web server as a Linux user service

The service starts `flux serve` through a fresh `nix develop` on every launch, including
crash recovery and update restarts. Create an account first in the same data directory:

```bash
cd flux
nix develop --accept-flake-config --command flux user add admin --admin
python3 scripts/install-service.py -- --host 127.0.0.1 --port 8765
systemctl --user daemon-reload
systemctl --user enable --now flux.service flux-update.path
journalctl --user -u flux.service -f
```

Pass additional `flux serve` options after `--`, including `--data` if needed (use the
same `--data` when creating the account). Optional service environment settings go in
`~/.config/flux/service.env`, one `NAME=value` per line. The service does not inherit your
interactive shell; give agent executables absolute paths through `FLUX_<AGENT>_BIN` or
set `PATH` there. Nix refreshes the library and Python environment itself.

The update watcher restarts a running server after Git commits, pulls, and checkouts,
or edits to `flake.nix` / `flake.lock`. Ordinary uncommitted Python edits require
`systemctl --user restart flux.service`. The watcher does not fetch updates, change the
lock file, or restart a manually stopped server. Updating Nix dependencies can take time
to build or download; follow the journal for progress. Server restarts disconnect live
requests, but running loops keep their own sessions and the restarted server finds them again.
The service stops only its main process (`KillMode=process`), so an update does not send
SIGTERM to their Podman/crun containers. Agent logins and connection Tests from the old
server are cleaned up separately.

If you installed an earlier version, re-run the installer with your existing server arguments
and run `systemctl --user daemon-reload` before the next restart to apply this stop policy.

For startup at boot and operation after logout, enable user lingering once with
`loginctl enable-linger "$USER"` (your system may require administrator authorization).
To stop both the server and automatic restarts, run
`systemctl --user disable --now flux-update.path flux.service`.
Re-run the installer and `systemctl --user daemon-reload` after moving the checkout or
changing server arguments, then restart the service.
