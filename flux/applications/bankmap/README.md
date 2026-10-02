# bankmap/ — conflict-free bank-mapping functions

Given a set of access strides and a concurrency N, find an address-to-bank mapping such that
for **every** start address, N accesses at any of those strides land in N distinct banks — and
say precisely what is achievable when that is impossible.

```bash
cd flux
nix develop --command flux task run applications/bankmap --tui       # baseline, proof, z3, then model rounds
```

The study is the document and the commands of `flux_bankmap.steps`, one per phase (D799) -- no
world. The chain below is `orchestrate: {command: "... steps search {history} {state} {params}"}`:
the loop calls it once a round with what was measured and refused so far, and it prints the next
candidates (each a mapping's Verilog with a `// flux_bankmap:` line saying the mapping and the
wiring), lessons, and -- when nothing conflict-free exists -- the best partial answer as the run's
conclusion. The gate is `steps check` (the exhaustive checker), the one stage `steps cost` (the
XOR count). A model round asks the run's own model (`FLUX_REMOTE_*`, a local Ollama), told the
solver's outcome, the counter-examples, what was refused and what the operator typed.

The record and the chosen mapping's Verilog go to `applications/bankmap/out/` (`bankmap.db`,
`bankmap.v`) unless `--db` / `--out` say otherwise. `params.model_rounds: 0` stops after the
solver; a round with no model reachable ends the chain where the solver did.

## Changing the ask

