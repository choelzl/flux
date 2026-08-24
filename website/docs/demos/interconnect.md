# Interconnect

**28 clients of 128 bits must reach 32 banks concurrently; minimise fabric area subject to a
600 MHz floor -- on placed ASAP7 silicon.**

```bash
nix develop .#physical --command python3 applications/interconnect/demo.py
```

The search widens through fabric families -- direct, hierarchical, multistage, parallel-switch,
three-stage Clos, radix-R butterfly -- with the space *enumerated* from (clients, banks, width)
rather than listed. Screening a topology is instant and analytic; measuring one runs Yosys and
OpenROAD once per distinct selector arity and Verilator on the whole generated fabric under
uniform-random traffic. Nothing in the results table is an estimate.

## Correct, not just fast

Since the simulation checks the fabric rather than only timing it: each bank verifies its own
mail (the destination is derived from the payload), the payload carries its own complement so
dropped or crossed bits are caught, and the client id rides along, giving a per-client delivery
census that exposes starvation no aggregate number would show. A fabric that delivers to the
wrong bank raises -- it is broken, not slow. The negative control: one swapped entry in a
routing table produces thousands of misdelivered words and fails the measurement.

## The model's role

With `--llm-round N`, a local model proposes fabrics of its own, handed the problem, the repo's
own measured arity-to-frequency table, and everything measured so far. It proposes and nothing
else: every fabric it names is built by the real constructor, routed constructively, screened,
placed and simulated exactly like an enumerated candidate.

This is where hybrids come from -- the deterministic search widens only *within* families
someone wrote down, and mixing families is exactly the gap a proposer fills. In one measured
run, a proposed hybrid Clos-radix-crossbar fabric topped the table at 17.5 words/cycle, above
anything the 1,101-candidate enumeration reached -- at 2.7x the area, extending the frontier at
the throughput end rather than dominating it, which is the right kind of contribution from a
proposer whose output everything else then measures.

## Useful flags

`--rounds N` bounds the widening rounds, `--budget N` grants evaluations per round, `--db PATH`
resumes a campaign store, `--llm-round N` adds the model round.
