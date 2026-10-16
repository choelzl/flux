# Three loops of different shapes

Read the live files under `flux/applications/` before copying; these excerpts may lag them.

## 1. RTL with a golden model -- `flux/applications/mul8/`

```
mul8/  problem.yaml  golden.py  README.md  workbench/  out/
```

```yaml
statement: >-
  A combinational signed 8x8 -> 16-bit multiplier in SystemVerilog: module `mul8` with inputs
  `a` and `w` (`input logic signed [7:0]`) and output `p` (`output logic signed [15:0]`), the
  smallest that makes 1600 MHz placed on ASAP7.
contract: >-
  One module named exactly `mul8`, purely combinational, synthesizable SystemVerilog (logic,
  assign, always_comb; no $ system tasks, no initial blocks). p must equal a * w as signed
  integers for every input.
language: systemverilog
flow:
  test: "fluxpreset golden {artifact} --golden {home}/golden.py"
  measure:
    screen:
      command: "fluxpreset rtl measure {artifact} --stage synth --clock-ps 625"
      metrics: [fmax_mhz, area_um2, power_w, cell_count]
      needs: [yosys, openroad]
    confirm:
      command: "fluxpreset rtl measure {artifact} --stage place --clock-ps 625"
      metrics: [fmax_mhz, area_um2, power_w, cell_count]
      needs: [yosys, openroad]
  select: {finalists: 2}
  knowledge: off
objectives:
  - {metric: fmax_mhz, direction: maximize, goal: 1600}
  - {metric: area_um2, direction: minimize}
budget: {steps: 4, repair_attempts: 6, prototype: false}
```

Why it looks like this: presets do all the work (no scripts); the clock (625 ps) matches the
goal (1600 MHz); a small loop needs no knowledge; "smallest that makes X" = goal then minimize.

## 2. A script walks a design space -- `flux/applications/macarray/`

```
macarray/  problem.yaml  invent.problem.yaml  workload.yaml  lib/flux_macarray/steps.py  workbench/  out/invented/
```

```yaml
language: systemverilog
objectives:
  - {metric: fmax_mhz, direction: maximize, goal: 1000}
  - {metric: area_um2, direction: minimize}
flow:
  orchestrate:
    policy: sweep
    space:
      multiplier: {values: [behavioral, array, booth4, wallace], from: "out/invented/*.sv"}
      reducer: [tree, chain, csa]
      pipeline: [0, 1, 2, 3]
    seeds: [{multiplier: behavioral, reducer: tree, pipeline: 0}]
  generate: {command: "{python} -m flux_macarray.steps gen {artifact} {multiplier} {reducer} {pipeline} --invented {home}/out/invented"}
  test:
    verify: {run: "{python} -m flux_macarray.steps check {artifact} {pipeline}", timeout_s: 300}
  measure:
    screen: {command: "fluxpreset rtl measure {artifact} --stage synth --clock-ps 1000 --module mac_pe",
             metrics: [fmax_mhz, area_um2, power_w, cell_count], needs: [yosys, openroad]}
    confirm: {command: "fluxpreset rtl measure {artifact} --stage place --clock-ps 1000 --module mac_pe",
              metrics: [fmax_mhz, area_um2, power_w, cell_count], needs: [yosys, openroad], timeout_s: 1800}
  select: {finalists: 4}
budget: {prototype: false, batch: 48, workers: 0}
```

Why: 48 points is a sweep; `from:` lets a second loop (`invent.problem.yaml`, a model writing
new multipliers into `out/invented/`) grow the space; no model is needed to render a point.
For the smallest form of this pattern see `flux/applications/adder16` (one knob, `gen.py`).

## 3. Sub-loops composed into one design -- `flux/applications/nlu/`

```
nlu/  problem.yaml
      ops/{recip,rsqrt,sigmoid,tanh,gelu,log,exp}/{problem.yaml, golden.py}
      knowledge/{nlu-methods.md, fp16_blocks.py}
      workbench/notes/  out/
```

```yaml
subtasks: [ops/recip, ops/rsqrt, ops/sigmoid, ops/tanh, ops/gelu, ops/log, ops/exp]
language: systemverilog
objectives:
  - {metric: fmax_mhz, direction: maximize, goal: 800, margin: 0.03, tie: 0.03}
  - {metric: area_um2, direction: minimize}
  - {metric: power_w, direction: minimize}
flow:
  generate: claude
  test: "fluxpreset golden {artifact} --golden {home}/golden.py"   # {home}: each operator's folder
  measure:
    synth: {command: "fluxpreset rtl measure {artifact} --stage synth --clock-ps 1250 --timeout 1200",
            metrics: [fmax_mhz, area_um2, power_w, cell_count], needs: [yosys, openroad], timeout_s: 1300}
    place: {command: "fluxpreset rtl measure {artifact} --stage place --clock-ps 1250 --timeout 1800",
            metrics: [fmax_mhz, area_um2, power_w, cell_count], needs: [yosys, openroad], timeout_s: 1900}
  knowledge: {sheet: knowledge/nlu-methods.md, files: [knowledge/fp16_blocks.py]}
  select: {finalists: 3}
budget: {prototype: true, steps: 24, repair_attempts: 20, explore_every: 4, cooldown_after: 3,
         prototype_attempts: 30, prototype_patience: 8, prototype_attempts_max: 90}
```

A child, `ops/recip/problem.yaml`, holds only `statement` and `contract`; it inherits the rest.
Its `golden.py` fixes `SEED=1`, `COUNT=1000` and `TOLERANCE_ULP={"y":1}`.

Why: seven float operators share one flow, so the parent says it once; numeric RTL gets
`prototype: true`; hard operators get a coding agent, a method sheet and more repairs; `margin`
and `tie` absorb the gap between synth and placed numbers.

## Other patterns worth copying

- `flux/applications/bankmap` -- a search command: `orchestrate: {command: "{python} -m flux_bankmap.steps search {history} {state} {params}"}` plus `params:`.
- `flux/applications/prefetcher` -- `language: ini`, `knowledge.files`, and an objective with `keep: 0.9` (stay within 90 % of the best).
