# Starter examples

Three small, complete problems. Copy one to start your own.

| folder | the problem | AI model? | files |
|---|---|---|---|
| `adder16` | the smallest 16-bit adder that makes 3000 MHz placed, from 4 architectures | no | document, `gen.py` (writes each adder), `golden.py` |
| `mul8` | a signed 8x8 multiplier built from partial products, 1000 MHz placed | yes | document, `golden.py` |
| `primes` | the fastest Python `count_primes(n)` (not hardware) | yes | document, `check.py`, `bench.py` |

## adder16: a sweep, no model

```bash
flux task run applications/adder16/adder16.problem.yaml --screen-only --passes 1
```

- `space`: `arch` (behavioral, ripple, carry_select, kogge_stone) x `block` (2, 4, 8): 12 designs.
- `flow`: `dse: sweep`, `generate: {command: gen.py ...}`.
- `gate`: `flux rtl test` against `golden.py` (Verilator).
- `stages`: `screen` (Yosys synthesis), `confirm` (OpenROAD placement, dropped by `--screen-only`).
- About three minutes. The chosen adder goes to `applications/adder16/out/adder16.v`.

## mul8: a model writes the RTL

```bash
flux task run applications/mul8/mul8.problem.yaml --tui
```

- `statement` and `contract`: the prompt (ports, Verilog-2001 only, no `a * w`).
- `gate`: `flux rtl test` against `golden.py`; a failing design goes back with the failing
  inputs, up to `repair_attempts: 6` times.
- `flow: {generate: {agent: opencode}}` (or `claude`, `codex`) lets a coding agent write it.

## primes: not hardware

```bash
flux task run applications/primes/primes.problem.yaml --passes 3
```

- `gate`: `check.py` refuses a wrong answer. `stages`: `bench.py` prints `time_ms`.
- Each pass asks for something faster.
- `flux new NAME --kind python` writes a problem of this shape.

All three need the tool shell except `primes`, which needs only Python and a model.
