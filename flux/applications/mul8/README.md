# mul8/ -- an RTL problem with no code of its own

A combinational signed 8x8 -> 16-bit multiplier in SystemVerilog (module `mul8`, inputs `a`
and `w`, output `p`): the smallest one that makes 1000 MHz placed on ASAP7, built from partial
products (Booth recoding, a compression tree, a Baugh-Wooley array) rather than `a * w`. A
model writes the RTL; Flux checks it and measures it. It is the smallest complete example of
a problem where the design is written, not enumerated.

## The files

| file | what it is |
|---|---|
| `mul8.problem.yaml` | the problem document: the statement and the contract (together, the prompt), the gate, the two stages, the objectives, the budget |
| `golden.py` | the golden model: `PORTS` and `golden(a, w)`, what the multiplier must compute |

The gate is `flux rtl test {artifact} --golden {home}/golden.py`: Verilator runs the design on
the corners of every input and on random vectors and compares each output with `golden()`. A
design that fails goes back to the model with the failing vectors, up to
`budget.repair_attempts` times. The stages are `flux rtl measure --stage synth` (Yosys +
OpenSTA, the screen) and `--stage place` (OpenROAD, the numbers the report quotes).

## Run it

It needs a model: the design is written by it. See "Choose a model" in the
[usage guide](../../../docs/usage-guide.md) (a local Ollama by default, or any
OpenAI-compatible server). From `flux/`:

```bash
nix develop --command flux task check applications/mul8/mul8.problem.yaml
nix develop --command flux task run applications/mul8/mul8.problem.yaml --tui
nix develop --command flux task run applications/mul8/mul8.problem.yaml --screen-only   # synthesis only
```

The record goes to `applications/mul8/out/mul8.db` and the chosen design to `out/mul8.sv`.

## Change it

- **Let a coding agent write it:** set `flow: {generate: opencode}` (or `claude`,
  `codex`); the agent uses its own model and tools, and the loop's gate and stages judge what it
  wrote.
- **Another target:** the `goal` of the `fmax_mhz` objective and `--clock-ps` in the two stage
  commands.
- **Another circuit:** a new statement and contract, a `golden.py` with its `PORTS` and
  `golden()`, in a folder of its own (its name is the id). Nothing else changes: that is the whole of an RTL
  problem.
- **More tries:** `budget.steps` (designs per pass) and `budget.repair_attempts`.
