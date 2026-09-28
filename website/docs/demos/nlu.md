# NLU: an FP16 non-linear unit

**The problem.** One hardware unit that computes seven functions on 16-bit floating-point
numbers (FP16): `exp`, `log`, `sigmoid`, `tanh`, `gelu`, `recip`, `rsqrt`. Every answer must be
within 1 ULP (one step of the last digit) of the exact result, on all 65,536 inputs. Among the
designs that pass, the goal is 800 MHz on ASAP7 with the least area and power.

## Run it

```bash
flux task check applications/nlu/nlu.problem.yaml
flux task run applications/nlu/nlu.problem.yaml --tui
flux task run applications/nlu/nlu.problem.yaml --screen-only    # stop at synthesis
```

It runs until you stop it. To continue the recorded demo campaign instead of starting a new one,
add `--db applications/nlu/demo-nlu.db`.

## What the document says

| key | value |
|---|---|
| `parts` | the seven functions, each designed separately, then joined under one selector |
| `params` | `ulp_budget: 1`, `clock_period_ps: 1250`, `test_rounds: 1` |
| `objectives` | `fmax_mhz` at least 800, then least `area_um2`, then least `power_w` |
| `stages` | `screen` (Yosys), `confirm` (OpenROAD placement), `route` (OpenROAD routing) |
| `ladder` | each function alone is placed; only the whole unit is routed |
| `knowledge` | `knowledge/nlu-methods.md`, a method sheet the model reads |
| `world` | `flux_nlu.world:World`: the FP16 reference, the exhaustive gate, the RTL translator |

## How a design is made

1. The model writes each function as a Python prototype; it is checked on all 65,536 inputs.
2. Flux translates the passing prototype into SystemVerilog.
3. Verilator checks the RTL on every input (the gate). A failing design goes back with its worst
   inputs.
4. Yosys screens the survivors; OpenROAD places the best; the whole unit is routed.

The model also chooses the method per function (table, polynomial, Newton-Raphson, CORDIC, ...),
whether functions share hardware, and how deep to pipeline.

## Result recorded

Routed: **692 MHz at 5,592 um2** (788 MHz placed). Routing costs about 12% of the clock.

## What it needs

- An AI model (it designs the functions).
- The tool shell: Verilator, Yosys, OpenROAD.
- Time: hours. Routing the whole unit alone takes over an hour.

To ask something else (other functions, a 2-ULP budget, a 1 GHz clock), copy the document,
change `parts`, `params.ulp_budget` or the `fmax_mhz` goal, and give it a new `id`.