The request is the document's `params:`; there are no per-application flags. Copy
the folder (its name is the copy's id, so its record is its own), edit `params:` in its
`problem.yaml`, and run the copy:

```yaml
params:
  strides: [1, 8, 16, 17]   # access strides, in words
  concurrent: 8             # N accesses issued together; at most `banks`
  banks: 8                  # B, a power of two
  address_bits: 20          # the address space the guarantee must hold over
  z3_seconds: 60            # solver budget per attempt
  max_xor_inputs: null      # hardware bound: address bits folded into one bank bit
  llm_round: 6              # mappings a model may propose per round; 0 = solver only
  model_rounds: 2           # model rounds after the solver
```

(That example is the impossible request below.) The interconnect keys -- `topology`,
`stage_capacities`, `lanes`, `stages` -- are described under "Crossbar stages" below.

## The chain

| stage | what | cost | what it can say |
|---|---|---|---|
| baseline | `bank = addr mod B` | µs | the strides that collapse it |
| pigeonhole | a clique of B+1 addresses that must all differ in bank | µs | **impossible for any mapping** |
| z3 | the XOR-fold family, searched exactly (CEGIS with the checker as oracle) | ms–s | the cheapest conflict-free fold, or **no fold exists** |
| feasible | z3 again, descending N and growing the stride set | s | what the request's strides *do* admit |
| model | non-linear expressions a local model proposes, told what failed | minutes | a family the solver cannot express |

Every stage is judged by one **exhaustive checker** (`check.py`): every start address in the
space, vectorised in numpy, a few milliseconds per stride. A sample is not a guarantee, and the
kernel does not get to choose where its arrays are placed.

## What the first runs established

**Strides {1, 8, 16, 17} at N = B = 8 has no solution, for any mapping.** A stride-1 window needs
a..a+7 in eight distinct banks; stride 8 needs a and a+8 to differ; and a+8 must then differ from
a+1 (difference 7), a+2 (6), … a+7 (1). Nine addresses, eight banks. The first run spent 216 s
proving z3's family insufficient and refusing eight model proposals; the pigeonhole check now
answers in 1.7 s and the model is never asked.

The linear family reaches only **3** concurrent accesses for those strides, while the pigeonhole
bound is **7**. That gap — N in 4..7 — is where a non-linear mapping might exist and where the
model is worth its cost. Not yet searched: the study stops at the proof today.

Feasible cases solve instantly: strides {1, 8, 16} at N=4 → `bank = {a1^a4, a3, a0^a5}`, two XOR
gates, in one z3 round.

## Crossbar stages

A staged crossbar does not deliver a request straight to its bank. Stage 1 routes on some bank-index
bits into a *group*; the links into that group carry only so many requests per cycle; later stages
route inside the group. Two accesses bound for different banks can still collide at stage 1.

A stage is therefore **which bank-index bits identify its resource, and a capacity**. Bank-level
conflict-freeness is the special case `bits=all, capacity=1` and is always checked; stages add to it.

| `params:` | what it describes |
|---|---|
| `topology: staged:4x2` | 8 banks: stage 1 routes on the top 2 bank bits into 4 groups |
| `topology: staged:4x2`, `stage_capacities: [2, 1]` | the same with two parallel links per group |
| `topology: staged:4x8`, `lanes: 4` | 32 banks behind seven 4x4s feeding four 7x8s: each 4x4 sees four consecutive accesses and must spread them over the 7x8s |
| `stages: [{bits: [1, 2], capacity: 1}]` | an explicit sharing point, for topologies GxH cannot describe |
| `stages: [{bits: [3, 4], capacity: 1, lanes: 4}]` | the same with lanes: bits 3 and 4, capacity 1, per chunk of 4 accesses |

### Other interconnects

`params.topology` names the network between requesters and banks (one of the values below); each one reduces to stages the
checker, z3 and the pigeonhole all understand, plus a note on what it assumes:

```yaml
topology: crossbar             # one full switch (the default): only the bank can conflict
topology: staged:4x8           # the tree above (with lanes: 4)
topology: omega                # log2(B) stages of 2x2 switches, self-routed MSB first: after
                               # stage j, sources that agree modulo 2^(n-j) share a link with
                               # banks that agree on the top j bits
topology: butterfly            # the mirror: low bank bits, lanes grouped on their high bits
topology: clos:4,4,8           # r=8 ingress 4x4, m=4 middle, r egress: per-cycle routed,
                               # non-blocking when m >= n; clos:4,2,8 blocks (2 of 4 lanes out)
topology: benes                # a Clos of 2x2s, non-blocking: bank conflicts are the whole story
stages: [{bits: [4], capacity: 1, lanes: 8, lane_key: mod}]   # an explicit stage: bank bit 4,
                               # capacity 1, lanes grouped mod 8
```

The blocking ones (staged, omega, butterfly) assume self-routing, so every internal link is a
conflict point. The non-blocking ones (Clos with m >= n, Benes) assume the route is computed
each cycle; with greedy or fixed routing they block like a butterfly, which is a different
request and is written as explicit `stages:`.

A first stage built from several small crossbars sees LANES, not the window: its capacity
binds within each chunk of `lanes` consecutive accesses. The 32-bank request above, at
strides {1, 2, 16, 32, 64, 128, 256}, is proved impossible for any mapping in seconds: a
stride-s chunk fixes the group as a 4s-periodic function of the address, and a stride-t chunk
then collides whenever 2s divides t, which for powers of two is every pair. Each stride alone
is servable at 16 concurrent; no two are. The lever is the lane assignment, a second link per
4x4 output, or one stride per kernel, and the report says so instead of asking the model.

When the wiring is YOURS to choose, say so and the solver chooses it with the mapping:
`stages: [{bits: [3, 4], capacity: 1, lanes: 4, lane_key: free, blocks: 7}]` (seven free
4-input crossbars into the four groups) searches the lane-to-crossbar assignment jointly with
the XOR-fold and reports both -- the partition it chose is then the hardware every stage
checks against. On the 32-bank request this
settles the wiring axis: N=4 is SOLVED outright (a 4-XOR fold, each access on its own
crossbar); at N=8 and N=16 the linear family is unsat over EVERY wiring, with 6 and 7
concurrent as the measured caps -- the remaining frontier is non-linear mappings.

The lane assignment matters more than the mapping. The same request with lanes INTERLEAVED
(`{bits: [3, 4], capacity: 1, lanes: 7, lane_key: mod}`: access i on 4x4 number i mod 7) is not proved impossible; the linear
family is unsat for all seven strides at N = 16 and at N = 8, but 7 concurrent accesses across
all strides are served by a 6-XOR fold, strides {1, 2} at N = 16 by another, and {1, 2, 16} at
N = 8 by a third. That is the gap the model round is for, and the first case where the proof
stage stays silent rather than closing the door. The first model round did not fill it: sixteen
non-linear proposals (XOR-shift folds, odd-multiplier hashes), every one refused by the
exhaustive checker, the closest failing on 3% of start addresses -- reported as "not found",
not as impossible, because nothing proved it so.

Every stage understands stages. The checker counts the load on each resource per window; z3 encodes a
capacity-1 stage as pairwise "differ on these bits" and a capacity-c stage as "among any c+1 accesses
of a window, one pair differs"; the pigeonhole bound uses the tightest capacity-1 resource. First
findings: a `4x2` crossbar with single links cannot serve strides {8, 16} at N=4 for *any* mapping
(five addresses must occupy four groups, proved in 0.5 s), and with two links per group z3 finds a
stage-aware two-XOR fold in 3.4 s that the bank-only search's answer would have violated.

## Layout

`lib/src/flux_bankmap/`: `problem` (request/result), `mapping` (Modulo, XorFold, Expr — each
with cost, description and Verilog), `check` (the exhaustive checker), `impossible` (the
pigeonhole proof), `solve_z3` (CEGIS over folds), `propose` (the model's DSL and prompt), `topology` (the networks as stages), `steps` (the document's commands: the search, the check,
the cost). It runs as a document: `flux task run`; the decided mapping's Verilog is the run's
artifact (`out/bankmap.v`).
