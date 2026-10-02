# macarray/ -- the MAC processing element's microarchitecture, on real ASAP7 numbers

The array is given: `lanes` products summed per cycle at the precision the workload IR
declares (int8 x int8 by default, from `core/ir/workload/examples/mlp-gemm0.yaml`). What is
searched is inside the PE:

| knob | values | what it changes |
|---|---|---|
| multiplier | `behavioral` (the `*` operator), `array` (sign-magnitude shift-and-add), `booth4` (radix-4 Booth), `wallace` (carry-save tree), plus anything a model invented | how each product is formed |
| reducer | `chain`, `tree`, `csa` | how the products (and the accumulator input) become one sum |
| pipeline | 0, 1, 2, 3 | register stages: products; plus the output; plus a cut through the reduction |

48 points (4 x 3 x 4), plus every multiplier `invent.problem.yaml` kept. The study is two
documents and the commands of `flux_macarray.steps`, one per phase (D798) -- no world:

| box | what runs |
|---|---|
| `orchestrate` | a sweep over the space; the multiplier knob also takes every file in `out/invented/` (`from: "out/invented/*.sv"`) |
| `generate` | `steps gen {artifact} {multiplier} {reducer} {pipeline}`: the PE's RTL (`rtl.py`) |
| `test` | `steps check {artifact} {pipeline}`: **Verilator** against golden vectors seeded from the workload, the latency checked against the stages the PE claims |
| `measure` | `flux rtl measure --stage synth` (the **screen**: Yosys + OpenSTA, seconds, optimistic) and `--stage place` (**OpenROAD**, the number a report may quote) |
| `select` | the objectives: at least 1000 MHz placed, then the least area; the design they choose is always placed, the rest of the finalists spread along the fmax-vs-area frontier |

```bash
cd flux
nix develop --command flux task check applications/macarray/problem.yaml
nix develop --command flux task run applications/macarray/problem.yaml --passes 1        # screen and place the space
nix develop --command flux task run applications/macarray/problem.yaml --passes 1 --screen-only
nix develop --command flux task run applications/macarray/invent.problem.yaml --passes 3 # a model invents multipliers
```

`budget.batch: 48` screens the whole space in one pass. The record and the decided PE go to
`applications/macarray/out/` (`macarray.db`, `macarray.sv`; the invention's `macarray.invent.db`);
a resumed run re-measures nothing, because measurements are keyed on the design, the stage's
command and the tool fingerprints.

## Changing the ask

Copy the folder -- its name is the copy's id, so its record and measurements do not mix with
the original's -- and edit the copy's `problem.yaml`: the clock is the stages' `--clock-ps` and
the `fmax_mhz` objective's `goal`; the lanes and the workload are the step commands'
`--lanes N` and `--workload FILE`; the space is `orchestrate.space`.

```bash
cp -r applications/macarray applications/mac16
# edit applications/mac16/problem.yaml: --lanes 16 on gen and check, --clock-ps 667 and goal: 1500
nix develop --command flux task run applications/mac16/problem.yaml --passes 1
```

## What the model does

Nothing in the PE study: a 48-point space is screened exhaustively and needs a judge, not a
proposer. `invent.problem.yaml` asks a model for a **multiplier structure the enumeration does
not contain** -- one combinational module `mult_inv` (`a`, `w` in, `p` out) -- refused before
any tool runs if it is sequential, uses SystemVerilog casts, or is the behavioral `a * w`;
then Verilator against the products at the sign corners (`steps mult-check`), its failures fed
back up to `repair_attempts` times, and the screen of the standard PE built around it (`steps
mult-screen`) as its measurement. Every multiplier that passes is kept in `out/invented/`
(renamed by its content) and joins the PE study's space on its next run.

## Layout

`problem.yaml` is the PE study, `invent.problem.yaml` the invention. `lib/src/flux_macarray/` holds
what the step commands are made of: `steps` (the commands), `config` (the space and the
shape), `rtl` (the generator), `verify` (golden vectors and the Verilator verdict), `invent`
(the multiplier's rules and golden model), `measure` and `objective` (the PE's score).
