# Run a loop

Everything enters through `nix develop` from the `flux/` directory of a
[checkout](https://github.com/choelzl/flux). The first entry builds or downloads the
toolchain (Verilator, Yosys, OpenROAD, ChampSim); accept the flake's binary cache so the heavy
tools are downloaded rather than compiled:

```bash
git clone https://github.com/choelzl/flux && cd flux/flux
nix develop --accept-flake-config          # once: the binary cache; later entries take seconds
```

## The first run needs no model

```bash
nix develop --command flux task run applications/adder16/adder16.problem.yaml --screen-only
```

That sweeps twelve 16-bit adders (four architectures, three block sizes), proves each against
a golden model in Verilator, synthesises each with Yosys + OpenSTA and prints a decision-first
report. It takes a few minutes and calls no model. Drop `--screen-only` to also place the
finalists with OpenROAD. `applications/adder16/` is also the smallest example of a problem of
your own: a document, a generator script and a golden model.

## Every loop is a document

```bash
nix develop --command flux task check applications/<app>/<app>.problem.yaml   # answerable? tools present?
nix develop --command flux task run   applications/<app>/<app>.problem.yaml   # run it
```

| document | model? | what a run costs |
|---|---|---|
| `adder16/adder16.problem.yaml` | no | minutes |
| `bankmap/bankmap.problem.yaml` | no with `--steps 2`; its later rounds use one | seconds |
| `interconnect_mapping/interconnect_mapping.problem.yaml` | no (`params.llm_rounds: 0`) | about two minutes with `--screen-only` |
| `macarray/macarray.problem.yaml` | no with `--steps 1`; the invention rounds use one | minutes per pass; the document runs until `flux stop` (`--passes 1` for one pass) |
| `mul8/mul8.problem.yaml` | yes: a model writes the multiplier | depends on the model |
| `npu_gemm/npu_gemm.problem.yaml` | no | about 30 seconds |
| `gelu_fp16/gelu_fp16.problem.yaml` | yes: a coding agent writes the prototype, the loop spells the RTL | hours to days with a 35B model |
| `primes/primes.problem.yaml` | yes: a model writes and speeds up a Python function | seconds per pass |
| `nlu/nlu.problem.yaml` | yes: a model designs the operators | hours (routing the whole unit alone is over an hour) |
| `prefetcher/prefetcher.problem.yaml` | no for the search; `refine` uses one | tens of minutes; needs three traces that are not in git |
| `prefetcher/invent.problem.yaml` | yes: a model writes a C++ prefetcher | tens of minutes per design; the same traces |

## Or start from a prompt

`flux ask` needs no document of yours: it takes what you want in words (and optionally files:
a spec, reference code, a PDF, tests), has a model or a coding agent write the problem
document and the files it names under `./out/ask-<slug>/`, checks it, runs it and revises it
from the report. It needs a model.

```bash
nix develop --command flux ask "a signed 8x8 multiplier, the smallest that makes 1 GHz placed" --file ref.sv
nix develop --command flux ask --tui        # a setup screen: prompt, files, author, passes
```

## Things worth knowing before running one

- **The knobs are in the document.** Another ask -- other operators, a 1 GHz clock, 16
  lanes, other strides -- is a copy of the document with its `params:` changed and its own
  `id:`; there are no per-application flags. `flux task run`'s own flags are
  the loop's: `--db`, `--out`, `--steps`, `--passes`, `--screen-only`, `--tui`, `--think`,
  `--agent`, `--role`, `--skill`, `--json`, `--replies`, `--model` (`flux task run --help` lists
  them all). `--json FILE` writes the answer for a script.
- **Choosing a model.** By default a local Ollama (`FLUX_LLM_MODEL` names the tag). Setting
  `FLUX_REMOTE_BASE_URL` (with `FLUX_REMOTE_MODEL` and `FLUX_REMOTE_API_KEY`) points every model
  role at an OpenAI-compatible server instead (LocalAI, llama.cpp, vLLM, OpenRouter). The first
  prompt that leaves the machine is announced. A role whose model is unreachable reports itself
  skipped, and the rule-based halves run anyway.
- **Real tools.** NLU, macarray, adder16 and mul8 measure real silicon (Yosys + OpenROAD on
  ASAP7, Verilator for correctness); a stage whose tool is absent is skipped and the report
  says so.
- **prefetcher** needs three 5G traces (~380 MB, not in git); the ChampSim simulator itself
  comes from the flake.
- **The record.** By default each run writes `applications/<app>/out/<id>.db` (and the chosen
  design beside it); `--db PATH` picks another. A relaunch resumes from the record and nothing
  measured is paid for twice. `flux report <db>` writes the campaign report (the fronts per
  pass, the best so far, the parts' ledger); `flux status`, `flux stop` and `flux attach`
  operate a long campaign.
- **The TUI** (`--tui`) shows the roles, the current turn with its prompt, the timing tree and
  the results tab with the front; `f` types operator guidance into the next prompt.

## What a report looks like

A DECISION block first (the thing to build, every number from the measurement stage the report
names), then the front across the objectives, then WHAT THIS RUN ESTABLISHED / NOT
ESTABLISHED / REFUSED with reasons, then the cost. If a run proves the request impossible, the
proof and the nearest feasible answers *are* the report.
