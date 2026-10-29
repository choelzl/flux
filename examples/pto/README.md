# pto -- PTO's elementwise tile units, one pyCircuit datapath

The PTO ISA's elementwise tile units (TADD, TSUB, TMUL, TMAX, ... -- `fluxpreset pto ops`) as ONE
combinational datapath in pyCircuit, checked against the spec's own ASL model.

    ./fetch-spec                  # the spec into spec/, pinned (git-ignored), with tools/ndf
    flux task run applications/pto

Nothing about the spec is in `problem.yaml`; it is `spec/`, read by the presets:

| Step | Command | What it does |
|---|---|---|
| scope list | `fluxpreset pto ops --names` | the interface's units -- a pass works on one |
| brief | `fluxpreset ndf clauses UNIT` | the unit's NDF clauses, and the units depending on it |
| scope check | `fluxpreset pto check DESIGN --units UNIT --dependents` | the unit and its dependents against the spec |
| gate | `fluxpreset pto check DESIGN --build-only` | the design compiles (pyCircuit, C++ model) |
| measure | `fluxpreset pto check DESIGN --measure` | every unit: `vectors_failing`, `units_passing` |

The reference vectors are the spec's answers: per unit, special and seeded random values for each
data type of 8 bits or more, run through ASLRef once (one run per unit, ~10 s, more where the spec's
model asserts) and cached under `~/.cache/flux/pto-ref` by the spec's content. A design check is a
pyCircuit build and a C++ simulation (~10 s), no ASLRef.

`fluxpreset pto ref UNIT` also lists where the spec's own model asserts (TADD on TF32, E4M3, ... at
03eb383): those vectors are not compared, and are worth reporting upstream. `fluxpreset asl run
UNIT` runs a unit's AVS tests (the spec's, not the design's) with the spec's page runner.
