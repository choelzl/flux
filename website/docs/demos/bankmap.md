# Bank mapping

**Given a set of access strides and a concurrency N, find an address-to-bank mapping such that
for every start address, N accesses at any of those strides land in N distinct banks -- and
say precisely what is achievable when that is impossible.**

```bash
nix develop --command python3 applications/bankmap/demo.py --strides 1 8 16 --concurrent 4 --banks 8
```

## The ladder

| rung | what | cost | what it can say |
|---|---|---|---|
| baseline | `bank = addr mod B` | µs | the strides that collapse it |
| pigeonhole | a clique of B+1 addresses that must all differ in bank | µs | **impossible for any mapping** |
| z3 | the XOR-fold family, searched exactly (CEGIS with the checker as oracle) | ms-s | the cheapest conflict-free fold, or **no fold exists** |
| feasible | z3 again, descending N and growing the stride set | s | what the request's strides *do* admit |
| model | non-linear expressions a local model proposes, told what failed | minutes | a family the solver cannot express |

Every rung is judged by one **exhaustive checker**: every start address in the space,
vectorised in numpy, a few milliseconds per stride. A sample is not a guarantee, and the kernel
does not get to choose where its arrays are placed.

## The interconnect is part of the request

A staged crossbar does not deliver a request straight to its bank: two accesses bound for
different banks can still collide at an earlier stage. `--topology` names the network --
crossbar, staged tree, omega, butterfly, Clos, Benes -- and each reduces to *stages* (which
bank-index bits identify a resource, and a capacity) that the checker, z3 and the pigeonhole
argument all understand. When the wiring is yours to choose, the solver chooses it jointly with
the mapping and reports both.

## What runs establish

Proofs close doors fast: strides {1, 8, 16, 17} at N = B = 8 has no solution for *any* mapping
-- nine addresses that must occupy eight banks, proved in under two seconds, so the model is
never asked. Feasible cases solve instantly: strides {1, 8, 16} at N = 4 admit a two-XOR-gate
fold found in one solver round. And the gap between what the linear family reaches and what
the pigeonhole bound allows is exactly where the model round is worth its cost -- proposals
are refused by the exhaustive checker or kept, and a near miss is reported as "not found",
never as impossible, because nothing proved it so.

The mapping comes back with its Verilog: a conflict-free fold is a few XOR gates on each
port's address path.
