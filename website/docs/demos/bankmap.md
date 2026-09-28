# Bank mapping

**The problem.** A memory split into banks can serve several accesses at once only if they land
in different banks. Given the access strides and how many accesses happen together (N), find the
cheapest address-to-bank mapping that is conflict-free for **every** start address, or prove that
none exists and say what is achievable instead.

## Run it

```bash
flux task run applications/bankmap/bankmap.problem.yaml --steps 2   # solver only, no model, seconds
flux task run applications/bankmap/bankmap.problem.yaml --tui       # plus model rounds
```

The record and the chosen mapping's Verilog go to `applications/bankmap/out/`.

## What the document says

| key | value |
|---|---|
| `params.strides` | `[1, 8, 16]`: the access strides, in words |
| `params.concurrent` | `4`: accesses issued together (N) |
| `params.banks` | `8` (a power of two) |
| `params.address_bits` | `20`: the address space the guarantee covers |
| `params.topology` | `crossbar`; also `staged:GxH`, `omega`, `butterfly`, `clos:n,m,r`, `benes` |
| `params.llm_round` | `6` model proposals per round; `0` = solver only |
| `objectives` | least `hardware_cost` (XOR gates) |
| `budget.steps` | `4`: baseline, solver, then two model rounds |

## The chain, cheapest first

| stage | what it can say |
|---|---|
| baseline (`addr mod B`) | which strides break it |
| pigeonhole | **impossible for any mapping**, with the addresses that prove it |
| z3 solver | the cheapest XOR mapping, or **no XOR mapping exists** |
| feasible | what the strides do allow (smaller N, fewer strides) |
| model | non-linear mappings the solver cannot express |

One exhaustive checker judges every stage: every start address, every stride.

## Results recorded

- Strides {1, 8, 16} at N = 4: a two-XOR-gate mapping, found in one solver round.
- Strides {1, 8, 16, 17}, 8 accesses at once into 8 banks: **impossible for any mapping** (nine addresses must fit
  in eight banks), proved in under two seconds; the model is never asked.

## What it needs

z3 (in the tool shell, or `pip install -e "./flux[bankmap]"`). A model only for the model rounds;
without one they report themselves skipped.
