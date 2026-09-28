# Run an application

Each application is a folder in
[`flux/applications/`](https://github.com/choelzl/flux/tree/main/flux/applications) holding one
document. Run the commands from the `flux/` folder, inside the tool shell (`nix develop`).

1. See what a document needs and which tools are missing (runs nothing):

    ```bash
    flux task check applications/adder16/adder16.problem.yaml
    ```

2. Run it:

    ```bash
    flux task run applications/adder16/adder16.problem.yaml --passes 1
    ```

3. Read the report printed at the end, or write an HTML one from the record:

    ```bash
    flux report applications/adder16/out/adder16.db
    ```

## The applications

| document | AI model? | a run takes | needs |
|---|---|---|---|
| [`adder16`](starters.md) | no | minutes | Verilator, Yosys, OpenROAD |
| [`npu_gemm`](npu_gemm.md) | no | about 30 s | ZigZag (`pip install -e "./flux[zigzag]"`) |
| [`bankmap`](bankmap.md) | no with `--steps 2` | seconds | z3 (`pip install -e "./flux[bankmap]"`) |
| [`interconnect_mapping`](interconnect_mapping.md) | no | about 2 min with `--screen-only` | Yosys, OpenROAD for the last stage |
| [`macarray`](macarray.md) | no with `--steps 1` | minutes per pass | Verilator, Yosys, OpenROAD |
| [`primes`](starters.md) | yes | seconds per pass | Python only |
| [`mul8`](starters.md) | yes | depends on the model | Verilator, Yosys, OpenROAD |
| [`nlu`](nlu.md) | yes | hours | Verilator, Yosys, OpenROAD |
| [`gelu_fp16`](gelu_fp16.md) | yes, a coding agent | hours to days | OpenCode, Verilator, Yosys, OpenROAD |
| [`prefetcher`](prefetcher.md) | yes | tens of minutes | ChampSim, three traces not in git |

The tool shell brings every tool above. Outside it, `flux task check` names what is missing and
which stages will be skipped.

## Options you will use

| option | what it does |
|---|---|
| `--passes N` | stop after N passes; without it a run goes on until you stop it |
| `--screen-only` | stop at synthesis (fast, estimate only) |
| `--tui` | a live screen; `f` types a note into the next prompt, `q` quits |
| `--db FILE` | where the record goes (default `out/<id>.db` beside the document) |
| `--json FILE` | also write the answer as JSON, for scripts |

Stop a run with Ctrl-C, `q` in the screen, or `flux stop <record>` from another terminal. Run it
again and it resumes: nothing already measured is measured twice.

## Choosing an AI model

By default Flux uses a local [Ollama](https://ollama.com) and the model named in
`FLUX_LLM_MODEL` (default `qwen3.8:latest`). For any OpenAI-compatible server:

```bash
export FLUX_REMOTE_BASE_URL=http://my-server:8080
export FLUX_REMOTE_MODEL=<model name on that server>
export FLUX_REMOTE_API_KEY=<key>        # only if the server wants one
```

## Changing an application

Every setting is in the document. To ask something else, copy the document, change its
`params:` or `objectives:`, and give the copy its own `id:` so it keeps its own record.
