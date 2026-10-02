# adder16/ -- a design-space sweep with no model and no Python package

An unsigned 16-bit adder (module `adder16`, inputs `a` and `b`, 17-bit sum `s`): the smallest
one that makes 3000 MHz placed on ASAP7, chosen from four architectures -- behavioral (`a + b`,
left to the synthesis tool), ripple carry, carry-select (block size 2, 4 or 8) and Kogge-Stone.
It is the smallest complete example of a design-space exploration in Flux, and the right first
run: it needs no model.

## The files

| file | what it is |
|---|---|
| `adder16.problem.yaml` | the problem document: the statement, the `flow.orchestrate.space` (`arch` x `block`, 12 points), the generator command, the gate, the two stages, the objectives, the budget |
| `gen.py` | the generator: `gen.py <out> <arch> <block>` writes one adder as Verilog |
| `golden.py` | the golden model: `PORTS` and `golden(a, b)`, what the adder must compute |

For every point of the space the loop runs `gen.py` with that point's knobs, proves the result
against `golden.py` in Verilator (`flux rtl test`), synthesises it with Yosys + OpenSTA
(`flux rtl measure --stage synth`, the screen), and places the two finalists with OpenROAD
(`--stage place`, the numbers the report quotes).

## Run it

From `flux/`:

```bash
nix develop --command flux task check applications/adder16/adder16.problem.yaml   # what it will do, tools present?
nix develop --command flux task run applications/adder16/adder16.problem.yaml --screen-only   # synthesis only, a few minutes
nix develop --command flux task run applications/adder16/adder16.problem.yaml   # plus placement of the finalists
```

The report opens with the DECISION (the design to build and its numbers, and which stage they
come from), then the frontier, then what the run established and what it did not. With
`--screen-only` every number is from synthesis, and the report says so. The record goes to
`applications/adder16/out/adder16.db` and the chosen adder to `out/adder16.v`; a second run
resumes from the record and re-measures nothing.

## Change it

- **Another architecture:** add a function to `gen.py` that returns the body lines, add it to
  the table at the bottom of `gen.py`, and add its name to `space.arch` in the document.
- **Another target:** change the `goal` of the `fmax_mhz` objective, and `--clock-ps` in the two
  stage commands to match.
- **Another search:** `flow: {orchestrate: sweep}` tries every point; `montecarlo`, `anneal`,
  `gradient`, `genetic` or `llm` (a model names the next points) are one word each.
- **Another width:** change `N` in `gen.py`, the port widths in `golden.py`, and the statement.

For a copy that keeps its own record, copy the folder: its name is the id.
